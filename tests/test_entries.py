from decimal import Decimal

from app.models import TransactionType
from app.schemas import RawTransactionArgs, RecordTransactionArgs
from app.services.entries import amount_is_grounded, resolve_entry


def raw(**kw):
    return RawTransactionArgs(**kw)


def test_missing_amount_asks_a_specific_question():
    q = resolve_entry(raw(customer="Ramesh", item="rice", type="credit"), "Ramesh took rice")
    assert q.question == "What was the amount for Ramesh's rice purchase?"


def test_invented_amount_is_rejected():
    q = resolve_entry(
        raw(customer="Ramesh", item="rice", type="credit", amount=Decimal("500"), amount_quote="500"),
        "Ramesh took rice",
    )
    assert "amount" in q.question


def test_amount_must_match_its_quote():
    assert not amount_is_grounded(Decimal("5000"), "500", "Ramesh took 500 rupees of rice")
    assert amount_is_grounded(Decimal("500"), "500", "Ramesh took 500 rupees of rice")


def test_spoken_amount_is_accepted():
    assert amount_is_grounded(Decimal("500"), "five hundred", "Ramesh took five hundred rupees of rice")


def test_unclear_type_asks():
    q = resolve_entry(raw(customer="Ramesh", amount=Decimal("500"), amount_quote="500"), "Ramesh 500")
    assert "credit" in q.question and "pay" in q.question

def test_missing_customer_asks():
    q = resolve_entry(raw(amount=Decimal("500"), amount_quote="500", type="credit"), "took 500 of rice")
    assert "Which customer" in q.question


def test_complete_entry_passes():
    entry = resolve_entry(
        raw(customer=" ramesh  ", amount=Decimal("500"), amount_quote="500", type="credit", item="rice"),
        "ramesh took 500 rupees of rice",
    )
    assert isinstance(entry, RecordTransactionArgs)
    assert entry.customer == "ramesh" and entry.type == TransactionType.credit