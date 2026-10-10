from decimal import Decimal

from app.models import TransactionType
from app.schemas import RecordTransactionArgs
from app.services import assistant, customers
from app.services import transactions as ledger
from app.services.llm import ParsedMessage, ToolCall

from app.schemas import RawTransactionArgs
from app.services.entries import Clarification

SENDER = "whatsapp:+911234567890"


def entry(name, amount, type_=TransactionType.credit, item=None):
    return RecordTransactionArgs(customer=name, amount=Decimal(str(amount)), type=type_, item=item)


def fake_llm(*entries):
    return lambda text: ParsedMessage(calls=[ToolCall("record_transaction", e) for e in entries])


def test_duplicate_entry_asks_before_saving(db, monkeypatch):
    ledger.add_transaction(db, "Ramesh", 500, item="rice")
    monkeypatch.setattr(assistant, "parse_message", fake_llm(entry("Ramesh", 500, item="rice")))
    reply = assistant.reply_to_text(db, "Ramesh took 500 rupees of rice", SENDER)
    assert "same as an entry saved" in reply
    assert ledger.get_balance_by_name(db, "Ramesh").balance == Decimal("500")
    assert "1,000.00" in assistant.reply_to_text(db, "yes", SENDER)


def test_overpayment_asks_before_saving(db, monkeypatch):
    ledger.add_transaction(db, "Ramesh", 300)
    monkeypatch.setattr(assistant, "parse_message", fake_llm(entry("Ramesh", 500, TransactionType.payment)))
    reply = assistant.reply_to_text(db, "Ramesh paid 500", SENDER)
    assert "owes only" in reply and "300.00" in reply
    assert ledger.get_balance_by_name(db, "Ramesh").balance == Decimal("300")
    assistant.reply_to_text(db, "yes", SENDER)
    assert ledger.get_balance_by_name(db, "Ramesh").balance == Decimal("-200")


def test_exact_payment_saves_directly(db, monkeypatch):
    ledger.add_transaction(db, "Ramesh", 300)
    monkeypatch.setattr(assistant, "parse_message", fake_llm(entry("Ramesh", 300, TransactionType.payment)))
    reply = assistant.reply_to_text(db, "Ramesh paid 300", SENDER)
    assert "Ramesh paid" in reply and "YES" not in reply


def make_question(missing="amount", **partial):
    return Clarification("What was the amount for Ramesh's rice purchase?",
                         RawTransactionArgs(**partial), missing)


def test_amount_followup_is_filled_without_the_llm(db, monkeypatch):
    customers.add_customer(db, "Ramesh")
    calls = []

    def llm(text):
        calls.append(text)
        return ParsedMessage(clarifications=[make_question(customer="Ramesh", type="credit", item="rice")])

    monkeypatch.setattr(assistant, "parse_message", llm)
    first = assistant.reply_to_text(db, "Ramesh took rice", SENDER)
    assert "What was the amount" in first
    second = assistant.reply_to_text(db, "500", SENDER)
    assert len(calls) == 1  # the reply was handled in Python, not by the LLM
    assert "500.00" in second
    assert ledger.get_balance_by_name(db, "Ramesh").balance == Decimal("500")


def test_type_followup(db, monkeypatch):
    customers.add_customer(db, "Ramesh")
    q = make_question("type", customer="Ramesh", amount=Decimal("500"), amount_quote="500")
    monkeypatch.setattr(assistant, "parse_message", lambda text: ParsedMessage(clarifications=[q]))
    assistant.reply_to_text(db, "Ramesh 500", SENDER)
    reply = assistant.reply_to_text(db, "credit", SENDER)
    assert "Total owed" in reply and "500.00" in reply


def test_followup_in_words_falls_back_to_llm(db, monkeypatch):
    seen = []

    def llm(text):
        seen.append(text)
        return ParsedMessage(clarifications=[make_question(customer="Ramesh", type="credit", item="rice")])

    monkeypatch.setattr(assistant, "parse_message", llm)
    assistant.reply_to_text(db, "Ramesh took rice", SENDER)
    assistant.reply_to_text(db, "five hundred", SENDER)
    assert "Ramesh took rice" in seen[1] and "five hundred" in seen[1]


def test_fresh_message_is_not_merged_into_old_question(db, monkeypatch):
    seen = []

    def llm(text):
        seen.append(text)
        if len(seen) == 1:
            return ParsedMessage(clarifications=[make_question(customer="Ramesh", type="credit", item="rice")])
        return ParsedMessage()

    monkeypatch.setattr(assistant, "parse_message", llm)
    assistant.reply_to_text(db, "Ramesh took rice", SENDER)
    assistant.reply_to_text(db, "Who owes me the most?", SENDER)
    assert seen[1] == "Who owes me the most?"

    
def test_empty_and_junk_messages_never_reach_the_llm(db, monkeypatch):
    def boom(text):
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(assistant, "parse_message", boom)
    assert assistant.reply_to_text(db, "   ", SENDER) == assistant.EMPTY_TEXT
    assert assistant.reply_to_text(db, "!!!", SENDER) == assistant.NOT_UNDERSTOOD_TEXT
    assert assistant.reply_to_text(db, "x" * 2000, SENDER) == assistant.TOO_LONG_TEXT


def test_no_tool_call_gives_fixed_friendly_text(db, monkeypatch):
    monkeypatch.setattr(
        assistant, "parse_message", lambda text: ParsedMessage(reply="The capital of France is Paris")
    )
    assert assistant.reply_to_text(db, "capital of France?", SENDER) == assistant.NOT_UNDERSTOOD_TEXT