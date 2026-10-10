import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PendingClarification, PendingConfirmation
from app.schemas import RecordTransactionArgs

EXPIRY = timedelta(minutes=10)
YES = {"yes", "y", "yeah", "ok", "okay", "haan", "han", "ha"}
NO = {"no", "n", "nahi", "nahin", "cancel"}


def clear_pending(db: Session, sender: str) -> None:
    db.execute(delete(PendingConfirmation).where(PendingConfirmation.sender == sender))
    db.commit()


def save_pending(db: Session, sender: str, entries: list[RecordTransactionArgs]) -> None:
    """One pending confirmation per sender; a new one replaces the old."""
    clear_pending(db, sender)
    payload = json.dumps([entry.model_dump(mode="json") for entry in entries])
    db.add(PendingConfirmation(sender=sender, payload=payload))
    db.commit()


def get_pending(db: Session, sender: str) -> list[RecordTransactionArgs] | None:
    row = db.scalars(
        select(PendingConfirmation)
        .where(PendingConfirmation.sender == sender)
        .order_by(PendingConfirmation.created_at.desc())
    ).first()
    if row is None:
        return None

    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > EXPIRY:
        clear_pending(db, sender)
        return None

    return [RecordTransactionArgs.model_validate(item) for item in json.loads(row.payload)]

def clear_clarification(db: Session, sender: str) -> None:
    db.execute(delete(PendingClarification).where(PendingClarification.sender == sender))
    db.commit()


def save_clarification(db: Session, sender: str, original_text: str) -> None:
    """Remember the message that was missing information, so a short reply can complete it."""
    clear_clarification(db, sender)
    db.add(PendingClarification(sender=sender, original_text=original_text))
    db.commit()


def get_clarification(db: Session, sender: str) -> str | None:
    row = db.scalars(
        select(PendingClarification)
        .where(PendingClarification.sender == sender)
        .order_by(PendingClarification.created_at.desc())
    ).first()
    if row is None:
        return None
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > EXPIRY:
        clear_clarification(db, sender)
        return None
    return row.original_text
