import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from lnbits.core.crud import get_standalone_payment
from lnbits.core.crud.payments import update_payment
from lnbits.core.crud.wallets import create_wallet, delete_wallet, get_wallet
from lnbits.core.db import db as core_db
from lnbits.core.models import Payment, PaymentState
from lnbits.core.models.wallets import WalletType
from lnbits.core.services import fiat_providers, payments
from lnbits.fiat.base import FiatInvoiceResponse, FiatPaymentStatus
from lnbits.task_manager import task_manager

import tpos.tasks as tasks  # type: ignore[import]
import tpos.views_payments as views_payments  # type: ignore[import]
from tpos.crud import (  # type: ignore[import]
    db,
    get_latest_tpos_payments,
    get_tpos,
    get_tpos_payment_by_hash,
)
from tpos.models import TposClean  # type: ignore[import]

from .test_api import _tpos_payload, _user_with_tabs

FIAT_SALE = {"amount": 1000, "amount_fiat": 1, "pay_in_fiat": True}


@pytest.fixture
def checkout_db_locks(monkeypatch):
    # LNbits database locks are global, but pytest uses one event loop per test.
    for database in (db, core_db):
        monkeypatch.setattr(database, "lock", asyncio.Lock())


async def _terminal(client, wallet, **settings):
    response = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(currency="EUR", **settings),
        headers={"X-API-KEY": wallet.adminkey},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _cash_checkout(client, username):
    user, wallet = await _user_with_tabs(username)
    fiat = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    tpos = await _terminal(
        client, wallet, allow_cash_settlement=True, fiat_wallet_id=fiat.id
    )
    base = f"/tpos/api/v1/tposs/{tpos['id']}"
    sale = await client.post(
        f"{base}/invoices", json={**FIAT_SALE, "fiat_method": "cash"}
    )
    assert sale.is_success, sale.text
    return user, wallet, fiat, base, sale.json()["payment_hash"]


@pytest.mark.asyncio
async def test_cash_requires_explicit_owned_fiat_wallet(client):
    user, wallet = await _user_with_tabs("fiat_cash_setup")
    other_user, _ = await _user_with_tabs("fiat_cash_foreign")
    wrong_currency = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="USD"
    )
    foreign = await create_wallet(
        user_id=other_user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    owned = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    assert not user.super_user
    headers = {"X-API-KEY": wallet.adminkey}
    for wallet_id, status in (
        (None, 400),
        (wallet.id, 400),
        (wrong_currency.id, 400),
        (foreign.id, 403),
    ):
        response = await client.post(
            "/tpos/api/v1/tposs",
            json=_tpos_payload(
                currency="EUR", allow_cash_settlement=True, fiat_wallet_id=wallet_id
            ),
            headers=headers,
        )
        assert response.status_code == status, response.text
    tpos = await _terminal(
        client, wallet, allow_cash_settlement=True, fiat_wallet_id=owned.id
    )
    assert tpos["fiat_wallet_id"] == owned.id
    public = TposClean.model_validate(tpos).model_dump_json()
    assert owned.id not in public and owned.adminkey not in public


@pytest.mark.asyncio
async def test_cash_confirmation_rejects_foreign_terminal_and_deleted_wallet(
    client, fixed_fiat_rate
):
    user, wallet, fiat, base, payment_hash = await _cash_checkout(
        client, "fiat_cash_binding"
    )
    other = await _terminal(
        client, wallet, allow_cash_settlement=True, fiat_wallet_id=fiat.id
    )
    base += "/invoices"
    suffix = f"invoices/{payment_hash}"
    other_base = f"/tpos/api/v1/tposs/{other['id']}/{suffix}"
    assert (await client.get(other_base)).status_code == 404
    assert (await client.post(f"{other_base}/cash/validate")).status_code == 404
    assert (
        await client.post(f"{other_base}/print", json={"receipt_type": "receipt"})
    ).status_code == 404

    await delete_wallet(user.id, fiat.id)
    assert (
        await client.post(f"{base}/{payment_hash}/cash/validate")
    ).status_code == 409
    assert (
        await client.post(base, json={**FIAT_SALE, "fiat_method": "cash"})
    ).status_code == 409
    payment = await get_standalone_payment(payment_hash, incoming=True)
    assert payment and payment.pending


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination", ["unbound", "missing", "wrong_currency", "foreign"]
)
async def test_provider_checkout_cannot_fall_back_to_lightning(
    client, enable_stripe, monkeypatch, destination
):
    user, wallet = await _user_with_tabs(f"fiat_{destination}")
    enable_stripe(user.id)
    tpos = await _terminal(client, wallet)
    wallet_id = None
    if destination == "missing":
        wallet_id = uuid4().hex
    elif destination == "wrong_currency":
        wallet_id = (
            await create_wallet(
                user_id=user.id, wallet_type=WalletType.FIAT, currency="USD"
            )
        ).id
    elif destination == "foreign":
        other, _ = await _user_with_tabs("fiat_other_owner")
        wallet_id = (
            await create_wallet(
                user_id=other.id, wallet_type=WalletType.FIAT, currency="EUR"
            )
        ).id
    await db.execute(
        "UPDATE tpos.pos SET fiat_provider = 'stripe', fiat_wallet_id = :wallet "
        "WHERE id = :id",
        {"wallet": wallet_id, "id": tpos["id"]},
    )
    create = AsyncMock()
    monkeypatch.setattr(views_payments, "create_payment_request", create)
    response = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices", json=FIAT_SALE
    )
    assert response.status_code == 409, response.text
    create.assert_not_awaited()
    assert not await get_latest_tpos_payments(tpos["id"])


