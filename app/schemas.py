from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models import TransactionType


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str | None = None


class CustomerRead(CustomerCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class TransactionCreate(BaseModel):
    customer_id: int
    item: str | None = None
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    type: TransactionType


class TransactionRead(TransactionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime

class BalanceRead(BaseModel):
    customer_id: int
    name: str
    balance: Decimal

class RecordTransactionArgs(BaseModel):
    customer: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0)
    type: TransactionType
    item: str | None = None


class GetBalanceArgs(BaseModel):
    customer: str = Field(min_length=1, max_length=100)


class GetTopDebtorsArgs(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)


class NoArgs(BaseModel):
    pass

class GetPaymentsArgs(BaseModel):
    customer: str = Field(min_length=1, max_length=100)

class GetTopPayersArgs(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)

class RawTransactionArgs(BaseModel):
    """What the LLM extracted. Every field may be missing; Python decides what to do about it."""
    customer: str | None = None
    amount: Decimal | None = None
    amount_quote: str | None = None
    type: str | None = None
    item: str | None = None

class GetOverdueArgs(BaseModel):
    days: int | None = Field(default=None, ge=1, le=365)