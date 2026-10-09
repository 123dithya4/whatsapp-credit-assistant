from datetime import timezone
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import TransactionType
from app.schemas import (
    GetBalanceArgs,
    GetPaymentsArgs,
    GetTopDebtorsArgs,
    RecordTransactionArgs,
)
from app.services import transactions as ledger


def _rs(value: Decimal) -> str:
    return f"₹{value:,.2f}"


def _owed_text(balance: Decimal) -> str:
    if balance < 0:
        return f"Advance paid: {_rs(-balance)}"
    return f"Total owed: {_rs(balance)}"


def _local_date(dt) -> str:
    """Stored times are UTC; show the date in the shop's local time (IST)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ledger.LOCAL_TZ).strftime("%d %b")


def run_call(db: Session, name: str, args: BaseModel) -> str:
    """Execute one validated tool call through the Python business logic."""
    try:
        if name == "record_transaction":
            assert isinstance(args, RecordTransactionArgs)
            if args.type == TransactionType.credit:
                r = ledger.add_transaction(db, args.customer, args.amount, args.item)
                what = f" of {r.transaction.item}" if r.transaction.item else ""
                return (f"✅ {r.customer.name} took ₹{r.transaction.amount}{what} on credit. "
                        f"{_owed_text(r.balance)}")
            r = ledger.record_payment(db, args.customer, args.amount)
            return (f"✅ {r.customer.name} paid ₹{r.transaction.amount}. "
                    f"{_owed_text(r.balance)}")

        if name == "get_balance":
            assert isinstance(args, GetBalanceArgs)
            b = ledger.get_balance_by_name(db, args.customer)
            if b.balance < 0:
                return f"{b.name} has paid {_rs(-b.balance)} in advance"
            return f"{b.name} owes {_rs(b.balance)}"

        if name == "get_payments":
            assert isinstance(args, GetPaymentsArgs)
            s = ledger.get_payments(db, args.customer)
            if s.count == 0:
                return f"{s.customer.name} hasn't paid anything yet."
            owed = ledger.get_balance(db, s.customer.id)
            lines = [f"- {_local_date(t.created_at)}: {_rs(t.amount)}" for t in s.recent]
            plural = "payment" if s.count == 1 else "payments"
            return (f"{s.customer.name} has paid {_rs(s.total)} in total ({s.count} {plural}).\n"
                    + "\n".join(lines)
                    + f"\nStill owes: {_rs(owed)}")

        if name == "get_top_debtors":
            assert isinstance(args, GetTopDebtorsArgs)
            debtors = ledger.get_top_debtors(db, args.limit)
            if not debtors:
                return "Nobody owes you anything right now 🎉"
            lines = [f"{i}. {d.name} — {_rs(d.balance)}" for i, d in enumerate(debtors, 1)]
            return "Top debtors:\n" + "\n".join(lines)

        if name == "get_today_transactions":
            txns = ledger.get_today_transactions(db)
            if not txns:
                return "No entries today yet."
            lines = [f"- {t.customer.name}: {t.type.value} {_rs(t.amount)}" for t in txns]
            given = sum((t.amount for t in txns if t.type == TransactionType.credit), Decimal("0"))
            received = sum((t.amount for t in txns if t.type == TransactionType.payment), Decimal("0"))
            return ("Today's entries:\n" + "\n".join(lines)
                    + f"\nGiven on credit: {_rs(given)} | Received: {_rs(received)}")

        return f"⚠️ Unsupported action '{name}'"

    except ledger.LedgerError as exc:
        return f"⚠️ {exc}"
