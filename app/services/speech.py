import logging
from urllib.parse import urlparse

import httpx
from groq import Groq, GroqError

from app.config import settings

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # a voice note is normally well under 1 MB

# Content types Groq's Whisper accepts, mapped to a file extension
_EXTENSIONS = {
    "audio/ogg": "ogg", "audio/opus": "ogg",
    "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a",
    "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/webm": "webm", "audio/flac": "flac",
}


class SpeechError(Exception):
    """Downloading or transcribing the voice note failed."""


def is_audio(content_type: str) -> bool:
    return content_type.strip().lower().startswith("audio/")


def download_media(url: str) -> bytes:
    """Download a Twilio media file. Only twilio.com HTTPS links are allowed,
    because we attach your Twilio credentials to the request."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (host == "twilio.com" or host.endswith(".twilio.com")):
        raise SpeechError("Refusing to download media from a non-Twilio address")
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise SpeechError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not set")

    try:
        with httpx.Client(follow_redirects=True, timeout=15) as client:
            resp = client.get(url, auth=(settings.twilio_account_sid, settings.twilio_auth_token))
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise SpeechError(f"Could not download the voice note: {exc}") from exc

    if len(resp.content) > MAX_AUDIO_BYTES:
        raise SpeechError("Voice note is too large")
    return resp.content


def _build_prompt(names) -> str:
    """Whisper uses this as a spelling hint. Only customer names, no English words."""
    if not names:
        return ""
    return ("Customers: " + ", ".join(names) + ".")[:600]


def transcribe(audio: bytes, content_type: str, hint_names=()) -> str:
    ext = _EXTENSIONS.get(content_type.split(";")[0].strip().lower())
    if ext is None:
        raise SpeechError(f"Unsupported audio type: {content_type}")
    if not settings.groq_api_key:
        raise SpeechError("GROQ_API_KEY is not set")

    client = Groq(api_key=settings.groq_api_key)
    prompt = _build_prompt(hint_names)

    def run(language: str | None = None):
        extra = {"language": language} if language else {}
        if prompt:
            extra["prompt"] = prompt
        return client.audio.transcriptions.create(
            file=(f"voice.{ext}", audio),
            model=settings.whisper_model,
            response_format="verbose_json",
            temperature=0.0,
            **extra,
        )

    try:
        if settings.whisper_language:  # a forced language always wins
            return (run(settings.whisper_language).text or "").strip()

        result = run()
        detected = (getattr(result, "language", "") or "").strip().lower()
        allowed = {x.strip().lower() for x in settings.whisper_allowed_languages.split(",") if x.strip()}
        logger.info("Whisper detected language: %r", detected)

        if detected and allowed and detected not in allowed and settings.whisper_fallback_language:
            logger.info("%r is not an allowed language, retrying as %s",
                        detected, settings.whisper_fallback_language)
            result = run(settings.whisper_fallback_language)
        return (result.text or "").strip()
    except GroqError as exc:
        raise SpeechError(f"Transcription failed: {exc}") from exc