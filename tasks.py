import asyncio
import json
from collections.abc import Awaitable, Callable
from functools import partial
from math import ceil
from time import time
from typing import Any

from lnbits.core.crud import get_user_active_extensions_ids, get_wallet
from lnbits.core.crud.payments import get_standalone_payment, update_payment
from lnbits.core.models import Payment
from lnbits.core.services import (
    create_invoice,
    get_pr_from_lnurl,
    pay_invoice,
    websocket_updater,
)
from lnbits.settings import settings
from lnbits.tasks import register_invoice_listener
from loguru import logger

from .crud import (
    claim_fiat_checkout,
    claim_onchain_fulfilment,
    finish_fiat_checkout,
    get_pending_tpos_payments,
    get_tpos,
    get_tpos_payment_by_hash,
    record_onchain_balance,
    set_onchain_fulfilment,
    update_tpos_payment,
)
from .models import Tpos, TposPayment, TposPaymentStatus
from .services import ensure_tpos_tabs_access, is_tpos_payment
from .services_inventory import deduct_inventory_stock
from .services_onchain import fetch_onchain_balance
from .services_orders import push_order_to_orders
from .services_tabs import create_tab_settlement_for_tpos

tpos_invoice_queue: asyncio.Queue[Payment] = asyncio.Queue()


async def wait_for_paid_invoices():
    register_invoice_listener(tpos_invoice_queue, "ext_tpos")

    while True:
        payment = await tpos_invoice_queue.get()
        retryable = payment.fiat_provider or payment.checking_id.startswith(
            "internal_cash_"
        )
        for _ in range(2 if retryable else 1):
            try:
                await on_invoice_paid(payment)
                break
            except Exception:
                logger.exception(
                    "tpos: failed to process payment {}", payment.payment_hash
                )


async def poll_onchain_payments():
    while settings.lnbits_running:
        for checkout in await get_pending_tpos_payments():
            try:
                if not checkout.paid:
                    await check_onchain_payment(checkout)
                if checkout.paid:
                    await _fulfil_onchain_checkout(checkout)
            except Exception:
                logger.exception(
                    "tpos: failed to process on-chain sale {}", checkout.id
                )
        await asyncio.sleep(10)


async def check_onchain_payment(checkout: TposPayment) -> None:
    sale = checkout.onchain_sale
    if not sale or not checkout.onchain_address or not checkout.onchain_network:
        return
    balance = await fetch_onchain_balance(
        checkout.mempool_endpoint, checkout.onchain_address, checkout.onchain_network
    )
    confirmed = balance["confirmed"]
    pending = balance["unconfirmed"]
    received = confirmed + pending
    eligible = received if checkout.onchain_zero_conf else confirmed
    previous_received = checkout.balance
    if not checkout.onchain_zero_conf:
        previous_received += checkout.pending
    expired = time() >= sale.expires_at
    was_funded = previous_received >= checkout.amount
    status = TposPaymentStatus.PENDING
    if eligible >= checkout.amount and (not expired or was_funded):
        status = TposPaymentStatus.PAID
    elif expired and (received < checkout.amount or not was_funded):
        if not received:
            status = TposPaymentStatus.EXPIRED
        elif received < checkout.amount:
            status = TposPaymentStatus.UNDERPAID
        else:
            status = TposPaymentStatus.ABANDONED
    if (eligible, pending, status) == (
        checkout.balance,
        checkout.pending,
        checkout.status,
    ):
        return
    checkout.balance = eligible
    checkout.pending = pending
    checkout.status = status
    checkout.paid = status == TposPaymentStatus.PAID
    if not await record_onchain_balance(checkout):
        checkout.paid = False
        return
    payload = json.dumps(
        {
            "pending": not checkout.paid,
            "payment_hash": checkout.payment_hash,
            "onchain_balance": checkout.balance,
            "onchain_pending": checkout.pending,
            "payment_method": "onchain",
            "status": checkout.status,
        }
    )
    await websocket_updater(checkout.payment_hash, payload)
    if checkout.paid:
        await websocket_updater(checkout.tpos_id, payload)


