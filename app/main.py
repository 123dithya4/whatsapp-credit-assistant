import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.config import settings

logging.basicConfig(level=settings.log_level.upper(), format="%(levelname)s:     %(name)s - %(message)s")

from app import models  # noqa: E402,F401  (registers the tables)
from app.database import Base, engine  # noqa: E402
from app.routes import assistant, customers, dashboard, transactions, whatsapp  # noqa: E402
from app.security import require_token_in_production  # noqa: E402

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


PRODUCTION = settings.environment == "production"

app = FastAPI(
    title="WhatsApp Credit Assistant",
    lifespan=lifespan,
    docs_url=None if PRODUCTION else "/docs",
    redoc_url=None if PRODUCTION else "/redoc",
    openapi_url=None if PRODUCTION else "/openapi.json",
)

protected = [Depends(require_token_in_production)]
app.include_router(assistant.router, dependencies=protected)
app.include_router(customers.router, dependencies=protected)
app.include_router(transactions.router, dependencies=protected)
app.include_router(dashboard.router)  # data endpoint has its own token check
app.include_router(whatsapp.router)   # verified by Twilio's signature


@app.get("/health")
def health():
    return {"status": "ok"}