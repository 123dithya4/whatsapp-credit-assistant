import json
import logging
import re
import unicodedata
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import TransactionType
from app.schemas import RawTransactionArgs, RecordTransactionArgs
from app.services import confirmations, customers, speech
from app.services.entries import Clarification, resolve_entry
from app.services import transactions as ledger
from app.services.actions import run_call
from app.services.llm import LLMError, ParsedMessage, ToolCall, parse_message

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 1000

EMPTY_TEXT = "I didn't get any text. Please type your message or send a voice note."
TOO_LONG_TEXT = "That message is too long for me. Please send one or two entries at a time."
NOT_UNDERSTOOD_TEXT = (
    "Sorry, I didn't understand that. You can say things like:\n"
    "_[customer] took [amount] rupees of [item] on credit_\n"
    "_[customer] paid [amount]_\n"
    "_Who owes me the most?_\n\n"
    "Type *help* to see everything."
)

# Words that show a message is a command or question, not a bare customer name
_NOT_A_NAME = {
    "help", "hi", "hello", "start", "show", "today", "todays", "today's",
    "who", "how", "what", "much", "balance", "owe", "owes", "paid", "pay",
    "took", "credit", "total", "transactions",
    "aaj", "kya", "kitna", "kaun", "baaki", "diya",
}
_TYPE_WORDS = {"credit", "payment", "paid", "pay", "udhaar", "took"}
_AMOUNT_ONLY = re.compile(r"(?i)(?:₹|rs\.?|rupees?)?\s*\d[\d,]*(?:\.\d+)?\s*(?:₹|rs\.?|rupees?)?")


def _looks_like_name(text: str) -> bool:
    text = text.strip()
    words = text.split()
    if not 1 <= len(words) <= 3 or len(text) > 40:
        return False
    if any(w.lower().strip(".'-") in _NOT_A_NAME for w in words):
        return False
    return all(
        ch.isalpha() or ch in " .'-" or unicodedata.category(ch).startswith("M")
        for ch in text
    )


def _is_followup(text: str) -> bool:
    """Does this short message look like an answer to 'what was the amount?' etc.?"""
    t = text.strip()
    if _AMOUNT_ONLY.fullmatch(t):                  # "500", "₹500", "500 rupees"
        return True
    if t.lower().rstrip(".!") in _TYPE_WORDS:      # "credit" / "payment"
        return True
    return _looks_like_name(t)                     # a name, or spoken numbers like "five hundred"


def _confirmation_reason(db: Session, entry: RecordTransactionArgs) -> str | None:
    """Why this entry must be confirmed before saving (None = safe to save now)."""
    customer = customers.get_customer_by_name(db, entry.customer)
    if customer is None:
        return "new customer"
    if ledger.find_recent_duplicate(db, customer.id, entry.type, entry.amount, entry.item):
        return "same as an entry saved a moment ago"
    if entry.type == TransactionType.payment:
        owed = ledger.get_balance(db, customer.id)
        if owed <= 0:
            return "owes nothing right now"
        if entry.amount > owed:
            return f"owes only ₹{owed:,.2f}"
    return None


def _confirmation_prompt(items: list[tuple[RecordTransactionArgs, str]]) -> str:
    lines = []
    for entry, reason in items:
        line = f"• {entry.customer} — {entry.type.value} ₹{entry.amount:,.2f}"
        if entry.item:
            line += f" of {entry.item}"
        lines.append(f"{line} ({reason})")

    tail = "Save? Reply *YES* or *NO*."
    if len(items) == 1 and items[0][1] == "new customer":
        tail += "\n(Wrong name? Just type the correct name.)"
    return "⚠️ Please check before I save:\n" + "\n".join(lines) + "\n\n" + tail


def _save_or_ask(db: Session, sender: str, entry: RecordTransactionArgs) -> str:
    """Save right away if the entry is safe, otherwise ask for confirmation."""
    reason = _confirmation_reason(db, entry)
    if reason is None:
        return run_call(db, "record_transaction", entry)
    confirmations.save_pending(db, sender, [entry])
    return _confirmation_prompt([(entry, reason)])


def _handle_answer(db: Session, sender: str, text: str) -> str | None:
    """If an entry is waiting for confirmation, process YES / NO / a corrected name.
    Returns a reply, or None to continue as a normal message."""
    pending = confirmations.get_pending(db, sender)
    answer = text.strip().lower().rstrip(".!")

    if pending is None:
        if answer in confirmations.YES or answer in confirmations.NO:
            return ("There's nothing waiting for a yes/no right now. "
                    "Tell me an entry or ask a question, or type *help*.")
        return None

    confirmations.clear_pending(db, sender)

    if answer in confirmations.YES:
        replies = []
        for entry in pending:
            customers.add_customer(db, entry.customer)
            replies.append(run_call(db, "record_transaction", entry))
        return "\n".join(replies)

    if answer in confirmations.NO:
        return "Okay, cancelled. Nothing was saved."

    # One waiting entry + a bare name = the user is correcting the spelling
    if len(pending) == 1 and _looks_like_name(text):
        corrected = pending[0].model_copy(update={"customer": customers.normalize_name(text)})
        return _save_or_ask(db, sender, corrected)

    return None  # anything else: treat it as a brand-new message


