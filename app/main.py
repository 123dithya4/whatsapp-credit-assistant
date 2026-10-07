from fastapi import FastAPI

from app import models  # noqa: F401  (registers the tables)
from app.database import Base, engine
from app.routes import customers, transactions

Base.metadata.create_all(bind=engine)

app = FastAPI(title="WhatsApp Credit Assistant")

app.include_router(customers.router)
app.include_router(transactions.router)


@app.get("/health")
def health():
    return {"status": "ok"}