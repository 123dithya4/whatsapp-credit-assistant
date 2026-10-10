from decimal import Decimal

import pytest

from app.models import TransactionType
from app.schemas import RecordTransactionArgs
from app.services import assistant, customers, speech
from app.services.llm import ParsedMessage, ToolCall

SENDER = "whatsapp:+911234567890"


def test_voice_note_flow(db, monkeypatch):
    customers.add_customer(db, "Ramesh")
    monkeypatch.setattr(speech, "download_media", lambda url: b"audio")
    monkeypatch.setattr(
        speech, "transcribe",
        lambda audio, ctype, names=(): "Ramesh took five hundred rupees of rice on credit",
    )
    args = RecordTransactionArgs(
        customer="Ramesh", amount=Decimal("500"), type=TransactionType.credit, item="rice"
    )
    monkeypatch.setattr(
        assistant, "parse_message",
        lambda text: ParsedMessage(calls=[ToolCall("record_transaction", args)]),
    )

    reply = assistant.reply_to_voice(db, "https://api.twilio.com/x", "audio/ogg", SENDER)
    assert 'Heard: "Ramesh took five hundred rupees of rice on credit"' in reply
    assert "✅" in reply
    assert "₹500.00" in reply


def test_download_failure_gives_friendly_reply(db, monkeypatch):
    def boom(url):
        raise speech.SpeechError("nope")

    monkeypatch.setattr(speech, "download_media", boom)
    reply = assistant.reply_to_voice(db, "https://api.twilio.com/x", "audio/ogg", SENDER)
    assert "couldn't process" in reply


def test_silent_audio_gives_hint(db, monkeypatch):
    monkeypatch.setattr(speech, "download_media", lambda url: b"audio")
    monkeypatch.setattr(speech, "transcribe", lambda audio, ctype, names=(): "")
    reply = assistant.reply_to_voice(db, "https://api.twilio.com/x", "audio/ogg", SENDER)
    assert "couldn't hear" in reply


def test_download_rejects_non_twilio_host():
    with pytest.raises(speech.SpeechError):
        speech.download_media("https://evil.example.com/a.ogg")


def test_is_audio():
    assert speech.is_audio("audio/ogg")
    assert not speech.is_audio("image/jpeg")