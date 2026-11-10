from collections.abc import Awaitable, Callable

import httpx
from lnbits.core.models import Payment
from lnbits.settings import settings
from loguru import logger

from .helpers import create_internal_user_access_token
from .models import Tpos, TposPayment


async def push_order_to_orders(
    user_id: str,
    payment: Payment | TposPayment,
    tpos: Tpos,
    base_url: str | None = None,
    before_dispatch: Callable[[], Awaitable[None]] | None = None,
) -> None:
    onchain = isinstance(payment, TposPayment)
    memo: str | None
    if isinstance(payment, TposPayment):
        if not payment.onchain_sale:
            raise ValueError("Missing on-chain sale.")
        extra = payment.onchain_sale.extra
        checking_id = payment.payment_hash
        amount_msat = payment.amount * 1000
        fee_msat = 0
        memo = payment.onchain_sale.memo
        created_at = payment.created_at
    else:
        extra = payment.extra
        checking_id = payment.checking_id
        amount_msat = payment.amount
        fee_msat = payment.fee
        memo = payment.memo
        created_at = payment.time
    details = extra.get("details") or {}
    payload = {
        "source": "tpos",
        "tpos_id": extra.get("tpos_id"),
        "tpos_name": tpos.name if tpos else None,
        "payment_hash": payment.payment_hash,
        "checking_id": checking_id,
        "amount_msat": amount_msat,
        "fee_msat": fee_msat,
        "memo": memo,
        "paid_in_fiat": bool(extra.get("paid_in_fiat")),
        "currency": details.get("currency"),
        "exchange_rate": details.get("exchangeRate") or extra.get("exchangeRate"),
        "tax_included": details.get("taxIncluded"),
        "tax_value": details.get("taxValue"),
        "items": details.get("items") or [],
        "notes": extra.get("notes"),
        "created_at": created_at.isoformat(),
        "paid": True,
        "shipped": True,
    }

    access = create_internal_user_access_token(user_id)
    params = {}
    if base_url:
        params["base_url"] = base_url
    async with httpx.AsyncClient() as client:
        if before_dispatch:
            await before_dispatch()
        try:
            response = await client.post(
                url=f"http://{settings.host}:{settings.port}/orders/api/v1/orders",
                headers={"Authorization": f"Bearer {access}"},
                params=params,
                json=payload,
            )
            if before_dispatch:
                response.raise_for_status()
        except Exception as exc:
            if onchain:
                raise
            logger.warning(f"tpos: failed to push order to orders: {exc}")
