import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer
from app.schemas import CustomerCreate


def normalize_name(name: str) -> str:
    """Collapse repeated whitespace and trim a customer name, preserving case."""
    return re.sub(r"\s+", " ", name).strip()


def create_customer(db: Session, data: CustomerCreate) -> Customer:
    clean = normalize_name(data.name)
    if not clean:
        raise ValueError("Customer name cannot be empty")

    customer = Customer(name=clean, phone=data.phone)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def get_customer(db: Session, customer_id: int) -> Customer | None:
    return db.get(Customer, customer_id)


def get_customer_by_name(db: Session, name: str) -> Customer | None:
    """Exact match, case-insensitive. Fuzzy matching comes in a later phase."""
    normalized = normalize_name(name).lower()
    stmt = select(Customer).where(func.lower(Customer.name) == normalized)
    return db.scalars(stmt).first()


def add_customer(db: Session, name: str, phone: str | None = None) -> tuple[Customer, bool]:
    """Get or create a customer; return the customer and whether it was created."""
    clean = normalize_name(name)
    if not clean:
        raise ValueError("Customer name cannot be empty")

    existing = get_customer_by_name(db, clean)
    if existing:
        return existing, False

    customer = Customer(name=clean, phone=phone)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer, True


def list_customers(db: Session) -> list[Customer]:
    return list(db.scalars(select(Customer).order_by(Customer.name)))
