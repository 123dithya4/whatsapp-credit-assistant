import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.schemas import GetBalanceArgs, GetTopDebtorsArgs, NoArgs, RawTransactionArgs, RecordTransactionArgs
from app.services import assistant, customers, speech
from app.services import transactions as ledger
from app.services.entries import resolve_entry
from app.services.llm import ParsedMessage, ToolCall

SENDER = "whatsapp:+911234567890"
_sid = count(1)


# ---------- helpers ----------
@pytest.fixture
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(settings, "twilio_validate_signature", False)
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "allowed_senders", "")
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def llm(monkeypatch):
    """Scripted stand-in for the model: message text -> raw tool calls."""
    scripts, seen = {}, []

    def parse(text):
        seen.append(text)
        parsed = ParsedMessage()
        for name, args in scripts.get(text, []):
            if name == "record_transaction":
                result = resolve_entry(args, text)  # the real validation runs
                if isinstance(result, RecordTransactionArgs):
                    parsed.calls.append(ToolCall(name, result))
                else:
                    parsed.clarifications.append(result)
            else:
                parsed.calls.append(ToolCall(name, args))
        return parsed

    monkeypatch.setattr(assistant, "parse_message", parse)
    return SimpleNamespace(on=lambda text, *calls: scripts.__setitem__(text, list(calls)), seen=seen)


def raw(customer=None, amount=None, type_=None, item=None, quote=None):
    return RawTransactionArgs(
        customer=customer,
        amount=Decimal(str(amount)) if amount is not None else None,
        amount_quote=quote if quote is not None else (str(amount) if amount is not None else None),
        type=type_,
        item=item,
    )


def credit(name, amount, item=None):
    return ("record_transaction", raw(name, amount, "credit", item))


def payment(name, amount):
    return ("record_transaction", raw(name, amount, "payment"))


def send(client, body, sid=None, **extra):
    """Post a WhatsApp message to the webhook; return the reply text ('' if none)."""
    data = {"Body": body, "From": SENDER, "NumMedia": "0",
            "MessageSid": sid or f"SM{next(_sid):032d}", **extra}
    r = client.post("/whatsapp/webhook", data=data)
    assert r.status_code == 200
    return "\n".join(m.text or "" for m in ET.fromstring(r.content).findall("Message"))


def balance(db, name):
    return ledger.get_balance_by_name(db, name).balance


# ---------- 1-6: ledger operations ----------
def test_01_credit_transaction(client, db, llm):
    text = "Ramesh took 500 rupees of rice on credit"
    llm.on(text, credit("Ramesh", 500, "rice"))
    ask = send(client, text)
    assert "new customer" in ask and "YES" in ask
    done = send(client, "yes")
    assert "Total owed" in done and "500.00" in done
    assert balance(db, "Ramesh") == Decimal("500")


def test_02_payment(client, db, llm):
    ledger.add_transaction(db, "Ramesh", 500)
    llm.on("Ramesh paid 200", payment("Ramesh", 200))
    reply = send(client, "Ramesh paid 200")
    assert "Total owed" in reply and "300.00" in reply


def test_03_multiple_transactions_for_one_customer(client, db, llm):
    customer, _ = customers.add_customer(db, "Ramesh")
    for text, call in [
        ("Ramesh took 500 rupees of rice on credit", credit("Ramesh", 500, "rice")),
        ("Ramesh took 300 rupees of sugar on credit", credit("Ramesh", 300, "sugar")),
        ("Ramesh paid 200", payment("Ramesh", 200)),
    ]:
        llm.on(text, call)
        assert "Total owed" in send(client, text)
    assert balance(db, "Ramesh") == Decimal("600")  # 500 + 300 - 200
    assert len(ledger.list_transactions(db, customer.id)) == 3


def test_04_customer_balance(client, db, llm):
    ledger.add_transaction(db, "Ramesh", 600)
    text = "How much does Ramesh owe?"
    llm.on(text, ("get_balance", GetBalanceArgs(customer="Ramesh")))
    assert "600.00" in send(client, text)


def test_05_top_debtor(client, db, llm):
    ledger.add_transaction(db, "Ramesh", 600)
    ledger.add_transaction(db, "Suresh", 150)
    ledger.add_transaction(db, "Anil", 100)
    ledger.record_payment(db, "Anil", 100)  # settled: must not appear
    text = "Who owes me the most?"
    llm.on(text, ("get_top_debtors", GetTopDebtorsArgs()))
    reply = send(client, text)
    assert reply.index("Ramesh") < reply.index("Suresh")
    assert "Anil" not in reply


def test_06_todays_transactions(client, db, llm):
    ledger.add_transaction(db, "Ramesh", 500)
    ledger.add_transaction(db, "Suresh", 300)
    old = ledger.add_transaction(db, "Anil", 100).transaction
    old.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    db.commit()
    text = "Show today's transactions"
    llm.on(text, ("get_today_transactions", NoArgs()))
    reply = send(client, text)
    assert "Ramesh" in reply and "Suresh" in reply and "Anil" not in reply
    assert "800.00" in reply


