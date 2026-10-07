from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services import customers
from app.services.transactions import (
    CustomerNotFoundError,
    InvalidAmountError,
    add_transaction,
    get_balance_by_name,
    get_today_transactions,
    get_top_debtors,
    record_payment,
)


def test_add_transaction_creates_customer(db):
    result = add_transaction(db, "Ramesh", 500, item="rice")
    assert result.customer.name == "Ramesh"
    assert result.balance == Decimal("500")
    assert result.transaction.item == "rice"


def test_name_matching_is_forgiving(db):
    add_transaction(db, "Ramesh", 500)
    add_transaction(db, "  ramesh ", 100)  # same person
    assert len(customers.list_customers(db)) == 1
    assert get_balance_by_name(db, "RAMESH").balance == Decimal("600")


def test_record_payment_reduces_balance(db):
    add_transaction(db, "Ramesh", 500)
    result = record_payment(db, "Ramesh", 200)
    assert result.balance == Decimal("300")


def test_payment_for_unknown_customer_fails(db):
    with pytest.raises(CustomerNotFoundError):
        record_payment(db, "Nobody", 100)


@pytest.mark.parametrize("bad", [0, -5, "abc", "0.001", 10_000_000])
def test_invalid_amounts_rejected(db, bad):
    with pytest.raises(InvalidAmountError):
        add_transaction(db, "Ramesh", bad)
    assert customers.list_customers(db) == []  # nothing was created


def test_top_debtors_ordering_and_filtering(db):
    add_transaction(db, "Ramesh", 500)
    add_transaction(db, "Suresh", 300)
    add_transaction(db, "Anil", 200)
    record_payment(db, "Anil", 200)  # settled -> excluded
    add_transaction(db, "Mohan", 100)
    record_payment(db, "Mohan", 300)  # overpaid -> excluded

    top = get_top_debtors(db)
    assert [d.name for d in top] == ["Ramesh", "Suresh"]
    assert top[0].balance == Decimal("500")


def test_today_transactions_excludes_old_entries(db):
    add_transaction(db, "Ramesh", 500)
    old = add_transaction(db, "Ramesh", 100).transaction
    old.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    db.commit()

    today = get_today_transactions(db)
    assert len(today) == 1
    assert today[0].amount == Decimal("500")
