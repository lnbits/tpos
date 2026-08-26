import asyncio
import json

from lnbits.core.crud import get_user_active_extensions_ids, get_wallet
from lnbits.core.crud.payments import get_standalone_payment, update_payment
from lnbits.core.models import Payment, PaymentState
from lnbits.core.services import (
    create_invoice,
    get_pr_from_lnurl,
    pay_invoice,
    websocket_updater,
)
from lnbits.tasks import internal_invoice_queue_put, register_invoice_listener
from loguru import logger

from .crud import (
    get_pending_tpos_payments,
    get_tpos,
    get_tpos_payment_by_hash,
    update_tpos_payment,
)
from .helpers import INTERNAL_FIAT_METHODS
from .services import ensure_tpos_tabs_access
from .services_inventory import deduct_inventory_stock
from .services_onchain import fetch_onchain_balance
from .services_orders import push_order_to_orders
from .services_tabs import create_tab_settlement_for_tpos


async def wait_for_paid_invoices():
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "ext_tpos")

    while True:
        payment = await invoice_queue.get()
        await on_invoice_paid(payment)


async def poll_onchain_payments():
    while True:
        pending_payments = await get_pending_tpos_payments()
        for tpos_payment in pending_payments:
            if not tpos_payment.onchain_address or not tpos_payment.mempool_endpoint:
                continue
            try:
                balance = await fetch_onchain_balance(
                    tpos_payment.mempool_endpoint, tpos_payment.onchain_address
                )
                confirmed_balance = int(balance["confirmed"])
                unconfirmed_balance = int(balance["unconfirmed"])
                settled_balance = (
                    confirmed_balance + unconfirmed_balance
                    if tpos_payment.onchain_zero_conf
                    else confirmed_balance
                )
                changed = (
                    tpos_payment.balance != settled_balance
                    or tpos_payment.pending != unconfirmed_balance
                )
                tpos_payment.balance = settled_balance
                tpos_payment.pending = unconfirmed_balance
                settled = settled_balance >= tpos_payment.amount
                if settled:
                    tpos_payment.payment_method = "onchain"
                if changed or settled:
                    await update_tpos_payment(tpos_payment)
                    await websocket_updater(
                        tpos_payment.payment_hash,
                        json.dumps(
                            {
                                "pending": not tpos_payment.paid,
                                "payment_hash": tpos_payment.payment_hash,
                                "onchain_balance": tpos_payment.balance,
                                "onchain_pending": tpos_payment.pending,
                                "payment_method": tpos_payment.payment_method,
                            }
                        ),
                    )
                if settled:
                    await settle_onchain_tpos_payment(tpos_payment)
            except Exception as exc:
                logger.warning(f"tpos: onchain polling failed: {exc}")
        await asyncio.sleep(10)


async def on_invoice_paid(payment: Payment) -> None:
    if (
        not payment.extra
        or payment.extra.get("tag") != "tpos"
        or payment.extra.get("tipSplitted")
    ):
        return

    payment_method = payment.extra.get("payment_method") or _payment_method(payment)
    tpos_payment = await get_tpos_payment_by_hash(payment.payment_hash)
    if tpos_payment and not tpos_payment.paid:
        tpos_payment.paid = True
        tpos_payment.payment_method = payment_method
        await update_tpos_payment(tpos_payment)

    if payment.extra.get("tpos_processed"):
        return

    await process_paid_tpos_payment(payment, payment_method=payment_method)


async def settle_onchain_tpos_payment(tpos_payment) -> None:
    payment = await get_standalone_payment(tpos_payment.payment_hash, incoming=True)
    if not payment or not payment.extra or payment.extra.get("tag") != "tpos":
        return

    payment.extra["payment_method"] = "onchain"
    payment.extra["settled_by_onchain"] = True
    if not payment.success:
        payment.status = PaymentState.SUCCESS
        await update_payment(payment)
    await internal_invoice_queue_put(payment.checking_id)


