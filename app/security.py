import secrets

from fastapi import Header, HTTPException

from app.config import settings


def _check(authorization: str) -> None:
    if not settings.dashboard_token:
        raise HTTPException(status_code=503, detail="Disabled. Set DASHBOARD_TOKEN to enable it.")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied.encode(), settings.dashboard_token.encode()):
        raise HTTPException(status_code=401, detail="Invalid token")


def require_token(authorization: str = Header(default="")) -> None:
    """Always enforced (dashboard data)."""
    _check(authorization)


def require_token_in_production(authorization: str = Header(default="")) -> None:
    """Open while developing locally, enforced in production."""
    if settings.environment == "production":
        _check(authorization)