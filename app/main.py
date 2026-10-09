from fastapi import FastAPI

from app import models  # noqa: F401  (registers the tables)
from app.database import Base, engine
from app.routes import assistant, customers, transactions, whatsapp

Base.metadata.create_all(bind=engine)

app = FastAPI(title="WhatsApp Credit Assistant")

app.include_router(customers.router)
app.include_router(transactions.router)
app.include_router(assistant.router)
app.include_router(whatsapp.router)


@app.get("/health")
def health():
    return {"status": "ok"}