@pytest.mark.asyncio
async def test_verified_card_event_uses_checkout_wallet_and_fulfils_once(
    client, enable_stripe, fixed_fiat_rate, monkeypatch, checkout_db_locks
):
    user, wallet = await _user_with_tabs("fiat_card_event")
    fiat = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    second = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    headers = {"X-API-KEY": wallet.adminkey}
    denied = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(
            currency="EUR", fiat_provider="stripe", fiat_wallet_id=fiat.id
        ),
        headers=headers,
    )
    assert denied.status_code == 400
    enable_stripe(user.id)
    tpos = await _terminal(
        client,
        wallet,
        fiat_provider="stripe",
        fiat_wallet_id=fiat.id,
        tabs_enabled=True,
        tabs_allow_create=True,
    )
    base = f"/tpos/api/v1/tposs/{tpos['id']}"
    tab = (await client.post(f"{base}/tabs", json={"name": "Card customer"})).json()
    charge = await client.post(
        f"{base}/tabs/{tab['id']}/charges",
        json={"amount": 1, "idempotency_key": "fiat-card-charge"},
    )
    assert charge.is_success, charge.text
    provider = AsyncMock()
    provider.create_invoice.return_value = FiatInvoiceResponse(
        ok=True, checking_id="pi_sale", payment_request="https://example.com/sale"
    )
    provider.get_invoice_status.side_effect = [
        FiatPaymentStatus(paid=False),
        FiatPaymentStatus(paid=True),
    ]
    for module in (payments, fiat_providers):
        monkeypatch.setattr(
            module, "get_fiat_provider", AsyncMock(return_value=provider)
        )
    monkeypatch.setattr(task_manager, "internal_invoice_queue", asyncio.Queue())
    replay: asyncio.Queue = asyncio.Queue()
    monkeypatch.setattr(views_payments, "tpos_invoice_queue", replay)
    order = AsyncMock()
    monkeypatch.setattr(tasks, "maybe_push_order", order)
    payout = AsyncMock()
    monkeypatch.setattr(tasks, "get_pr_from_lnurl", payout)
    monkeypatch.setattr(tasks, "pay_invoice", payout)
    create_request = views_payments.create_payment_request

    async def pay_during_creation(wallet_id, invoice_data):
        payment = await create_request(wallet_id, invoice_data)
        assert not (await fiat_providers.check_fiat_status(payment)).success
        assert task_manager.internal_invoice_queue.empty()
        assert (await fiat_providers.check_fiat_status(payment)).success
        event = task_manager.internal_invoice_queue.get_nowait()
        await tasks.on_invoice_paid(event)
        order.assert_not_awaited()
        return payment

    monkeypatch.setattr(views_payments, "create_payment_request", pay_during_creation)
    response = await client.post(
        f"{base}/invoices",
        json={
            **FIAT_SALE,
            "tip_amount": 100,
            "tip_amount_fiat": 0.1,
            "user_lnaddress": "alice@example.com",
            "tab_settlement": {
                "tab_id": tab["id"],
                "amount": 1,
                "idempotency_key": "fiat-card-settlement",
            },
        },
    )
    assert response.status_code == 201, response.text
    assert not response.json()["bolt11"]
    payment_hash = response.json()["payment_hash"]
    event = replay.get_nowait()
    assert event.wallet_id == fiat.id
    response = await client.put(
        base,
        json=_tpos_payload(fiat_wallet_id=second.id, currency="EUR"),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    checkout = await get_tpos_payment_by_hash(payment_hash)
    assert checkout and not checkout.paid
    terminal = await get_tpos(tpos["id"])
    assert terminal and terminal.fiat_wallet_id == second.id
    await tasks.maybe_settle_tab(
        event.model_copy(update={"amount": event.amount - 1000}),
        terminal,
        "fiat",
        checkout,
    )
    outstanding = (await client.get(f"{base}/tabs")).json()["data"]
    assert outstanding[0]["balance"] == 1
    await asyncio.gather(
        *(tasks.on_invoice_paid(event.model_copy(deep=True)) for _ in range(2))
    )
    order.assert_awaited_once()
    payout.assert_not_awaited()
    receipt = await client.get(f"{base}/invoices/{payment_hash}?extra=true")
    assert receipt.status_code == 200 and receipt.json()["paid"]
    assert (await client.get(f"{base}/tabs")).json()["data"] == []
    checkout = (await get_latest_tpos_payments(tpos["id"]))[0]
    assert checkout.paid and checkout.payment_method == "fiat"
    lightning = await get_wallet(wallet.id)
    credited = await get_wallet(fiat.id)
    untouched = await get_wallet(second.id)
    assert lightning and lightning.balance_msat == wallet.balance_msat
    assert credited and credited.balance_msat == event.amount + event.fee
    assert untouched and untouched.balance_msat == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["bound", "lightning"])
