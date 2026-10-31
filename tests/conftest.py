import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.database import Base
from app.config import settings


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()

@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch):
    """Tests must never depend on the developer's personal .env file."""
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "allowed_senders", "")
    monkeypatch.setattr(settings, "dashboard_token", "")
    monkeypatch.setattr(settings, "reminder_after_days", 7)
