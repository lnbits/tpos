from time import time
from typing import Optional

from fastapi import Query
from pydantic import BaseModel, Field, validator


class PayLnurlWData(BaseModel):
    lnurl: str


class CreateWithdrawPay(BaseModel):
    pay_link: str


class CreateTposInvoice(BaseModel):
    amount: int = Query(..., ge=1)
    memo: Optional[str] = Query(None)
    exchange_rate: Optional[float] = Query(None, ge=0.0)
    details: Optional[dict] = Query(None)
    tip_amount: Optional[int] = Query(None, ge=1)
    user_lnaddress: Optional[str] = Query(None)
    internal_memo: Optional[str] = Query(None, max_length=512)
    pay_in_fiat: bool = Query(False)
    amount_fiat: Optional[float] = Query(None, ge=0.0)
    tip_amount_fiat: Optional[float] = Query(None, ge=0.0)


class CreateTposData(BaseModel):
    wallet: Optional[str]
    name: Optional[str]
    currency: Optional[str]
    tax_inclusive: bool = Field(True)
    tax_default: float = Field(None)
    tip_options: str = Field("[]")
    tip_wallet: str = Field("")
    withdraw_time: int = Field(0)
    withdraw_between: int = Field(10, ge=1)
    withdraw_limit: Optional[int] = Field(None, ge=1)
    withdraw_time_option: Optional[str] = Field(None)
    withdraw_premium: Optional[float] = Field(None)
    lnaddress: bool = Field(False)
    lnaddress_cut: Optional[int] = Field(0)
    enable_receipt_print: bool = Query(False)
    business_name: Optional[str]
    business_address: Optional[str]
    business_vat_id: Optional[str]
    fiat_provider: Optional[str] = Field(None)


class TposClean(BaseModel):
    id: str
    name: str
    currency: str
    tax_inclusive: bool
    tax_default: Optional[float] = None
    withdraw_time: int
    withdraw_between: int
    withdraw_limit: Optional[int] = None
    withdraw_time_option: Optional[str] = None
    withdraw_premium: Optional[float] = None
    withdrawn_amount: int = 0
    lnaddress: Optional[bool] = None
    lnaddress_cut: int = 0
    items: Optional[str] = None
    tip_options: Optional[str] = None
    enable_receipt_print: bool
    business_name: Optional[str] = None
    business_address: Optional[str] = None
    business_vat_id: Optional[str] = None
    fiat_provider: Optional[str] = None

    @property
    def withdraw_maximum(self) -> int:
        if not self.withdraw_limit:
            return 0
        return self.withdraw_limit - self.withdrawn_amount

    @property
    def can_withdraw(self) -> bool:
        now = int(time())
        seconds = (
            self.withdraw_between * 60
            if self.withdraw_time_option != "secs"
            else self.withdraw_between
        )
        last_withdraw_time = self.withdraw_time - now
        return last_withdraw_time < seconds


class Tpos(TposClean, BaseModel):
    wallet: str
    tip_wallet: Optional[str] = None


class LnurlCharge(BaseModel):
    id: str
    tpos_id: str
    amount: int = 0
    claimed: bool = False


class Item(BaseModel):
    image: Optional[str]
    price: float
    title: str
    description: Optional[str]
    tax: Optional[float] = Field(0, ge=0.0)
    disabled: bool = False
    categories: Optional[list[str]] = []

    @validator("tax", pre=True, always=True)
    def set_default_tax(cls, v):
        return v or 0


class CreateUpdateItemData(BaseModel):
    items: list[Item]