def _save_clarification(db: Session, sender: str, clarification: Clarification, context_text: str) -> None:
    payload = json.dumps({
        "partial": clarification.partial.model_dump(mode="json"),
        "missing": clarification.missing,
        "context": context_text,
    })
    confirmations.save_clarification(db, sender, payload)


def _handle_followup(db: Session, sender: str, text: str) -> tuple[ParsedMessage | None, str]:
    """If the bot just asked a question, use the short reply to complete the entry in Python.
    Returns (result, context_text); result is None when the LLM should read context_text."""
    stored = confirmations.get_clarification(db, sender)
    if stored is None:
        return None, text
    confirmations.clear_clarification(db, sender)
    if not _is_followup(text):
        return None, text

    try:
        data = json.loads(stored)
        partial = RawTransactionArgs.model_validate(data["partial"])
        missing, context = data["missing"], data["context"]
    except (ValueError, KeyError):
        return None, text

    reply = text.strip()
    word = reply.lower().rstrip(".!")
    combined = f"{context}\n{reply}"

    if missing == "amount" and _AMOUNT_ONLY.fullmatch(reply):
        number = re.search(r"\d[\d,]*(?:\.\d+)?", reply).group()
        partial.amount = Decimal(number.replace(",", ""))
        partial.amount_quote = reply
    elif missing == "type" and word in _TYPE_WORDS:
        partial.type = "credit" if word in {"credit", "udhaar", "took"} else "payment"
    elif missing == "customer" and _looks_like_name(reply):
        partial.customer = reply
    else:
        # e.g. an amount said in words: let the LLM read the original message plus the reply
        return None, f"{context}\n(Reply to my question: {reply})"

    result = resolve_entry(partial, combined)
    if isinstance(result, Clarification):
        return ParsedMessage(clarifications=[result]), combined
    return ParsedMessage(calls=[ToolCall("record_transaction", result)]), combined


def reply_to_text(db: Session, text: str, sender: str) -> str:
    """Message text in -> reply text out. Never raises for LLM problems."""
    text = (text or "").strip()
    if not text:
        return EMPTY_TEXT
    if len(text) > MAX_MESSAGE_CHARS:
        return TOO_LONG_TEXT
    if not any(ch.isalnum() for ch in text):  # only emoji / punctuation
        return NOT_UNDERSTOOD_TEXT

    answered = _handle_answer(db, sender, text)
    if answered is not None:
        return answered

    parsed, llm_text = _handle_followup(db, sender, text)
    if parsed is None:
        try:
            parsed = parse_message(llm_text)
        except LLMError:
            logger.exception("LLM call failed")
            return "⚠️ Sorry, I'm having trouble right now. Please try again in a minute."

    replies: list[str] = []
    to_confirm: list[tuple[RecordTransactionArgs, str]] = []

    for call in parsed.calls:
        if call.name == "unsupported_request":
            logger.info("Unsupported request: %r", text)
        if call.name == "record_transaction":
            reason = _confirmation_reason(db, call.args)
            if reason:
                to_confirm.append((call.args, reason))
                continue
        replies.append(run_call(db, call.name, call.args))

    if to_confirm:
        confirmations.save_pending(db, sender, [entry for entry, _ in to_confirm])
        replies.append(_confirmation_prompt(to_confirm))

    replies.extend(c.question for c in parsed.clarifications)
    if len(parsed.clarifications) == 1 and not parsed.calls:
        # One incomplete entry: remember what we understood so a short reply can complete it
        _save_clarification(db, sender, parsed.clarifications[0], llm_text)

    for err in parsed.errors:
        logger.warning("Tool call rejected: %s", err)
    if parsed.errors:
        replies.append("⚠️ I couldn't understand part of that message. Please check and resend it.")

    if not replies:
        logger.info("No action for %r (LLM said: %r)", text, parsed.reply)
        replies = [NOT_UNDERSTOOD_TEXT]  # fixed text: free LLM text is never sent to users
    return "\n".join(replies)


def reply_to_voice(db: Session, audio_url: str, content_type: str, sender: str) -> str:
    """Voice note in -> reply text out. Same logic as typed text after transcription."""
    names = [c.name for c in customers.list_customers(db)[:30]]
    try:
        audio = speech.download_media(audio_url)
        transcript = speech.transcribe(audio, content_type, names)
    except speech.SpeechError:
        logger.exception("Voice note failed")
        return "⚠️ Sorry, I couldn't process that voice note. Please try again, or type your message."

    if len(transcript) < 3:
        return "🎤 I couldn't hear anything clear. Please try again, a little closer to the phone."

    reply = reply_to_text(db, transcript, sender)
    return f'🎤 Heard: "{transcript}"\n\n{reply}'
