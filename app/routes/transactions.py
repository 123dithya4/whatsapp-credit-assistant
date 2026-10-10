from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import TransactionCreate, TransactionRead
from app.services import customers, transactions

router = APIRouter(prefix="/transactions", tags=["transactions"])



@router.post("", response_model=TransactionRead, status_code=201)
def add_transaction(data: TransactionCreate, db: Session = Depends(get_db)):
    if customers.get_customer(db, data.customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return transactions.create_transaction(db, data)


@router.get("", response_model=list[TransactionRead])
def get_transactions(customer_id: int | None = None, db: Session = Depends(get_db)):
    """All transactions, newest first. Add ?customer_id=1 to filter."""
    return transactions.list_all_transactions(db, customer_id)


@router.delete("/{transaction_id}", status_code=204)
def remove_transaction(transaction_id: int, db: Session = Depends(get_db)):
    if not transactions.delete_transaction(db, transaction_id):
        raise HTTPException(status_code=404, detail="Transaction not found")