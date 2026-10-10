import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from app.models import TransactionType
from app.schemas import RawTransactionArgs, RecordTransactionArgs
from app.services.customers import normalize_name


@dataclass
class Clarification:
    question: str                # what to ask the user
    partial: RawTransactionArgs  # everything understood so far
    missing: str                 # "customer", "amount" or "type"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def amount_is_grounded(amount: Decimal | None, quote: str | None, text: str) -> bool:
    """True only if the amount is backed by words the user really wrote or said."""
    if amount is None or amount <= 0 or not quote or not quote.strip():
        return False
    if _norm(quote) not in _norm(text):
        return False  # the model made the quote up
    numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", quote)
    if len(numbers) == 1:
        try:
            return Decimal(numbers[0].replace(",", "")) == amount
        except InvalidOperation:
            return False
    return True  # spoken words ("five hundred") cannot be checked numerically


def _to_type(value: str | None) -> TransactionType | None:
    try:
        return TransactionType((value or "").strip().lower())
    except ValueError:
        return None


def resolve_entry(raw: RawTransactionArgs, text: str) -> RecordTransactionArgs | Clarification:
    """Return a complete, validated entry, or a Clarification (a question plus what we
    understood so far). Never fills in a missing value."""
    name = normalize_name(raw.customer or "")
    type_ = _to_type(raw.type)
    item = (raw.item or "").strip() or None
    has_amount = amount_is_grounded(raw.amount, raw.amount_quote, text)

    partial = RawTransactionArgs(
        customer=name or None,
        amount=raw.amount if has_amount else None,
        amount_quote=raw.amount_quote if has_amount else None,
        type=type_.value if type_ else None,
        item=item,
    )

    if not name:
        question = (f"Which customer is this ₹{raw.amount:,.2f} entry for?" if has_amount
                    else "Which customer is this for, and what was the amount?")
        return Clarification(question, partial, "customer")

    if not has_amount:
        if type_ == TransactionType.payment:
            question = f"How much did {name} pay?"
        elif type_ == TransactionType.credit:
            question = (f"What was the amount for {name}'s {item} purchase?" if item
                        else f"What was the amount {name} took on credit?")
        else:
            question = f"What was the amount for {name}'s {item}?" if item else f"What was the amount for {name}?"
        return Clarification(question, partial, "amount")

    if type_ is None:
        question = (f"Did {name} take ₹{raw.amount:,.2f} on credit, or pay ₹{raw.amount:,.2f}? "
                    "Reply *credit* or *payment*.")
        return Clarification(question, partial, "type")

    try:
        return RecordTransactionArgs(customer=name, amount=raw.amount, type=type_, item=item)
    except ValidationError:
        return Clarification("That name looks too long. Please send just the customer's name.", partial, "customer")