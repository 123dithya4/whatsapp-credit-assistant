from decimal import Decimal

import pytest

from app.schemas import GetPaymentsArgs
from app.services import transactions as ledger
from app.services.actions import run_call
from app.schemas import NoArgs


def test_get_payments_totals(db):
    ledger.add_transaction(db, "Ramesh", 500)
    ledger.record_payment(db, "Ramesh", 200)
    ledger.record_payment(db, "Ramesh", 100)
    summary = ledger.get_payments(db, "ramesh")
    assert summary.total == Decimal("300")
    assert summary.count == 2


def test_get_payments_unknown_customer(db):
    with pytest.raises(ledger.CustomerNotFoundError):
        ledger.get_payments(db, "Nobody")


def test_payments_reply_when_none(db):
    ledger.add_transaction(db, "Ramesh", 500)
    reply = run_call(db, "get_payments", GetPaymentsArgs(customer="Ramesh"))
    assert "hasn't paid" in reply


def test_payments_reply_shows_total_and_remaining(db):
    ledger.add_transaction(db, "Ramesh", 500)
    ledger.record_payment(db, "Ramesh", 200)
    reply = run_call(db, "get_payments", GetPaymentsArgs(customer="Ramesh"))
    assert "₹200.00 in total" in reply
    assert "Still owes: ₹300.00" in reply

def test_top_payers(db):
    ledger.add_transaction(db, "Ramesh", 500)
    ledger.add_transaction(db, "Suresh", 500)
    ledger.record_payment(db, "Ramesh", 200)
    ledger.record_payment(db, "Suresh", 400)
    ledger.record_payment(db, "Suresh", 50)
    assert ledger.get_top_payers(db) == [("Suresh", Decimal("450")), ("Ramesh", Decimal("200"))]


def test_unsupported_request_gets_friendly_reply(db):
    reply = run_call(db, "unsupported_request", NoArgs())
    assert "can't help" in reply
