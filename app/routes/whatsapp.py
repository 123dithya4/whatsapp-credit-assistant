import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from app.config import settings
from app.database import get_db
from app.services import speech
from app.services.assistant import EMPTY_TEXT, reply_to_text, reply_to_voice

from sqlalchemy.exc import IntegrityError

from app.models import ProcessedMessage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

MAX_REPLY_CHARS = 1500  # WhatsApp via Twilio allows 1600; keep a margin

EXAMPLES_TEXT = (
    "Type or send a 🎤 voice note, in your own words:\n\n"
    "*Add credit*\n"
    "_[customer] took [amount] rupees of [item] on credit_\n\n"
    "*Record a payment*\n"
    "_[customer] paid [amount]_\n\n"
    "*Ask*\n"
    "_How much does [customer] owe?_\n"
    "_Who owes me the most?_\n"
    "_Show today's transactions_\n\n"
    "Type *help* anytime to see this again."
    "_Who paid the most?_\n"
)


def _first_name(profile_name: str) -> str:
    parts = profile_name.strip().split()
    name = parts[0][:30] if parts else ""
    return name if any(ch.isalpha() for ch in name) else ""


def _welcome_messages(profile_name: str) -> tuple[str, str]:
    name = _first_name(profile_name)
    greeting = f"Hi {name} \U0001F44B How can I help you today?" if name else "Hi \U0001F44B How can I help you today?"
    return greeting, EXAMPLES_TEXT


UNSUPPORTED_MEDIA_TEXT = "I can read text messages and voice notes. Please send one of those."


def _twiml(*texts: str) -> Response:
    resp = MessagingResponse()
    for text in texts:
        resp.message(text[:MAX_REPLY_CHARS])
    return Response(content=str(resp), media_type="application/xml")


def _verify_signature(request: Request, params: dict) -> None:
    if not settings.twilio_validate_signature:
        return
    if not settings.twilio_auth_token or not settings.public_base_url:
        raise HTTPException(status_code=500, detail="Twilio settings are missing")
    # Twilio signs the exact public URL it called, so rebuild it from PUBLIC_BASE_URL
    url = settings.public_base_url.rstrip("/") + request.url.path
    signature = request.headers.get("X-Twilio-Signature", "")
    if not RequestValidator(settings.twilio_auth_token).validate(url, params, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

async def _safe_reply(db: Session, func, *args) -> str:
    """Run the handler off the event loop; never let an unexpected error reach Twilio as a 500."""
    try:
        return await run_in_threadpool(func, db, *args)
    except Exception:
        logger.exception("Unhandled error while handling a WhatsApp message")
        db.rollback()
        return "⚠️ Something went wrong on my side. Please try again in a minute."

def _is_allowed(sender: str) -> bool:
    allowed = {s.strip() for s in settings.allowed_senders.split(",") if s.strip()}
    if not allowed:
        return settings.environment != "production"  # production with no list = nobody
    return sender in allowed

def _claim_message(db: Session, sid: str) -> bool:
    """True the first time Twilio's message ID is seen; False for a retry of the same message."""
    try:
        db.add(ProcessedMessage(sid=sid))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False

@router.post("/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    params = dict(form)
    _verify_signature(request, params)

    body = (params.get("Body") or "").strip()
    profile_name = str(params.get("ProfileName", ""))
    sender = str(params.get("From", ""))

    if not _is_allowed(sender):
        logger.warning("Rejected message from unauthorised sender ...%s", sender[-4:])
        return _twiml("Sorry, this number is not authorised to use this assistant.")

    sid = str(params.get("MessageSid", ""))
    if sid and not _claim_message(db, sid):
        logger.info("Ignoring a retry of message ...%s", sid[-6:])
        return Response(content=str(MessagingResponse()), media_type="application/xml")
    
    num_media = int(params.get("NumMedia") or 0)
    logger.info("WhatsApp message from ...%s (media=%s)", sender[-4:], num_media)

    if num_media > 0:
        media_type = str(params.get("MediaContentType0", ""))
        media_url = str(params.get("MediaUrl0", ""))
        if not speech.is_audio(media_type):
            return _twiml(UNSUPPORTED_MEDIA_TEXT)
        reply = await _safe_reply(db, reply_to_voice, media_url, media_type, sender)
        return _twiml(reply)
    text = body.lower()
    if text == "help":
        return _twiml(EXAMPLES_TEXT)
    if text in {"hi", "hello", "start"}:
        return _twiml(*_welcome_messages(profile_name))
    if not body:
        return _twiml(EMPTY_TEXT)

    # Groq + database calls are blocking, so run them off the event loop
    reply = await _safe_reply(db, reply_to_text, body, sender)
    return _twiml(reply)