async def on_invoice_paid(payment: Payment) -> None:
    if (
        not payment.extra
        or payment.extra.get("tag") != "tpos"
        or payment.extra.get("tipSplitted")
        or payment.checking_id.startswith("internal_onchain_")
        or not payment.success
        or not payment.is_in
    ):
        return

    checkout = await get_tpos_payment_by_hash(payment.payment_hash)
    if not checkout:
        return
    tpos = await get_tpos(checkout.tpos_id)
    if not tpos or not is_tpos_payment(payment, tpos, checkout):
        return

    is_fiat = bool(checkout.fiat_wallet_id)
    if is_fiat and not await claim_fiat_checkout(checkout.id):
        return

    finished = False
    try:
        stored_payment = await get_standalone_payment(
            payment.payment_hash, incoming=True
        )
        if (
            not stored_payment
            or not stored_payment.success
            or not is_tpos_payment(stored_payment, tpos, checkout)
        ):
            return

        payment_method = (
            "fiat"
            if is_fiat and stored_payment.fiat_provider
            else _payment_method(stored_payment)
        )
        if not stored_payment.extra.get("tpos_processed"):
            if not is_fiat and not checkout.paid:
                checkout.paid = True
                checkout.status = TposPaymentStatus.PAID
                checkout.payment_method = payment_method
                await update_tpos_payment(checkout)
            await process_paid_tpos_payment(
                stored_payment, tpos, checkout, payment_method
            )
        if is_fiat:
            await finish_fiat_checkout(checkout.id, payment_method)
            finished = True
    finally:
        if is_fiat and not finished:
            await finish_fiat_checkout(checkout.id)


async def process_paid_tpos_payment(
    payment: Payment, tpos: Tpos, checkout: TposPayment, payment_method: str
) -> None:
    if (
        not payment.extra
        or payment.extra.get("tag") != "tpos"
        or payment.extra.get("tipSplitted")
    ):
        return

    payment.extra["payment_method"] = payment_method
    if not checkout.fiat_wallet_id:
        payment.extra["tpos_processed"] = True
        await update_payment(payment)

    tip_amount = payment.extra.get("tip_amount")
    tpos_id = payment.extra.get("tpos_id")
    assert tpos_id

    stripped_payment = {
        "amount": payment.amount,
        "fee": payment.fee,
        "checking_id": payment.checking_id,
        "payment_hash": payment.payment_hash,
        "bolt11": "" if checkout.fiat_wallet_id else payment.bolt11,
        "pending": False,
        "payment_method": payment_method,
    }

    if not checkout.fiat_wallet_id and payment.extra.get("lnaddress"):
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

    if checkout.fiat_wallet_id:
        await _fulfil_fiat_checkout(payment, tpos)
        await maybe_settle_tab(payment, tpos, payment_method, checkout)
        await _record_fiat_progress(payment, "tpos_processed")
        return

    await maybe_settle_tab(payment, tpos, payment_method, checkout)
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


async def _fulfil_fiat_checkout(payment: Payment, tpos: Tpos) -> None:
    if not payment.extra.get("tpos_order_dispatched"):
        await maybe_push_order(
            payment,
            tpos,
            partial(_record_fiat_progress, payment, "tpos_order_dispatched"),
        )
    inventory = payment.extra.get("inventory")
    if inventory and not payment.extra.get("tpos_inventory_dispatched"):
        await deduct_inventory_stock(
            payment.wallet_id,
            inventory,
            partial(_record_fiat_progress, payment, "tpos_inventory_dispatched"),
        )


async def _record_fiat_progress(payment: Payment, step: str) -> None:
    # Orders and Inventory cannot deduplicate requests with an ambiguous outcome.
    payment.extra[step] = True
    await update_payment(payment.copy(include={"wallet_id", "checking_id", "extra"}))