async def test_cash_confirmation_keeps_original_destination(
    client, fixed_fiat_rate, monkeypatch, binding
):
    user, wallet, original, base, payment_hash = await _cash_checkout(
        client, f"switch_{binding}"
    )
    replacement = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="USD"
    )
    if binding == "lightning":
        await db.execute(
            "UPDATE tpos.payments SET fiat_wallet_id = NULL WHERE payment_hash = :hash",
            {"hash": payment_hash},
        )
        await core_db.execute(
            "UPDATE apipayments SET wallet_id = :wallet WHERE payment_hash = :hash",
            {"hash": payment_hash, "wallet": wallet.id},
        )
    switched = await client.put(
        base,
        json={"currency": "USD", "fiat_wallet_id": replacement.id},
        headers={"X-API-KEY": wallet.adminkey},
    )
    assert switched.is_success, switched.text
    queued = AsyncMock()
    monkeypatch.setattr(views_payments, "internal_invoice_queue_put", queued)
    confirmed = await client.post(f"{base}/invoices/{payment_hash}/cash/validate")
    payment = await get_standalone_payment(payment_hash, incoming=True)
    assert payment
    if binding == "lightning":
        assert confirmed.status_code == 409 and payment.pending
        queued.assert_not_awaited()
        return
    assert confirmed.is_success, confirmed.text
    assert payment.success and payment.wallet_id == original.id
    await tasks.on_invoice_paid(payment)
    checkout = await get_tpos_payment_by_hash(payment_hash)
    assert checkout and checkout.paid and checkout.fiat_wallet_id == original.id
    credited = await get_wallet(original.id)
    untouched = await get_wallet(replacement.id)
    assert credited and credited.balance_msat == 1_000_000
    assert untouched and untouched.balance_msat == 0


@pytest.mark.asyncio
async def test_cash_confirmation_distinguishes_failed_from_expired(
    client, fixed_fiat_rate, monkeypatch
):
    _, _, _, base, payment_hash = await _cash_checkout(client, "cash_failed_state")
    payment = await get_standalone_payment(payment_hash, incoming=True)
    assert payment
    url = f"{base}/invoices/{payment_hash}/cash/validate"
    monkeypatch.setattr(Payment, "is_expired", property(lambda _: True))
    expired = await client.post(url)
    assert expired.status_code == 409 and "has expired" in expired.json()["detail"]

    payment.status = PaymentState.FAILED
    await update_payment(payment)
    monkeypatch.setattr(Payment, "is_expired", property(lambda _: False))
    failed = await client.post(url)
    assert failed.status_code == 409 and "cannot be settled" in failed.json()["detail"]
    stored = await get_standalone_payment(payment_hash, incoming=True)
    assert stored and stored.failed


