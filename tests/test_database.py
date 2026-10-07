from decimal import Decimal

from app.models import TransactionType
from app.schemas import CustomerCreate, TransactionCreate
from app.services import customers, transactions


def test_credit_and_payment_balance(db):
    c = customers.create_customer(db, CustomerCreate(name="Ramesh"))
    transactions.create_transaction(
        db, TransactionCreate(customer_id=c.id, item="rice", amount=Decimal("500"), type=TransactionType.credit)
    )
    transactions.create_transaction(
        db, TransactionCreate(customer_id=c.id, amount=Decimal("200"), type=TransactionType.payment)
    )
    assert transactions.get_balance(db, c.id) == Decimal("300")


def test_find_customer_case_insensitive(db):
    customers.create_customer(db, CustomerCreate(name="Ramesh"))
    assert customers.get_customer_by_name(db, "  ramesh ") is not None
