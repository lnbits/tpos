from typing import Optional

from fastapi import Query, Request
from lnurl import Lnurl
from lnurl import encode as lnurl_encode
from pydantic import BaseModel, Field, validator


class CreateWithdrawPay(BaseModel):
    pay_link: str


class CreateTposInvoice(BaseModel):
    amount: int = Query(..., ge=1)
    memo: Optional[str] = Query(None)
    details: Optional[dict] = Query(None)
    tip_amount: Optional[int] = Query(None, ge=1)
    user_lnaddress: Optional[str] = Query(None)


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
    withdraw_pin: Optional[int] = Field(None, ge=1)
    withdraw_time_option: Optional[str] = Field(None)
    withdraw_premium: Optional[float] = Field(None)
    withdraw_pin_disabled: bool = Field(False)
    lnaddress: bool = Field(False)
    lnaddress_cut: Optional[int] = Field(0)
    enable_receipt_print: bool = Query(False)
    business_name: Optional[str]
    business_address: Optional[str]
    business_vat_id: Optional[str]

    @validator("withdraw_pin", pre=True)
    def empty_string_to_none(cls, v):
        if v == "" or v is None:
            return None
        return v


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
    withdraw_pin_disabled: Optional[bool] = None
    withdrawn_amount: int = 0
    lnaddress: Optional[bool] = None
    lnaddress_cut: int = 0
    items: Optional[str] = None
    tip_options: Optional[str] = None
    enable_receipt_print: bool
    business_name: Optional[str] = None
    business_address: Optional[str] = None
    business_vat_id: Optional[str] = None

    @property
    def withdraw_maximum(self) -> int:
        if not self.withdraw_limit:
            return 0
        return self.withdraw_limit - self.withdrawn_amount


class Tpos(TposClean, BaseModel):
    wallet: str
    tip_wallet: Optional[str] = None
    withdraw_pin: Optional[int] = None


class LnurlCharge(BaseModel):
    id: str
    tpos_id: str
    amount: int = Field(None)
    claimed: bool = Field(False)

    def lnurl(self, req: Request) -> Lnurl:
        url = str(
            req.url_for(
                "tpos.tposlnurlcharge",
                lnurlcharge_id=self.id,
                amount=self.amount,
            )
        )
        return lnurl_encode(url)


class HashCheck(BaseModel):
    hash: bool
    lnurl: bool


class PayLnurlWData(BaseModel):
    lnurl: str


class LNaddress(BaseModel):
    lnaddress: str


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
