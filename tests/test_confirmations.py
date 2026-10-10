from decimal import Decimal

from app.models import TransactionType
from app.schemas import RecordTransactionArgs
from app.services import assistant, customers
from app.services import transactions as ledger
from app.services.llm import ParsedMessage, ToolCall

SENDER = "whatsapp:+911234567890"


def payment(name="Meenakshi", amount="500"):
    return RecordTransactionArgs(customer=name, amount=Decimal(amount), type=TransactionType.payment)


def fake_llm(args):
    return lambda text: ParsedMessage(calls=[ToolCall("record_transaction", args)])


def test_unknown_customer_asks_and_saves_nothing(db, monkeypatch):
    monkeypatch.setattr(assistant, "parse_message", fake_llm(payment()))
    reply = assistant.reply_to_text(db, "Meenakshi paid 500", SENDER)
    assert "YES" in reply
    assert customers.get_customer_by_name(db, "Meenakshi") is None


def test_yes_creates_customer_and_records(db, monkeypatch):
    monkeypatch.setattr(assistant, "parse_message", fake_llm(payment()))
    assistant.reply_to_text(db, "Meenakshi paid 500", SENDER)
    reply = assistant.reply_to_text(db, "yes", SENDER)
    assert "Meenakshi paid" in reply
    assert ledger.get_balance_by_name(db, "Meenakshi").balance == Decimal("-500")


def test_no_cancels(db, monkeypatch):
    monkeypatch.setattr(assistant, "parse_message", fake_llm(payment()))
    assistant.reply_to_text(db, "Meenakshi paid 500", SENDER)
    reply = assistant.reply_to_text(db, "no", SENDER)
    assert "cancelled" in reply
    assert customers.get_customer_by_name(db, "Meenakshi") is None


def test_known_customer_saves_immediately(db, monkeypatch):
    ledger.add_transaction(db, "Ramesh", 500)
    monkeypatch.setattr(assistant, "parse_message", fake_llm(payment("Ramesh", "200")))
    reply = assistant.reply_to_text(db, "Ramesh paid 200", SENDER)
    assert "Total owed" in reply and "300.00" in reply