async def process_paid_tpos_payment(
    payment: Payment, *, payment_method: str = "lightning"
) -> None:
    if (
        not payment.extra
        or payment.extra.get("tag") != "tpos"
        or payment.extra.get("tipSplitted")
    ):
        return

    payment.extra["tpos_processed"] = True
    payment.extra["payment_method"] = payment_method
    await update_payment(payment)

    tip_amount = payment.extra.get("tip_amount")
    tpos_id = payment.extra.get("tpos_id")
    assert tpos_id

    stripped_payment = {
        "amount": payment.amount,
        "fee": payment.fee,
        "checking_id": payment.checking_id,
        "payment_hash": payment.payment_hash,
        "bolt11": payment.bolt11,
        "pending": False,
        "payment_method": payment_method,
    }

    tpos = await get_tpos(tpos_id)
    assert tpos
    if payment.extra.get("lnaddress") and payment.extra["lnaddress"] != "":
        calc_amount = payment.amount - ((payment.amount / 100) * tpos.lnaddress_cut)
        address = payment.extra.get("lnaddress")
        if address:
            try:
                pr = await get_pr_from_lnurl(address, int(calc_amount // 1000) * 1000)
            except Exception as exc:
                logger.error(f"tpos: Error getting payment request from lnurl: {exc}")
                pr = None

            if pr:
                payment.extra["lnaddress"] = ""
                paid_payment = await pay_invoice(
                    payment_request=pr,
                    wallet_id=payment.wallet_id,
                    extra={**payment.extra},
                )
                logger.debug(f"tpos: LNaddress paid cut: {paid_payment.checking_id}")

    await websocket_updater(tpos_id, json.dumps(stripped_payment))
    await websocket_updater(payment.payment_hash, json.dumps(stripped_payment))

    await maybe_settle_tab(payment, tpos, payment_method)
    await maybe_push_order(payment, tpos)

    inventory_payload = payment.extra.get("inventory")
    if inventory_payload:
        try:
            await deduct_inventory_stock(payment.wallet_id, inventory_payload)
        except Exception as exc:
            logger.warning(f"tpos: inventory deduction failed: {exc}")

    if not tip_amount:
        return

    wallet_id = tpos.tip_wallet
    if not wallet_id:
        return

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


async def maybe_settle_tab(payment: Payment, tpos, payment_method: str) -> None:
    settlement = (payment.extra or {}).get("tab_settlement")
    if not settlement:
        return

    try:
        user_id = await ensure_tpos_tabs_access(tpos)
        await create_tab_settlement_for_tpos(
            user_id=user_id,
            tab_id=settlement["tab_id"],
            payload={
                "amount": settlement["amount"],
                "method": _tabs_settlement_method(payment_method, payment),
                "reference": settlement.get("reference"),
                "description": settlement.get("description") or "TPoS settlement",
                "metadata": json.dumps(
                    {
                        "source": "tpos",
                        "source_id": tpos.id,
                        "source_action": "settlement_paid",
                        "payment_hash": payment.payment_hash,
                        "payment_method": payment_method,
                    }
                ),
                "idempotency_key": settlement["idempotency_key"],
            },
        )
    except Exception as exc:
        logger.warning(f"tpos: tab settlement failed: {exc}")


def _tabs_settlement_method(payment_method: str, payment: Payment) -> str:
    if payment_method == "cash":
        return "cash"
    if payment.extra.get("fiat_method") == "terminal":
        return "card"
    return "other"


def _payment_method(payment: Payment) -> str:
    if payment.extra.get("payment_method"):
        return str(payment.extra["payment_method"])
    if payment.extra.get("fiat_method") in INTERNAL_FIAT_METHODS:
        return str(payment.extra["fiat_method"])
    if payment.extra.get("fiat_payment_request", "").startswith("pi_"):
        return "fiat"
    return "lightning"


async def maybe_push_order(payment: Payment, tpos) -> None:
    wallet = await get_wallet(payment.wallet_id)
    if not wallet:
        return

    active_extensions = await get_user_active_extensions_ids(wallet.user)
    if "orders" not in active_extensions:
        return

    await push_order_to_orders(
        wallet.user,
        payment,
        tpos,
        base_url=payment.extra.get("base_url"),
    )
