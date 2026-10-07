from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import BalanceRead, CustomerCreate, CustomerRead
from app.services import customers, transactions

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post("", response_model=CustomerRead, status_code=201)
def create_customer(data: CustomerCreate, db: Session = Depends(get_db)):
    if customers.get_customer_by_name(db, data.name):
        raise HTTPException(status_code=409, detail="Customer with this name already exists")
    return customers.create_customer(db, data)


@router.get("", response_model=list[CustomerRead])
def list_customers(db: Session = Depends(get_db)):
    return customers.list_customers(db)


@router.get("/{name}/balance", response_model=BalanceRead)
def customer_balance(name: str, db: Session = Depends(get_db)):
    customer = customers.get_customer_by_name(db, name)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return BalanceRead(
        customer_id=customer.id,
        name=customer.name,
        balance=transactions.get_balance(db, customer.id),
    )