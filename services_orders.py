import httpx
from lnbits.settings import settings
from loguru import logger

from .helpers import create_internal_user_access_token


async def push_order_to_orders(
    user_id: str,
    payment,
    tpos,
    base_url: str | None = None,
) -> None:
    details = payment.extra.get("details") or {}
    payload = {
        "source": "tpos",
        "tpos_id": payment.extra.get("tpos_id"),
        "tpos_name": tpos.name if tpos else None,
        "payment_hash": payment.payment_hash,
        "checking_id": payment.checking_id,
        "amount_msat": payment.amount,
        "fee_msat": payment.fee,
        "memo": payment.memo,
        "paid_in_fiat": bool(payment.extra.get("paid_in_fiat")),
        "currency": details.get("currency"),
        "exchange_rate": details.get("exchangeRate")
        or payment.extra.get("exchangeRate"),
        "tax_included": details.get("taxIncluded"),
        "tax_value": details.get("taxValue"),
        "items": details.get("items") or [],
        "notes": payment.extra.get("notes"),
        "created_at": payment.time.isoformat() if payment.time else None,
        "paid": True,
        "shipped": True,
    }

    access = create_internal_user_access_token(user_id)
    params = {}
    if base_url:
        params["base_url"] = base_url
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                url=f"http://{settings.host}:{settings.port}/orders/api/v1/orders",
                headers={"Authorization": f"Bearer {access}"},
                params=params,
                json=payload,
            )
        except Exception as exc:
            logger.warning(f"tpos: failed to push order to orders: {exc}")
