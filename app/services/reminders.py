import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import settings
from app.database import SessionLocal
from app.services import transactions as ledger
from app.services.actions import format_overdue

logger = logging.getLogger(__name__)


def send_whatsapp(to: str, body: str) -> bool:
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        logger.warning("Twilio credentials are missing; reminder not sent:\n%s", body)
        return False
    try:
        Client(settings.twilio_account_sid, settings.twilio_auth_token).messages.create(
            from_=settings.twilio_whatsapp_from, to=to, body=body[:1500]
        )
        return True
    except TwilioRestException as exc:
        if exc.code == 63016:
            logger.error(
                "WhatsApp only allows free-form messages within 24 hours of the user's last "
                "message. Send any message to the sandbox first, then run the reminder again."
            )
        else:
            logger.error("Twilio could not send the reminder (code %s): %s", exc.code, exc.msg)
        return False


def run_reminder_job() -> str | None:
    """Check overdue balances and send one WhatsApp digest to the shop owner."""
    db = SessionLocal()
    try:
        overdue = ledger.get_overdue(db, settings.reminder_after_days)
    finally:
        db.close()

    if not overdue:
        logger.info("Reminder job: no overdue balances")
        return None

    message = format_overdue(overdue, settings.reminder_after_days)
    if not settings.owner_whatsapp:
        logger.warning("OWNER_WHATSAPP is not set; reminder not sent:\n%s", message)
        return message

    if send_whatsapp(settings.owner_whatsapp, message):
        logger.info("Reminder sent: %d overdue customer(s)", len(overdue))
    return message


def create_scheduler() -> BackgroundScheduler:
    tz = str(ledger.LOCAL_TZ)  # Asia/Kolkata
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        run_reminder_job,
        CronTrigger(hour=settings.reminder_hour, minute=settings.reminder_minute, timezone=tz),
        id="daily_overdue_reminder",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,  # if the server was down at 9:00, still run within the hour
    )
    return scheduler