# ---------- 7: voice ----------
def test_07_voice_message(client, db, llm, monkeypatch):
    customers.add_customer(db, "Ramesh")
    spoken = "Ramesh took five hundred rupees of rice on credit"
    llm.on(spoken, ("record_transaction", raw("Ramesh", 500, "credit", "rice", quote="five hundred")))
    monkeypatch.setattr(speech, "download_media", lambda url: b"audio")
    monkeypatch.setattr(speech, "transcribe", lambda audio, ctype, names=(): spoken)
    reply = send(client, "", NumMedia="1", MediaUrl0="https://api.twilio.com/m", MediaContentType0="audio/ogg")
    assert f'Heard: "{spoken}"' in reply
    assert "Total owed" in reply and "500.00" in reply


def test_07b_voice_download_failure(client, monkeypatch):
    def boom(url):
        raise speech.SpeechError("nope")

    monkeypatch.setattr(speech, "download_media", boom)
    reply = send(client, "", NumMedia="1", MediaUrl0="https://api.twilio.com/m", MediaContentType0="audio/ogg")
    assert "couldn't process" in reply


def test_07c_non_audio_media(client):
    reply = send(client, "", NumMedia="1", MediaUrl0="https://api.twilio.com/m", MediaContentType0="image/jpeg")
    assert "voice notes" in reply


# ---------- 8: missing amount ----------
def test_08_missing_amount_then_short_answer(client, db, llm):
    customer, _ = customers.add_customer(db, "Ramesh")
    text = "Ramesh took rice"
    llm.on(text, ("record_transaction", raw("Ramesh", None, "credit", "rice")))
    ask = send(client, text)
    assert ask == "What was the amount for Ramesh's rice purchase?"
    assert ledger.list_transactions(db, customer.id) == []  # nothing saved yet
    done = send(client, "500")
    assert "Total owed" in done and "500.00" in done
    assert len(llm.seen) == 1  # the short answer was completed in Python, not by the LLM


def test_08b_invented_amount_is_not_saved(client, db, llm):
    customer, _ = customers.add_customer(db, "Ramesh")
    text = "Ramesh took rice"
    # the model "finds" an amount the user never said
    llm.on(text, ("record_transaction", raw("Ramesh", 500, "credit", "rice")))
    assert "What was the amount" in send(client, text)
    assert ledger.list_transactions(db, customer.id) == []


# ---------- 9: unknown customer ----------
def test_09_unknown_customer_asks_then_cancels(client, db, llm):
    llm.on("Meenakshi paid 500", payment("Meenakshi", 500))
    ask = send(client, "Meenakshi paid 500")
    assert "new customer" in ask
    assert customers.get_customer_by_name(db, "Meenakshi") is None
    assert "cancelled" in send(client, "no")
    assert customers.get_customer_by_name(db, "Meenakshi") is None


def test_09b_balance_of_unknown_customer(client, llm):
    text = "How much does Nobody owe?"
    llm.on(text, ("get_balance", GetBalanceArgs(customer="Nobody")))
    assert "No customer named" in send(client, text)


# ---------- 10: invalid input ----------
def test_10_invalid_input(client, db, llm):
    assert "didn't get any text" in send(client, "")
    assert "didn't understand" in send(client, "???")
    assert "too long" in send(client, "x" * 2000)

    llm.on("asdf qwer")  # the model returns no tool call
    assert "didn't understand" in send(client, "asdf qwer")

    llm.on("what is the weather today?", ("unsupported_request", NoArgs()))
    assert "can't help" in send(client, "what is the weather today?")


def test_10b_bad_amounts_are_never_saved(client, db, llm):
    customer, _ = customers.add_customer(db, "Ramesh")

    negative = "Ramesh took -50 rupees of rice"
    llm.on(negative, credit("Ramesh", -50, "rice"))
    assert "What was the amount" in send(client, negative)

    huge = "Ramesh took 99999999 rupees of rice"
    llm.on(huge, credit("Ramesh", 99999999, "rice"))
    assert "above the allowed limit" in send(client, huge)

    assert ledger.list_transactions(db, customer.id) == []


# ---------- 11: duplicate messages ----------
def test_11_same_entry_sent_twice_asks_first(client, db, llm):
    customers.add_customer(db, "Ramesh")
    text = "Ramesh took 500 rupees of rice on credit"
    llm.on(text, credit("Ramesh", 500, "rice"))

    assert "Total owed" in send(client, text)
    ask = send(client, text)  # sent again by mistake
    assert "same as an entry saved" in ask
    assert balance(db, "Ramesh") == Decimal("500")

    assert "cancelled" in send(client, "no")
    assert balance(db, "Ramesh") == Decimal("500")

    send(client, text)
    send(client, "yes")  # this time it really was a second sale
    assert balance(db, "Ramesh") == Decimal("1000")


def test_11b_twilio_retry_is_ignored(client, db, llm):
    customers.add_customer(db, "Ramesh")
    text = "Ramesh took 500 rupees of rice on credit"
    llm.on(text, credit("Ramesh", 500, "rice"))
    sid = "SMretry00000000000000000000000001"

    first = send(client, text, sid=sid)
    second = send(client, text, sid=sid)  # Twilio delivered the same message again
    assert "Total owed" in first
    assert second == ""
    assert balance(db, "Ramesh") == Decimal("500")