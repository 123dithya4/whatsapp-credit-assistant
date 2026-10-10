from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Customer, Transaction, TransactionType
from app.schemas import BalanceRead, TransactionCreate
from app.services import customers

# Business settings (these move to config.py later)
LOCAL_TZ = ZoneInfo("Asia/Kolkata")
MAX_AMOUNT = Decimal("1000000")  # sanity cap: catches speech-to-text mishearings
CENT = Decimal("0.01")
DUPLICATE_WINDOW = timedelta(minutes=3)


# ---------- errors ----------
class LedgerError(Exception):
    """Base class for business-rule failures."""


class CustomerNotFoundError(LedgerError):
    pass


class InvalidAmountError(LedgerError):
    pass


# ---------- result type ----------
@dataclass
class LedgerResult:
    transaction: Transaction
    customer: Customer
    balance: Decimal  # what the customer owes AFTER this entry


# ---------- helpers ----------
def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _validate_amount(amount) -> Decimal:
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        raise InvalidAmountError(f"'{amount}' is not a valid amount")
    if not value.is_finite():
        raise InvalidAmountError("Amount must be a finite number")
    value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if value <= 0:
        raise InvalidAmountError("Amount must be greater than 0")
    if value > MAX_AMOUNT:
        raise InvalidAmountError(f"Amount is above the allowed limit of {MAX_AMOUNT}")
    return value


def _signed_amount():
    """SQL expression: +amount for credit, -amount for payment."""
    return case(
        (Transaction.type == TransactionType.credit, Transaction.amount),
        else_=-Transaction.amount,
    )


def _record(db: Session, customer: Customer, type_: TransactionType,
            amount: Decimal, item: str | None) -> LedgerResult:
    txn = Transaction(
        customer_id=customer.id,
        item=item.strip() if item and item.strip() else None,
        amount=amount,
        type=type_,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return LedgerResult(transaction=txn, customer=customer, balance=get_balance(db, customer.id))


# ---------- core business functions (the future LLM "tools") ----------
def add_transaction(db: Session, customer_name: str, amount, item: str | None = None) -> LedgerResult:
    """Customer took goods on credit. Creates the customer if new."""
    value = _validate_amount(amount)
    customer, _ = customers.add_customer(db, customer_name)
    return _record(db, customer, TransactionType.credit, value, item)


def record_payment(db: Session, customer_name: str, amount) -> LedgerResult:
    """Customer paid money back. The customer must already exist."""
    value = _validate_amount(amount)
    customer = customers.get_customer_by_name(db, customer_name)
    if customer is None:
        raise CustomerNotFoundError(f"No customer named '{customer_name}'")
    return _record(db, customer, TransactionType.payment, value, None)


def get_balance(db: Session, customer_id: int) -> Decimal:
    """Amount the customer owes. Negative means they overpaid."""
    total = db.scalar(
        select(func.coalesce(func.sum(_signed_amount()), 0))
        .where(Transaction.customer_id == customer_id)
    )
    return _money(total)


def get_balance_by_name(db: Session, name: str) -> BalanceRead:
    customer = customers.get_customer_by_name(db, name)
    if customer is None:
        raise CustomerNotFoundError(f"No customer named '{name}'")
    return BalanceRead(
        customer_id=customer.id, name=customer.name, balance=get_balance(db, customer.id)
    )


@dataclass
class PaymentSummary:
    customer: Customer
    total: Decimal
    count: int
    recent: list[Transaction]


def get_payments(db: Session, customer_name: str, limit: int = 5) -> PaymentSummary:
    """How much a customer has paid back in total, plus their latest payments."""
    customer = customers.get_customer_by_name(db, customer_name)
    if customer is None:
        raise CustomerNotFoundError(f"No customer named '{customer_name}'")

    conditions = (
        Transaction.customer_id == customer.id,
        Transaction.type == TransactionType.payment,
    )
    total = db.scalar(select(func.coalesce(func.sum(Transaction.amount), 0)).where(*conditions))
    count = db.scalar(select(func.count(Transaction.id)).where(*conditions))
    recent = list(
        db.scalars(
            select(Transaction).where(*conditions)
            .order_by(Transaction.created_at.desc()).limit(limit)
        )
    )
    return PaymentSummary(customer=customer, total=_money(total), count=count, recent=recent)


def get_top_debtors(db: Session, limit: int = 5) -> list[BalanceRead]:
    """Customers who owe money, biggest debt first. Zero/negative balances excluded."""
    owed = func.sum(_signed_amount())
    stmt = (
        select(Customer.id, Customer.name, owed)
        .join(Transaction, Transaction.customer_id == Customer.id)
        .group_by(Customer.id, Customer.name)
        .having(owed > 0.005)
        .order_by(owed.desc())
        .limit(limit)
    )
    return [
        BalanceRead(customer_id=cid, name=name, balance=_money(total))
        for cid, name, total in db.execute(stmt)
    ]

def get_top_payers(db: Session, limit: int = 5) -> list[tuple[str, Decimal]]:
    """Customers who have paid back the most in total, biggest first."""
    total = func.sum(Transaction.amount)
    stmt = (
        select(Customer.name, total)
        .join(Transaction, Transaction.customer_id == Customer.id)
        .where(Transaction.type == TransactionType.payment)
        .group_by(Customer.id, Customer.name)
        .order_by(total.desc())
        .limit(limit)
    )
    return [(name, _money(amount)) for name, amount in db.execute(stmt)]

def find_recent_duplicate(db: Session, customer_id: int, type_: TransactionType,
                          amount, item: str | None) -> Transaction | None:
    """An identical entry saved within the last few minutes (probably a double send)."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - DUPLICATE_WINDOW
    stmt = (
        select(Transaction)
        .where(
            Transaction.customer_id == customer_id,
            Transaction.type == type_,
            Transaction.created_at >= cutoff,
        )
        .order_by(Transaction.created_at.desc())
    )
    wanted_item = (item or "").strip().lower()
    for txn in db.scalars(stmt):
        if _money(txn.amount) == _money(amount) and (txn.item or "").lower() == wanted_item:
            return txn
    return None

def get_today_transactions(db: Session) -> list[Transaction]:
    """Transactions since midnight in the shop's local time (IST), newest first."""
    start_local = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    # created_at is stored as UTC, so convert the day's bounds to UTC
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end = end_local.astimezone(timezone.utc).replace(tzinfo=None)

    stmt = (
        select(Transaction)
        .where(Transaction.created_at >= start, Transaction.created_at < end)
        .order_by(Transaction.created_at.desc())
    )
    return list(db.scalars(stmt))


# ---------- plain CRUD used by the REST routes (unchanged from Phase 3) ----------
def create_transaction(db: Session, data: TransactionCreate) -> Transaction:
    txn = Transaction(**data.model_dump())
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def list_transactions(db: Session, customer_id: int) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(Transaction.customer_id == customer_id)
        .order_by(Transaction.created_at)
    )
    return list(db.scalars(stmt))


