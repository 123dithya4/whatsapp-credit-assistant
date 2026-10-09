from decimal import Decimal

from app.models import TransactionType
from app.schemas import GetBalanceArgs, RecordTransactionArgs
from app.services.actions import run_call


def test_credit_then_payment_flow(db):
    credit = RecordTransactionArgs(
        customer="Ramesh", amount=Decimal("500"), type=TransactionType.credit, item="rice"
    )
    assert "Total owed: ₹500.00" in run_call(db, "record_transaction", credit)

    pay = RecordTransactionArgs(customer="Ramesh", amount=Decimal("200"), type=TransactionType.payment)
    assert "Total owed: ₹300.00" in run_call(db, "record_transaction", pay)

    assert "₹300.00" in run_call(db, "get_balance", GetBalanceArgs(customer="ramesh"))


def test_payment_for_unknown_customer_returns_warning(db):
    pay = RecordTransactionArgs(customer="Nobody", amount=Decimal("100"), type=TransactionType.payment)
    assert run_call(db, "record_transaction", pay).startswith("⚠️")