@pytest.mark.asyncio
async def test_update_rejects_ineligible_primary_wallet(client):
    user, wallet = await _user_with_tabs("fiat_primary_update")
    fiat = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    tpos = await _terminal(client, wallet)
    await db.execute(
        "UPDATE tpos.pos SET wallet = :wallet WHERE id = :id",
        {"wallet": fiat.id, "id": tpos["id"]},
    )
    update = await client.put(
        f"/tpos/api/v1/tposs/{tpos['id']}",
        json={"name": "Invalid terminal update"},
        headers={"X-API-KEY": fiat.adminkey},
    )
    assert update.status_code == 400
    stored = await get_tpos(tpos["id"])
    assert stored and stored.name == tpos["name"]


@pytest.mark.asyncio
async def test_cash_retry_after_tabs_failure_does_not_repeat_dispatches(
    client, fixed_fiat_rate, monkeypatch, checkout_db_locks
):
    _, _, fiat, base, payment_hash = await _cash_checkout(client, "cash_fulfil_retry")
    pending = await get_standalone_payment(payment_hash, incoming=True)
    assert pending
    pending.extra["inventory"] = {"inventory_id": "inventory", "items": []}
    await update_payment(pending)

    queued, order_sent, inventory_sent = AsyncMock(), AsyncMock(), AsyncMock()
    settle_tab = AsyncMock(side_effect=[RuntimeError("Tabs unavailable"), None])
    first_worker_ready, competing, released = (asyncio.Event() for _ in range(3))
    original_claim = tasks.claim_fiat_checkout

    async def claim(checkout_id):
        worker = asyncio.current_task()
        if worker and worker.get_name() == "competing-fiat-checkout":
            competing.set()
            await released.wait()
        return await original_claim(checkout_id)

    async def notify(*args):
        if not first_worker_ready.is_set():
            first_worker_ready.set()
            await competing.wait()

    async def push_order(_payment, _tpos, before_dispatch):
        await before_dispatch()
        await order_sent()

    async def deduct_stock(_wallet_id, _payload, before_dispatch):
        await before_dispatch()
        await inventory_sent()

    monkeypatch.setattr(views_payments, "internal_invoice_queue_put", queued)
    monkeypatch.setattr(tasks, "claim_fiat_checkout", claim)
    monkeypatch.setattr(tasks, "websocket_updater", notify)
    monkeypatch.setattr(tasks, "maybe_settle_tab", settle_tab)
    monkeypatch.setattr(tasks, "maybe_push_order", push_order)
    monkeypatch.setattr(tasks, "deduct_inventory_stock", deduct_stock)
    confirm_url = f"{base}/invoices/{payment_hash}/cash/validate"
    assert (await client.post(confirm_url)).is_success
    paid = await get_standalone_payment(payment_hash, incoming=True)
    assert paid and paid.success

    first = asyncio.create_task(tasks.on_invoice_paid(paid))
    await asyncio.wait_for(first_worker_ready.wait(), timeout=5)
    second = asyncio.create_task(
        tasks.on_invoice_paid(paid), name="competing-fiat-checkout"
    )
    try:
        with pytest.raises(RuntimeError, match="Tabs unavailable"):
            await first
        order_sent.assert_awaited_once()
        inventory_sent.assert_awaited_once()
        checkout = await get_tpos_payment_by_hash(payment_hash)
        assert checkout and not checkout.paid and checkout.status == "pending"
        assert (await client.post(confirm_url)).is_success
        assert queued.await_count == 2
    finally:
        released.set()
        await second

    order_sent.assert_awaited_once()
    inventory_sent.assert_awaited_once()
    assert settle_tab.await_count == 2
    checkout = await get_tpos_payment_by_hash(payment_hash)
    assert checkout and checkout.paid
    credited = await get_wallet(fiat.id)
    assert credited and credited.balance_msat == 1_000_000
