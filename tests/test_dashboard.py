import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.services import transactions as ledger

AUTH = {"Authorization": "Bearer secret-token"}


@pytest.fixture
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(settings, "dashboard_token", "secret-token")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_page_is_served(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "Credit Book" in r.text


def test_data_requires_token(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/dashboard", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_dashboard_disabled_without_token(client, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_token", "")
    assert client.get("/api/dashboard", headers=AUTH).status_code == 503


def test_dashboard_numbers(client, db):
    ledger.add_transaction(db, "Ramesh", 500, item="rice")
    ledger.add_transaction(db, "Suresh", 300)
    ledger.record_payment(db, "Suresh", 300)   # settled
    ledger.add_transaction(db, "Anil", 100)
    ledger.record_payment(db, "Anil", 150)     # advance: not counted as outstanding

    data = client.get("/api/dashboard", headers=AUTH).json()
    assert data["total_outstanding"] == "500.00"
    assert data["customer_count"] == 3
    assert data["customers_owing"] == 1
    assert [d["name"] for d in data["top_debtors"]] == ["Ramesh"]
    assert len(data["recent_transactions"]) == 5
    assert {t["customer"] for t in data["recent_transactions"]} == {"Ramesh", "Suresh", "Anil"}
    assert {b["name"] for b in data["balances"]} == {"Ramesh", "Suresh", "Anil"}