def list_all_transactions(db: Session, customer_id: int | None = None) -> list[Transaction]:
    stmt = select(Transaction).order_by(Transaction.created_at.desc())
    if customer_id is not None:
        stmt = stmt.where(Transaction.customer_id == customer_id)
    return list(db.scalars(stmt))


def delete_transaction(db: Session, transaction_id: int) -> bool:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        return False
    db.delete(txn)
    db.commit()
    return True

@dataclass
class OverdueBalance:
    customer_id: int
    name: str
    balance: Decimal
    oldest_unpaid: datetime
    days: int


def _oldest_unpaid(txns: list[Transaction]) -> datetime | None:
    """Payments are applied to the oldest credits first. Returns when the oldest
    credit that is still (partly) unpaid was given."""
    paid = sum((t.amount for t in txns if t.type == TransactionType.payment), Decimal("0"))
    for t in txns:  # oldest first
        if t.type != TransactionType.credit:
            continue
        if paid >= t.amount:
            paid -= t.amount
        else:
            return t.created_at
    return None


def get_overdue(db: Session, min_days: int = 7, now: datetime | None = None) -> list[OverdueBalance]:
    """Customers who still owe money and whose oldest unpaid credit is at least min_days old."""
    now = now or datetime.now(timezone.utc)
    result = []
    for customer in customers.list_customers(db):
        balance = get_balance(db, customer.id)
        if balance <= 0:
            continue
        since = _oldest_unpaid(list_transactions(db, customer.id))
        if since is None:
            continue
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        days = (now - since).days
        if days >= min_days:
            result.append(OverdueBalance(customer.id, customer.name, balance, since, days))
    return sorted(result, key=lambda o: o.days, reverse=True)
