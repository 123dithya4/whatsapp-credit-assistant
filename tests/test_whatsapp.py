import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.config import settings
from app.database import get_db
from app.main import app

URL = "/whatsapp/webhook"
PARAMS = {"Body": "Ramesh took 500 rupees of rice on credit", "From": "whatsapp:+911234567890"}


@pytest.fixture
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr("app.routes.whatsapp.reply_to_text", lambda db, text, sender: "stub reply")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_webhook_returns_twiml(client, monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", False)
    r = client.post(URL, data=PARAMS)
    assert r.status_code == 200
    assert "<Message>stub reply</Message>" in r.text


def test_missing_signature_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", True)
    monkeypatch.setattr(settings, "twilio_auth_token", "test_token")
    monkeypatch.setattr(settings, "public_base_url", "https://example.com")
    assert client.post(URL, data=PARAMS).status_code == 403


def test_valid_signature_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", True)
    monkeypatch.setattr(settings, "twilio_auth_token", "test_token")
    monkeypatch.setattr(settings, "public_base_url", "https://example.com")
    sig = RequestValidator("test_token").compute_signature("https://example.com" + URL, PARAMS)
    r = client.post(URL, data=PARAMS, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200


def test_voice_note_gets_placeholder(client, monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", False)
    r = client.post(URL, data={"Body": "", "NumMedia": "1", "From": "whatsapp:+911234567890"})
    assert "Voice notes are coming soon" in r.text


def test_welcome_greets_by_profile_name(client, monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", False)
    r = client.post(
        URL,
        data={"Body": "hi", "ProfileName": "Dithya M", "From": "whatsapp:+911234567890"},
    )
    assert "Hi Dithya" in r.text
    assert r.text.count("<Message>") == 2
