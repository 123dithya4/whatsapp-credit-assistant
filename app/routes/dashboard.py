import secrets
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services import transactions as ledger

router = APIRouter(tags=["dashboard"])

PAGE = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"


def require_token(authorization: str = Header(default="")) -> None:
    if not settings.dashboard_token:
        raise HTTPException(status_code=503, detail="Dashboard is disabled. Set DASHBOARD_TOKEN in .env to enable it.")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied.encode(), settings.dashboard_token.encode()):
        raise HTTPException(status_code=401, detail="Invalid dashboard token")


def _utc_iso(dt: datetime) -> str:
    """Stored times are UTC; mark them so the browser converts correctly."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@router.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(PAGE)  # the page holds no data; it asks /api/dashboard with the token


@router.get("/api/dashboard", dependencies=[Depends(require_token)])
def dashboard_data(db: Session = Depends(get_db)):
    balances = ledger.get_all_balances(db)
    owing = [b for b in balances if b.balance > 0]
    total = sum((b.balance for b in owing), Decimal("0"))

    return {
        "generated_at": _utc_iso(datetime.now(timezone.utc)),
        "total_outstanding": f"{total:.2f}",
        "customer_count": len(balances),
        "customers_owing": len(owing),
        "top_debtors": [{"name": b.name, "balance": f"{b.balance:.2f}"} for b in owing[:5]],
        "recent_transactions": [
            {
                "id": t.id,
                "customer": t.customer.name,
                "type": t.type.value,
                "item": t.item,
                "amount": f"{t.amount:.2f}",
                "created_at": _utc_iso(t.created_at),
            }
            for t in ledger.get_recent_transactions(db, 15)
        ],
        "balances": [{"name": b.name, "balance": f"{b.balance:.2f}"} for b in balances],
    }
