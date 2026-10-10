import logging
import unicodedata

from sqlalchemy.orm import Session

from app.schemas import RecordTransactionArgs
from app.services import confirmations, customers, speech
from app.services.actions import run_call
from app.services.llm import LLMError, parse_message

logger = logging.getLogger(__name__)

# Words that show a message is a command or question, not a bare customer name
_NOT_A_NAME = {
    "help", "hi", "hello", "start", "show", "today", "todays", "today's",
    "who", "how", "what", "much", "balance", "owe", "owes", "paid", "pay",
    "took", "credit", "total", "transactions",
    "aaj", "kya", "kitna", "kaun", "baaki", "diya",
}


def _looks_like_name(text: str) -> bool:
    text = text.strip()
    words = text.split()
    if not 1 <= len(words) <= 3 or len(text) > 40:
        return False
    if any(word.lower().strip(".'-") in _NOT_A_NAME for word in words):
        return False
    # Letters in any script, combining marks, spaces, periods, apostrophes, and hyphens.
    return all(
        char.isalpha() or char in " .'-" or unicodedata.category(char).startswith("M")
        for char in text
    )


def _confirmation_prompt(entries: list[RecordTransactionArgs]) -> str:
    lines = "\n".join(
        f"\u2022 {entry.customer} \u2014 {entry.type.value} \u20b9{entry.amount:,.2f}"
        + (f" ({entry.item})" if entry.item else "")
        for entry in entries
    )
    return (
        "\U0001F195 New customer, not in your book yet:\n"
        f"{lines}\n\n"
        "Add and save? Reply *YES* or *NO*.\n"
        "(Wrong name? Just type the correct name.)"
    )


def _save_or_ask(db: Session, sender: str, entry: RecordTransactionArgs) -> str:
    """Save right away for a known customer, otherwise ask for confirmation."""
    if customers.get_customer_by_name(db, entry.customer):
        return run_call(db, "record_transaction", entry)
    confirmations.save_pending(db, sender, [entry])
    return _confirmation_prompt([entry])


def _handle_answer(db: Session, sender: str, text: str) -> str | None:
    """Handle YES / NO / a corrected name for a pending entry, if present."""
    pending = confirmations.get_pending(db, sender)
    if pending is None:
        return None

    answer = text.strip().lower().rstrip(".!")
    confirmations.clear_pending(db, sender)

    if answer in confirmations.YES:
        replies = []
        for entry in pending:
            customers.add_customer(db, entry.customer)
            replies.append(run_call(db, "record_transaction", entry))
        return "\n".join(replies)

    if answer in confirmations.NO:
        return "Okay, cancelled. Nothing was saved."

    # One waiting entry + a bare name means the user is correcting the spelling.
    if len(pending) == 1 and _looks_like_name(text):
        corrected = pending[0].model_copy(update={"customer": customers.normalize_name(text)})
        return _save_or_ask(db, sender, corrected)

    return None  # anything else: treat it as a brand-new message


def reply_to_text(db: Session, text: str, sender: str) -> str:
    """Message text in -> reply text out. Never raises for LLM problems."""
    answered = _handle_answer(db, sender, text)
    if answered is not None:
        return answered

    try:
        parsed = parse_message(text)
    except LLMError:
        logger.exception("LLM call failed")
        return "âš ï¸ Sorry, I'm having trouble right now. Please try again in a minute."

    replies: list[str] = []
    needs_confirmation: list[RecordTransactionArgs] = []

    for call in parsed.calls:
        is_entry = call.name == "record_transaction"
        if is_entry and customers.get_customer_by_name(db, call.args.customer) is None:
            needs_confirmation.append(call.args)
        else:
            replies.append(run_call(db, call.name, call.args))

    if needs_confirmation:
        confirmations.save_pending(db, sender, needs_confirmation)
        replies.append(_confirmation_prompt(needs_confirmation))

    for err in parsed.errors:
        logger.warning("Tool call rejected: %s", err)
    if parsed.errors:
        replies.append("âš ï¸ I couldn't understand part of that message. Please check and resend it.")
    if not replies:
        replies = [parsed.reply or "Sorry, I didn't understand that. Please try again."]
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
