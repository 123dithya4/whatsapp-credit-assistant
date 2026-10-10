from datetime import timezone
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import TransactionType
from app.schemas import (
    GetBalanceArgs,
    GetPaymentsArgs,
    GetTopDebtorsArgs,
    GetTopPayersArgs,
    RecordTransactionArgs,
)
from app.services import transactions as ledger


def _rs(value: Decimal) -> str:
    return f"₹{value:,.2f}"


UNSUPPORTED_TEXT = (
    "Sorry, I can't help with that one. I can add credit, record payments, "
    "and answer questions like:\n"
    "_How much does [customer] owe?_\n"
    "_Who owes me the most?_\n"
    "_Who paid the most?_\n\n"
    "Type *help* to see everything."
)


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
                result = ledger.add_transaction(db, args.customer, args.amount, args.item)
                item = f" of {result.transaction.item}" if result.transaction.item else ""
                return (
                    f"✅ {result.customer.name} took ₹{result.transaction.amount}{item} on credit. "
                    f"{_owed_text(result.balance)}"
                )
            result = ledger.record_payment(db, args.customer, args.amount)
            return (
                f"✅ {result.customer.name} paid ₹{result.transaction.amount}. "
                f"{_owed_text(result.balance)}"
            )

        if name == "get_balance":
            assert isinstance(args, GetBalanceArgs)
            balance = ledger.get_balance_by_name(db, args.customer)
            if balance.balance < 0:
                return f"{balance.name} has paid {_rs(-balance.balance)} in advance"
            return f"{balance.name} owes {_rs(balance.balance)}"

        if name == "get_payments":
            assert isinstance(args, GetPaymentsArgs)
            summary = ledger.get_payments(db, args.customer)
            if summary.count == 0:
                return f"{summary.customer.name} hasn't paid anything yet."
            owed = ledger.get_balance(db, summary.customer.id)
            lines = [f"- {_local_date(txn.created_at)}: {_rs(txn.amount)}" for txn in summary.recent]
            plural = "payment" if summary.count == 1 else "payments"
            return (
                f"{summary.customer.name} has paid {_rs(summary.total)} in total "
                f"({summary.count} {plural}).\n"
                + "\n".join(lines)
                + f"\nStill owes: {_rs(owed)}"
            )

        if name == "get_top_debtors":
            assert isinstance(args, GetTopDebtorsArgs)
            debtors = ledger.get_top_debtors(db, args.limit)
            if not debtors:
                return "Nobody owes you anything right now 🎉"
            lines = [f"{i}. {debtor.name} — {_rs(debtor.balance)}" for i, debtor in enumerate(debtors, 1)]
            return "Top debtors:\n" + "\n".join(lines)

        if name == "get_top_payers":
            assert isinstance(args, GetTopPayersArgs)
            payers = ledger.get_top_payers(db, args.limit)
            if not payers:
                return "No payments recorded yet."
            lines = [f"{i}. {customer} — {_rs(total)}" for i, (customer, total) in enumerate(payers, 1)]
            return "Customers who paid the most:\n" + "\n".join(lines)

        if name == "get_today_transactions":
            transactions = ledger.get_today_transactions(db)
            if not transactions:
                return "No entries today yet."
            lines = [f"- {txn.customer.name}: {txn.type.value} {_rs(txn.amount)}" for txn in transactions]
            given = sum(
                (txn.amount for txn in transactions if txn.type == TransactionType.credit),
                Decimal("0"),
            )
            received = sum(
                (txn.amount for txn in transactions if txn.type == TransactionType.payment),
                Decimal("0"),
            )
            return (
                "Today's entries:\n" + "\n".join(lines)
                + f"\nGiven on credit: {_rs(given)} | Received: {_rs(received)}"
            )

        if name == "unsupported_request":
            return UNSUPPORTED_TEXT

        return f"⚠️ Unsupported action '{name}'"

    except ledger.CustomerNotFoundError as exc:
        return f"⚠️ {exc}. Please check the spelling."
    except ledger.LedgerError as exc:
        return f"⚠️ {exc}"
