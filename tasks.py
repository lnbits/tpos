import asyncio

import httpx
from lnbits.core.crud import get_wallet
from lnbits.core.models import Payment
from lnbits.core.services import (
    create_invoice,
    get_pr_from_lnurl,
    pay_invoice,
    websocket_updater,
)
from lnbits.helpers import create_access_token
from lnbits.settings import settings
from lnbits.tasks import register_invoice_listener
from loguru import logger

from .crud import get_tpos


async def _deduct_inventory_stock(wallet_id: str, inventory_payload: dict) -> None:
    wallet = await get_wallet(wallet_id)
    if not wallet:
        return
    inventory_id = inventory_payload.get("inventory_id")
    items = inventory_payload.get("items") or []
    if not inventory_id or not items:
        return
    ids: list[str] = []
    quantities: list[int] = []
    for item in items:
        item_id = item.get("id")
        qty = item.get("quantity") or 0
        if not item_id or qty <= 0:
            continue
        ids.append(item_id)
        quantities.append(int(qty))
    if not ids:
        return

    access = create_access_token(
        {"sub": "", "usr": wallet.user}, token_expire_minutes=1
    )
    async with httpx.AsyncClient() as client:
        await client.patch(
            url=f"http://{settings.host}:{settings.port}/inventory/api/v1/items/{inventory_id}/quantities",
            headers={"Authorization": f"Bearer {access}"},
            params={"source": "tpos", "ids": ids, "quantities": quantities},
        )
    return


async def wait_for_paid_invoices():
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "ext_tpos")

    while True:
        payment = await invoice_queue.get()
        await on_invoice_paid(payment)


async def on_invoice_paid(payment: Payment) -> None:
    if (
        not payment.extra
        or payment.extra.get("tag") != "tpos"
        or payment.extra.get("tipSplitted")
    ):
        return
    tip_amount = payment.extra.get("tip_amount")

    stripped_payment = {
        "amount": payment.amount,
        "fee": payment.fee,
        "checking_id": payment.checking_id,
        "payment_hash": payment.payment_hash,
        "bolt11": payment.bolt11,
    }

    tpos_id = payment.extra.get("tpos_id")
    assert tpos_id

    tpos = await get_tpos(tpos_id)
    assert tpos
    if payment.extra.get("lnaddress") and payment.extra["lnaddress"] != "":
        calc_amount = payment.amount - ((payment.amount / 100) * tpos.lnaddress_cut)
        address = payment.extra.get("lnaddress")
        if address:
            try:
                pr = await get_pr_from_lnurl(address, int(calc_amount))
            except Exception as e:
                logger.error(f"tpos: Error getting payment request from lnurl: {e}")
                return

            payment.extra["lnaddress"] = ""
            paid_payment = await pay_invoice(
                payment_request=pr,
                wallet_id=payment.wallet_id,
                extra={**payment.extra},
            )
            logger.debug(f"tpos: LNaddress paid cut: {paid_payment.checking_id}")

    await websocket_updater(tpos_id, str(stripped_payment))

    inventory_payload = payment.extra.get("inventory")
    if inventory_payload:
        await _deduct_inventory_stock(payment.wallet_id, inventory_payload)

    if not tip_amount:
        # no tip amount
        return

    wallet_id = tpos.tip_wallet
    assert wallet_id

    tip_payment = await create_invoice(
        wallet_id=wallet_id,
        amount=int(tip_amount),
        internal=True,
        memo="tpos tip",
    )
    logger.debug(f"tpos: tip invoice created: {payment.payment_hash}")

    paid_payment = await pay_invoice(
        payment_request=tip_payment.bolt11,
        wallet_id=payment.wallet_id,
        extra={**payment.extra, "tipSplitted": True},
    )
    logger.debug(f"tpos: tip invoice paid: {paid_payment.checking_id}")
