import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")

from app import models  # noqa: E402,F401  (registers the tables)
from app.config import settings  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.routes import assistant, customers, transactions, whatsapp  # noqa: E402

logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if settings.reminders_enabled:
        from app.services.reminders import create_scheduler

        scheduler = create_scheduler()
        scheduler.start()
        logger.info("Reminder scheduler started (daily at %02d:%02d IST)",
                    settings.reminder_hour, settings.reminder_minute)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="WhatsApp Credit Assistant", lifespan=lifespan)

app.include_router(assistant.router)
app.include_router(customers.router)
app.include_router(transactions.router)
app.include_router(whatsapp.router)


@app.get("/health")
def health():
    return {"status": "ok"}