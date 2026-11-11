from http import HTTPStatus
from math import ceil, isfinite

from fastapi import HTTPException
from lnbits.core.crud import get_wallet
from lnbits.core.models import Payment
from lnbits.utils.exchange_rates import fiat_amount_as_satoshis

from .models import CreateTposInvoice, Tpos, TposPayment
from .services_tabs import (
    tabs_available_for_user,
)


def is_tpos_payment(payment: Payment, tpos: Tpos, checkout: TposPayment | None) -> bool:
    fiat_wallet_id = checkout.fiat_wallet_id if checkout else None
    return (
        payment.is_in
        and payment.extra.get("tag") == "tpos"
        and payment.extra.get("tpos_id") == tpos.id
        and (checkout is None or checkout.tpos_id == tpos.id)
        and payment.wallet_id == (fiat_wallet_id or tpos.wallet)
    )


async def get_tpos_owner_user_id(tpos: Tpos) -> str:
    wallet = await get_wallet(tpos.wallet)
    if not wallet:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="TPoS is not ready for tabs integration.",
        )
    return wallet.user


async def ensure_tpos_tabs_access(tpos: Tpos) -> str:
    if not tpos.tabs_enabled:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Tabs integration is not enabled for this TPoS.",
        )
    user_id = await get_tpos_owner_user_id(tpos)
    if not await tabs_available_for_user(user_id):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Tabs integration is unavailable for this TPoS.",
        )
    return user_id


async def tpos_fiat_as_satoshis(amount: float, currency: str) -> int:
    try:
        return await fiat_amount_as_satoshis(amount, currency)
    except ValueError as exc:
        raise HTTPException(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Exchange rate not available. Please try again later.",
        ) from exc


async def validate_tpos_sale_amount(
    tpos: Tpos, data: CreateTposInvoice, amount: float, *, minimum: bool = False
) -> None:
    if not isfinite(amount) or amount <= 0:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid sale amount.")
    if tpos.currency.lower() == "sats":
        expected = ceil(amount)
        if data.amount < expected if minimum else data.amount != expected:
            raise HTTPException(
                HTTPStatus.BAD_REQUEST, "Invoice amount does not match the sale."
            )
        return

    supplied = data.amount_fiat if data.amount_fiat is not None else amount
    if not minimum and abs(supplied - amount) > 0.01:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, "Invoice amount does not match the sale."
        )
    if minimum:
        amount = max(supplied, amount)
    data.amount = await tpos_fiat_as_satoshis(amount, tpos.currency)
    if data.amount <= 0:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Sale amount is below one satoshi.")
    data.amount_fiat = amount
    data.exchange_rate = data.amount / amount
    if data.details:
        data.details["exchangeRate"] = data.exchange_rate
