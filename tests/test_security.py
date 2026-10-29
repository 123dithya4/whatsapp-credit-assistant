import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app

AUTH = {"Authorization": "Bearer secret-token"}
HOOK = "/whatsapp/webhook"


@pytest.fixture
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(settings, "dashboard_token", "secret-token")
    monkeypatch.setattr(settings, "twilio_validate_signature", False)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_rest_routes_open_in_development(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    assert client.get("/customers").status_code == 200


def test_rest_routes_need_token_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    assert client.get("/customers").status_code == 401
    assert client.get("/customers", headers=AUTH).status_code == 200


def test_unlisted_sender_is_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "allowed_senders", "whatsapp:+911111111111")
    r = client.post(HOOK, data={"Body": "hi", "From": "whatsapp:+922222222222"})
    assert "not authorised" in r.text


def test_production_without_allowlist_rejects_everyone(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "allowed_senders", "")
    r = client.post(HOOK, data={"Body": "hi", "From": "whatsapp:+911111111111"})
    assert "not authorised" in r.text