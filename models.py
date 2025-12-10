from time import time

from fastapi import Query
from pydantic import BaseModel, Field, validator


class PayLnurlWData(BaseModel):
    lnurl: str


class CreateWithdrawPay(BaseModel):
    pay_link: str


class CreateTposInvoice(BaseModel):
    amount: int = Query(..., ge=1)
    memo: str | None = Query(None)
    exchange_rate: float | None = Query(None, ge=0.0)
    details: dict | None = Query(None)
    inventory: "InventorySale" | None = Query(None)
    tip_amount: int | None = Query(None, ge=1)
    user_lnaddress: str | None = Query(None)
    internal_memo: str | None = Query(None, max_length=512)
    pay_in_fiat: bool = Query(False)
    fiat_method: str | None = Query(None)
    amount_fiat: float | None = Query(None, ge=0.0)
    tip_amount_fiat: float | None = Query(None, ge=0.0)


class InventorySaleItem(BaseModel):
    id: str
    quantity: int = Field(1, ge=1)


class InventorySale(BaseModel):
    inventory_id: str
    tags: list[str] = Field(default_factory=list)
    items: list[InventorySaleItem] = Field(default_factory=list)


class CreateTposData(BaseModel):
    wallet: str | None
    name: str | None
    currency: str | None
    use_inventory: bool = Field(False)
    inventory_id: str | None = None
    inventory_tags: list[str] | None = None
    tax_inclusive: bool = Field(True)
    tax_default: float = Field(0.0)
    tip_options: str = Field("[]")
    tip_wallet: str = Field("")
    withdraw_time: int = Field(0)
    withdraw_between: int = Field(10, ge=1)
    withdraw_limit: int | None = Field(None, ge=1)
    withdraw_time_option: str | None = Field(None)
    withdraw_premium: float | None = Field(None)
    lnaddress: bool = Field(False)
    lnaddress_cut: int | None = Field(0)
    enable_receipt_print: bool = Query(False)
    business_name: str | None
    business_address: str | None
    business_vat_id: str | None
    fiat_provider: str | None = Field(None)
    stripe_card_payments: bool = False


class TposClean(BaseModel):
    id: str
    name: str
    currency: str
    tax_inclusive: bool
    tax_default: float | None = None
    withdraw_time: int
    withdraw_between: int
    withdraw_limit: int | None = None
    withdraw_time_option: str | None = None
    withdraw_premium: float | None = None
    withdrawn_amount: int = 0
    lnaddress: bool | None = None
    lnaddress_cut: int = 0
    items: str | None = None
    use_inventory: bool = False
    inventory_id: str | None = None
    inventory_tags: str | None = None
    tip_options: str | None = None
    enable_receipt_print: bool
    business_name: str | None = None
    business_address: str | None = None
    business_vat_id: str | None = None
    fiat_provider: str | None = None
    stripe_card_payments: bool = False

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
    tip_wallet: str | None = None


class LnurlCharge(BaseModel):
    id: str
    tpos_id: str
    amount: int = 0
    claimed: bool = False


class Item(BaseModel):
    image: str | None
    price: float
    title: str
    description: str | None
    tax: float | None = Field(0, ge=0.0)
    disabled: bool = False
    categories: list[str] | None = []

    @validator("tax", pre=True, always=True)
    def set_default_tax(cls, v):
        return v or 0


class CreateUpdateItemData(BaseModel):
    items: list[Item]


class TapToPay(BaseModel):
    type: str = "tap_to_pay"
    payment_intent_id: str | None = None
    client_secret: str | None = None
    amount: int = 0
    currency: str | None = None
    tpos_id: str | None = None
    payment_hash: str | None = None
    paid: bool = False


CreateTposInvoice.update_forward_refs(InventorySale=InventorySale)