async def maybe_settle_tab(
    payment: Payment,
    tpos: Tpos,
    payment_method: str,
    checkout: TposPayment | None = None,
) -> None:
    settlement = (payment.extra or {}).get("tab_settlement")
    if not settlement:
        return
    amount_msat = settlement.get("amount_msat")
    if amount_msat is None and tpos.currency.lower() == "sats":
        amount_msat = (
            ceil(settlement["amount"]) + (payment.extra.get("tip_amount") or 0)
        ) * 1000
    # Pre-update fiat invoices retain their original settlement behavior.
    if (
        not payment.success
        or not is_tpos_payment(payment, tpos, checkout)
        or (
            amount_msat is not None
            and (
                amount_msat <= 0
                or (
                    payment.amount != amount_msat
                    if "amount_msat" in settlement
                    else payment.amount < amount_msat
                )
            )
        )
    ):
        logger.warning(
            "tpos: refusing unverified tab settlement for {}", payment.payment_hash
        )
        return

    try:
        await _settle_tab(
            tpos,
            settlement,
            payment.payment_hash,
            payment_method,
            _tabs_settlement_method(payment_method, payment),
        )
    except Exception as exc:
        if checkout and checkout.fiat_wallet_id:
            raise
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
    if payment.extra.get("fiat_method") == "cash":
        return "cash"
    if payment.extra.get("fiat_payment_request", "").startswith("pi_"):
        return "fiat"
    return "lightning"


async def maybe_push_order(
    payment: Payment | TposPayment,
    tpos: Tpos,
    before_dispatch: Callable[[], Awaitable[None]] | None = None,
) -> None:
    if isinstance(payment, TposPayment):
        if not payment.onchain_sale:
            raise ValueError("Missing on-chain sale.")
        wallet_id = payment.onchain_sale.wallet_id
        extra = payment.onchain_sale.extra
    else:
        wallet_id = payment.wallet_id
        extra = payment.extra
    wallet = await get_wallet(wallet_id)
    if not wallet:
        if isinstance(payment, TposPayment):
            raise ValueError("Missing on-chain sale owner wallet.")
        return

    active_extensions = await get_user_active_extensions_ids(wallet.user)
    if "orders" not in active_extensions:
        return

    await push_order_to_orders(
        wallet.user,
        payment,
        tpos,
        base_url=extra.get("base_url"),
        before_dispatch=before_dispatch,
    )


async def _settle_tab(
    tpos: Tpos,
    settlement: dict[str, Any],
    payment_hash: str,
    payment_method: str,
    tab_method: str,
) -> None:
    user_id = await ensure_tpos_tabs_access(tpos)
    await create_tab_settlement_for_tpos(
        user_id=user_id,
        tab_id=settlement["tab_id"],
        payload={
            "amount": settlement["amount"],
            "method": tab_method,
            "reference": settlement.get("reference"),
            "description": settlement.get("description") or "TPoS settlement",
            "metadata": json.dumps(
                {
                    "source": "tpos",
                    "source_id": tpos.id,
                    "source_action": "settlement_paid",
                    "payment_hash": payment_hash,
                    "payment_method": payment_method,
                }
            ),
            "idempotency_key": (
                f"tpos:{payment_hash}"
                if "amount_msat" in settlement
                else settlement["idempotency_key"]
            ),
        },
    )


async def _fulfil_onchain_checkout(checkout: TposPayment) -> None:
    sale = checkout.onchain_sale
    if not sale or not await claim_onchain_fulfilment(checkout.id):
        return
    try:
        tpos = await get_tpos(checkout.tpos_id)
        if not tpos or tpos.wallet != sale.wallet_id:
            raise ValueError("On-chain sale terminal is unavailable.")
        settlement = sale.extra.get("tab_settlement")
        if settlement:
            if settlement.get("amount_msat") != checkout.amount * 1000:
                raise ValueError("On-chain tab settlement amount mismatch.")
            await _settle_tab(
                tpos, settlement, checkout.payment_hash, "onchain", "other"
            )

        # A worker that stops during either dispatch requires operator reconciliation.
        await maybe_push_order(
            checkout, tpos, partial(set_onchain_fulfilment, checkout.id, "order")
        )
        inventory = sale.extra.get("inventory")
        if inventory:
            await deduct_inventory_stock(
                sale.wallet_id,
                inventory,
                partial(set_onchain_fulfilment, checkout.id, "inventory"),
            )
        await set_onchain_fulfilment(checkout.id, "complete")
    except Exception:
        await set_onchain_fulfilment(checkout.id, "pending", expected="processing")
        raise
