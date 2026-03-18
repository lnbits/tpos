from __future__ import annotations

from datetime import datetime
from time import time
from typing import Any, Literal

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
    notes: dict | None = Query(None)
    inventory: InventorySale | None = Query(None)
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
    inventory_omit_tags: list[str] | None = None
    tax_inclusive: bool = Field(True)
    tax_default: float | None = Field(0.0)
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
    enable_remote: bool = Query(False)
    business_name: str | None
    business_address: str | None
    business_vat_id: str | None
    only_show_sats_on_bitcoin: bool = Query(True)
    fiat_provider: str | None = Field(None)
    stripe_card_payments: bool = False
    stripe_reader_id: str | None = None
    allow_cash_settlement: bool = Field(False)

    @validator("tax_default", pre=True, always=True)
    def default_tax_when_none(cls, v):
        return 0.0 if v is None else v


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
    inventory_omit_tags: str | None = None
    tip_options: str | None = None
    enable_receipt_print: bool
    enable_remote: bool = False
    business_name: str | None = None
    business_address: str | None = None
    business_vat_id: str | None = None
    only_show_sats_on_bitcoin: bool = True
    fiat_provider: str | None = None
    stripe_card_payments: bool = False
    stripe_reader_id: str | None = None
    allow_cash_settlement: bool = False

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


class PrintReceiptRequest(BaseModel):
    receipt_type: Literal["receipt", "order_receipt"] = "receipt"


class ReceiptItemData(BaseModel):
    title: str = ""
    note: str | None = None
    quantity: int = 0
    price: float = 0.0


class ReceiptDetailsData(BaseModel):
    currency: str = "sats"
    exchangeRate: float = 1.0
    taxValue: float = 0.0
    taxIncluded: bool = False
    items: list[ReceiptItemData] = Field(default_factory=list)


class ReceiptExtraData(BaseModel):
    amount: int = 0
    paid_in_fiat: bool = False
    fiat_method: str | None = None
    fiat_payment_request: str | None = None
    details: ReceiptDetailsData = Field(default_factory=ReceiptDetailsData)


class ReceiptData(BaseModel):
    paid: bool = False
    extra: ReceiptExtraData = Field(default_factory=ReceiptExtraData)
    created_at: Any = None
    business_name: str | None = None
    business_address: str | None = None
    business_vat_id: str | None = None
    only_show_sats_on_bitcoin: bool = True

    def paid_in_fiat(self) -> bool:
        return bool(
            self.extra.paid_in_fiat
            or self.extra.fiat_method
            or self.extra.fiat_payment_request
        )

    def show_bitcoin_details(self) -> bool:
        return (not self.only_show_sats_on_bitcoin) or (not self.paid_in_fiat())

    def subtotal(self) -> float:
        if self.extra.details.items:
            return sum(
                item.price * item.quantity for item in self.extra.details.items
            )
        rate = self.extra.details.exchangeRate or 1.0
        return self.extra.amount / rate

    def total(self) -> float:
        if not self.extra.details.items:
            rate = self.extra.details.exchangeRate or 1.0
            return self.extra.amount / rate
        if self.extra.details.taxIncluded:
            return self.subtotal()
        return self.subtotal() + self.extra.details.taxValue

    def format_money(self, amount: float) -> str:
        return f"{amount:.2f} {self.extra.details.currency.upper()}"

    def formatted_created_at(self) -> str | None:
        if not self.created_at:
            return None
        if isinstance(self.created_at, datetime):
            return self.created_at.strftime("%Y-%m-%d %H:%M")
        value = str(self.created_at).strip()
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value

    def render_text(
        self, receipt_type: Literal["receipt", "order_receipt"] = "receipt"
    ) -> str:
        lines: list[str] = []
        lines.append("ORDER" if receipt_type == "order_receipt" else "RECEIPT")
        formatted_created_at = self.formatted_created_at()
        if formatted_created_at:
            lines.append(formatted_created_at)
        if self.show_bitcoin_details() and receipt_type != "order_receipt":
            lines.append(
                f"Rate (sat/{self.extra.details.currency}): "
                f"{self.extra.details.exchangeRate:.2f}"
            )
        lines.append("")

        for item in self.extra.details.items:
            if item.title.strip():
                lines.append(item.title.strip())
            if receipt_type == "order_receipt":
                lines.append(f"Qty: {item.quantity}")
            else:
                lines.append(f"{item.quantity} x {self.format_money(item.price)}")
            if item.note and item.note.strip():
                lines.append(item.note.strip())
            lines.append("")

        if receipt_type != "order_receipt":
            lines.append(f"Subtotal: {self.format_money(self.subtotal())}")
            lines.append(f"Tax: {self.format_money(self.extra.details.taxValue)}")
            lines.append(f"Total: {self.format_money(self.total())}")
            if self.show_bitcoin_details():
                lines.append(f"Total (sats): {self.extra.amount}")
            lines.append("")
            lines.append("Thank you for your purchase!")

        if receipt_type != "order_receipt":
            if self.business_name:
                lines.append(self.business_name)
            if self.business_address:
                lines.extend(
                    line for line in self.business_address.splitlines() if line.strip()
                )
            if self.business_vat_id:
                lines.append(f"VAT: {self.business_vat_id}")

        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    def to_api_dict(self) -> dict[str, Any]:
        data = self.dict()
        data["print_text"] = self.render_text("receipt")
        data["order_print_text"] = self.render_text("order_receipt")
        return data


class ReceiptPrint(BaseModel):
    type: str = "receipt_print"
    tpos_id: str | None = None
    payment_hash: str | None = None
    receipt_type: Literal["receipt", "order_receipt"] = "receipt"
    print_text: str = ""
    receipt: dict[str, Any] = Field(default_factory=dict)


CreateTposInvoice.update_forward_refs(InventorySale=InventorySale)
