import asyncio
import httpx
from lnbits.helpers import create_access_token
from lnbits.core.models import Payment
from lnbits.core.crud import get_wallet
from lnbits.settings import settings
from lnbits.core.services import (
    create_invoice,
    get_pr_from_lnurl,
    pay_invoice,
    websocket_updater,
)
from lnbits.tasks import register_invoice_listener
from loguru import logger

from .crud import get_tpos

async def _deduct_inventory_stock(payment: Payment, inventory_payload: dict) -> None:
    wallet = await get_wallet(payment.wallet_id)
    if not wallet:
        return
    inventory_id = inventory_payload.get("inventory_id")
    if not inventory_id:
        return
    access = create_access_token({"usr": wallet.user}, token_expire_minutes=1)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/inventory/api/v1/item?item_id={inventory_id}",
            headers={"Authorization": f"Bearer {access}"},
        )
        resp.raise_for_status()
        item = resp.json()
        if not item["quantity_in_stock"] or item["quantity_in_stock"] <= 0:
            return
        new_quantity = item["quantity_in_stock"] - 1
        update_data = {"item_id": inventory_id, "quantity_in_stock": new_quantity}
        await client.put(
            url=f"http://{settings.host}:{settings.port}/inventory/api/v1/item/{inventory_id}",
            headers={"Authorization": f"Bearer {access}"},
            json=update_data,
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
        await _deduct_inventory_stock(payment, inventory_payload)

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
