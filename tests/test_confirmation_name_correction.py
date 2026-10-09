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


def test_typing_corrected_name_replaces_pending(db, monkeypatch):
    monkeypatch.setattr(assistant, "parse_message", fake_llm(payment("L akshmi", "800")))
    first = assistant.reply_to_text(db, "L akshmi paid 800", SENDER)
    assert "L akshmi" in first

    second = assistant.reply_to_text(db, "Lakshmi", SENDER)
    assert ("Lakshmi " + chr(0x2014) + " payment " + chr(0x20b9) + "800.00") in second
    assert "YES" in second

    done = assistant.reply_to_text(db, "yes", SENDER)
    assert "Lakshmi paid" in done
    assert customers.get_customer_by_name(db, "L akshmi") is None


def test_corrected_name_matching_existing_customer_saves_directly(db, monkeypatch):
    ledger.add_transaction(db, "Ramesh", 500)
    monkeypatch.setattr(assistant, "parse_message", fake_llm(payment("Ramsh", "200")))
    assistant.reply_to_text(db, "Ramsh paid 200", SENDER)
    reply = assistant.reply_to_text(db, "Ramesh", SENDER)
    assert "Total owed:" in reply


def test_new_command_is_not_mistaken_for_a_name(db, monkeypatch):
    monkeypatch.setattr(assistant, "parse_message", fake_llm(payment()))
    assistant.reply_to_text(db, "Meenakshi paid 500", SENDER)

    monkeypatch.setattr(assistant, "parse_message", lambda text: ParsedMessage(reply="LLM was called"))
    reply = assistant.reply_to_text(db, "Show today's transactions", SENDER)
    assert reply == "LLM was called"
