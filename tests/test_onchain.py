import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from embit.script import Script  # type: ignore[import-untyped]
from lnbits.core.crud import get_standalone_payment, get_wallet
from lnbits.core.crud.wallets import (
    create_wallet,
    get_onchain_wallet,
    init_onchain_wallet_state,
)
from lnbits.core.db import db as core_db
from lnbits.core.models import Payment
from lnbits.core.models.wallets import WalletType
from lnbits.utils.electrum import network_from_name
from sqlalchemy.exc import IntegrityError  # type: ignore[import-untyped]
from tabs.crud import get_tab_settlements  # type: ignore[import]

from .. import (
    migrations,
    services_onchain,
    services_orders,
    tasks,
    views_onchain,
    views_payments,
)
from ..crud import (
    create_onchain_payment,
    create_tpos_payment,
    db,
    get_pending_tpos_payments,
    get_tpos_payment_by_hash,
)
from ..models import OnchainSale, Tpos, TposPayment, TposPaymentStatus
from .test_api import _tpos_payload, _user_with_tabs


def _address(seed: int, network: str = "main") -> str:
    address = Script(b"\x00\x14" + bytes([seed]) * 20).address(
        network_from_name(network)
    )
    assert address
    return address


@pytest.mark.asyncio
@pytest.mark.parametrize("core", [False, True])
async def test_ordinary_merchant_onchain_checkout_fulfils_once(
    client, monkeypatch, core
):
    user, wallet = await _user_with_tabs(f"onchain_checkout_{int(core)}")
    assert not user.super_user
    monkeypatch.setattr(core_db, "lock", asyncio.Lock())
    monkeypatch.setattr(db, "lock", asyncio.Lock())
    monkeypatch.setattr(views_payments, "time", lambda: 100)
    monkeypatch.setattr(tasks, "time", lambda: 101)
    monkeypatch.setattr(views_onchain, "validate_url", AsyncMock())
    watchonly = AsyncMock(return_value=not core)
    monkeypatch.setattr(views_onchain, "watchonly_available_for_user", watchonly)
    key = wallet.inkey
    wallet_id = "watch-wallet"
    if core:
        unconfigured = await create_wallet(
            user_id=user.id, wallet_type=WalletType.ONCHAIN
        )
        core_wallet = await get_onchain_wallet(unconfigured.id)
        assert core_wallet
        core_wallet.onchain_wallet_kind = "watch"
        core_wallet = await init_onchain_wallet_state(core_wallet)
        key = core_wallet.inkey
        wallet_id = core_wallet.id
    else:
        config = {"network": "Mainnet"}
        monkeypatch.setattr(
            views_onchain, "fetch_watchonly_config", AsyncMock(return_value=config)
        )
        monkeypatch.setattr(
            views_onchain, "fetch_watchonly_wallet", AsyncMock(return_value=config)
        )
    wallet_ref = f"core:{wallet_id}" if core else wallet_id
    fetch_address = AsyncMock(return_value={"address": _address(3 if core else 4)})
    monkeypatch.setattr(views_payments, "fetch_onchain_address", fetch_address)
    headers = {"X-API-KEY": wallet.adminkey}
    pos = await client.post(
        "/tpos/api/v1/tposs",
        headers=headers,
        json=_tpos_payload(
            onchain_enabled=True,
            onchain_wallet_id=wallet_ref,
            onchain_zero_conf=core,
            tabs_enabled=True,
            tabs_allow_create=True,
            enable_remote=True,
        ),
    )
    assert pos.status_code == 201, pos.text
    base = f"/tpos/api/v1/tposs/{pos.json()['id']}"
    tab = await client.post(f"{base}/tabs", json={"name": "Onchain"})
    assert tab.status_code == 200, tab.text
    tab_id = tab.json()["id"]
    charge = await client.post(
        f"{base}/tabs/{tab_id}/charges",
        json={"amount": 42, "idempotency_key": "onchain-charge"},
    )
    assert charge.status_code == 200, charge.text
    before = await get_wallet(wallet.id)
    assert before
    sale = {
        "amount": 42,
        "tip_amount": 3,
        "payment_method": "btc_onchain",
        "tab_settlement": {
            "tab_id": tab_id,
            "amount": 42,
            "idempotency_key": "onchain-settlement",
        },
    }
    paid_events = AsyncMock()
    monkeypatch.setattr(tasks, "websocket_updater", paid_events)
    create_payment = AsyncMock(
        side_effect=AssertionError("On-chain cannot create a Lightning invoice")
    )
    monkeypatch.setattr(views_payments, "create_payment_request", create_payment)

    # Competing checkouts returned the same receive address by the wallet source.
    responses = await asyncio.gather(
        client.post(f"{base}/invoices", json=sale),
        client.post(f"{base}/invoices", json=sale),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    invoice = next(
        response.json() for response in responses if response.status_code == 201
    )
    assert fetch_address.await_args
    assert fetch_address.await_args.args == (key, wallet_id)
    assert fetch_address.await_args.kwargs == {"core": core}
    if core:
        watchonly.assert_not_awaited()
    assert invoice["bolt11"] == ""
    assert invoice["payment_options"] == ["btc_onchain"]
    assert invoice["onchain_amount_sat"] == 45
    payment_hash = invoice["payment_hash"]
    assert not await get_standalone_payment(payment_hash, incoming=True)
    check_url = f"{base}/invoices/{payment_hash}"
    checkout = await get_tpos_payment_by_hash(payment_hash)
    assert checkout and checkout.onchain_sale and not checkout.paid

    unused_address = _address(6 if core else 7)
    fetch_address.side_effect = [
        {"address": invoice["onchain_address"]},
        {"address": unused_address},
    ]
    with monkeypatch.context() as mock:
        mock.setattr(
            views_payments, "websocket_updater", AsyncMock(side_effect=TimeoutError)
        )
        retry = await client.post(
            f"{base}/invoices", json={"amount": 42, "payment_method": "btc_onchain"}
        )
    assert retry.status_code == 201, retry.text
    retried_sale = await get_tpos_payment_by_hash(retry.json()["payment_hash"])
    assert retried_sale and retried_sale.onchain_address == unused_address

    balance = AsyncMock(return_value={"confirmed": 0, "unconfirmed": 44})
    monkeypatch.setattr(tasks, "fetch_onchain_balance", balance)
    await tasks.check_onchain_payment(checkout)
    assert not (await client.get(check_url)).json()["paid"]

    balance.return_value = {"confirmed": 0, "unconfirmed": 45}
    await tasks.check_onchain_payment(checkout)
    assert (await client.get(check_url)).json()["paid"] is core
    if not core:
        balance.return_value = {"confirmed": 45, "unconfirmed": 0}
        await asyncio.gather(
            tasks.check_onchain_payment(checkout),
            tasks.check_onchain_payment(checkout.model_copy(deep=True)),
        )

    # Recover from a stop after recording payment and compete to fulfil it.
    recovered = next(
        row for row in await get_pending_tpos_payments() if row.id == checkout.id
    )
    assert recovered.paid and recovered.onchain_fulfilment == "pending"
    push_order = AsyncMock()
    monkeypatch.setattr(tasks, "maybe_push_order", push_order)
    await asyncio.gather(
        tasks._fulfil_onchain_checkout(recovered),
        tasks._fulfil_onchain_checkout(recovered.model_copy(deep=True)),
    )
    push_order.assert_awaited_once()
    settlements = await get_tab_settlements(tab_id)
    assert len(settlements) == 1
    assert settlements[0].idempotency_key == f"tpos:{payment_hash}"
    assert (await client.get(check_url)).json() == {"paid": True, "status": "paid"}
    assert (await client.get(f"{check_url}?extra=true")).json()["paid"]
    assert (
        await client.post(f"{check_url}/print", json={"receipt_type": "receipt"})
    ).status_code == 200
    recent = (await client.get(f"{base}/invoices")).json()
    assert recent[0]["amount"] == 45_000 and recent[0]["payment_method"] == "onchain"
    other = await client.post(
        "/tpos/api/v1/tposs", headers=headers, json=_tpos_payload()
    )
    assert other.status_code == 201
    foreign_url = f"/tpos/api/v1/tposs/{other.json()['id']}/invoices/{payment_hash}"
    assert (await client.get(f"{foreign_url}?extra=true")).status_code == 404
    assert not await get_standalone_payment(payment_hash, incoming=True)
    after = await get_wallet(wallet.id)
    assert after and after.balance_msat == before.balance_msat
    create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_core_wallet_rejected_before_address_allocation(client, monkeypatch):
    user, wallet = await _user_with_tabs("onchain_owner")
    another_user, _ = await _user_with_tabs("onchain_foreign")
    foreign = await create_wallet(
        user_id=another_user.id, wallet_type=WalletType.ONCHAIN
    )
    unconfigured = await create_wallet(user_id=user.id, wallet_type=WalletType.ONCHAIN)
    hot = await get_onchain_wallet(unconfigured.id)
    assert hot
    hot.onchain_wallet_kind = "hot"
    hot = await init_onchain_wallet_state(hot)
    headers = {"X-API-KEY": wallet.adminkey}
    fetch = AsyncMock()
    monkeypatch.setattr(views_payments, "fetch_onchain_address", fetch)
    for selected, status in [(foreign, 400), (hot, 409)]:
        response = await client.post(
            "/tpos/api/v1/tposs",
            headers=headers,
            json=_tpos_payload(
                onchain_enabled=True, onchain_wallet_id=f"core:{selected.id}"
            ),
        )
        assert response.status_code == status, response.text
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous,pending,confirmed,status",
    [
        (0, 0, 0, "expired"),
        (0, 10, 0, "underpaid"),
        (0, 0, 42, "abandoned"),
        (42, 42, 0, "pending"),
        (42, 0, 42, "paid"),
    ],
)
async def test_onchain_expiry(previous, pending, confirmed, status, monkeypatch):
    checkout = await create_tpos_payment(
        TposPayment(
            id=uuid4().hex,
            tpos_id="test-expiry",
            payment_hash=uuid4().hex,
            amount=42,
            onchain_address=_address(5),
            onchain_network="Mainnet",
            onchain_zero_conf=False,
            pending=previous,
            onchain_sale=OnchainSale(
                wallet_id="wallet", expires_at=50, memo="", extra={}
            ),
        )
    )
    monkeypatch.setattr(tasks, "time", lambda: 51)
    monkeypatch.setattr(tasks, "websocket_updater", AsyncMock())
    monkeypatch.setattr(
        tasks,
        "fetch_onchain_balance",
        AsyncMock(
            return_value={
                "confirmed": confirmed,
                "unconfirmed": pending,
            }
        ),
    )
    await tasks.check_onchain_payment(checkout)
    stored = await get_tpos_payment_by_hash(checkout.payment_hash)
    assert stored and stored.status == status and stored.paid is (status == "paid")


@pytest.mark.asyncio
async def test_ambiguous_order_dispatch_is_not_replayed(client, monkeypatch):
    _, wallet = await _user_with_tabs("onchain_recovery")
    terminal = await client.post(
        "/tpos/api/v1/tposs",
        headers={"X-API-KEY": wallet.adminkey},
        json=_tpos_payload(),
    )
    assert terminal.status_code == 201
    tpos_id = terminal.json()["id"]
    sale = await create_tpos_payment(
        TposPayment(
            id="recovery-checkout",
            tpos_id=tpos_id,
            payment_hash="recovery-payment",
            amount=42,
            paid=True,
            status=TposPaymentStatus.PAID,
            onchain_fulfilment="pending",
            onchain_sale=OnchainSale(
                wallet_id=wallet.id, expires_at=50, memo="", extra={}
            ),
        )
    )

    async def dispatch(_sale, _tpos, before_dispatch):
        await before_dispatch()
        raise TimeoutError("Order outcome unknown")

    order = AsyncMock(side_effect=TimeoutError("Order wallet lookup failed"))
    monkeypatch.setattr(tasks, "maybe_push_order", order)
    with pytest.raises(TimeoutError):
        await tasks._fulfil_onchain_checkout(sale)
    retried = await get_tpos_payment_by_hash(sale.payment_hash)
    assert retried and retried.paid and retried.onchain_fulfilment == "pending"

    order.reset_mock()
    order.side_effect = dispatch
    with pytest.raises(TimeoutError):
        await tasks._fulfil_onchain_checkout(sale)
    stored = await get_tpos_payment_by_hash(sale.payment_hash)
    assert stored and stored.paid and stored.onchain_fulfilment == "order"
    await tasks._fulfil_onchain_checkout(stored)
    order.assert_awaited_once()
    assert sale.id not in {row.id for row in await get_pending_tpos_payments()}


@pytest.mark.asyncio
async def test_address_reservation_rolls_back_if_sale_insert_fails():
    existing = await create_tpos_payment(
        TposPayment(
            id="existing-sale", tpos_id="rollback", payment_hash="existing-hash"
        )
    )
    checkout = TposPayment(
        id="rollback-sale",
        tpos_id=existing.tpos_id,
        payment_hash=existing.payment_hash,
        onchain_address=_address(8),
        onchain_network="Mainnet",
        onchain_sale=OnchainSale(wallet_id="wallet", expires_at=50, memo="", extra={}),
    )
    with pytest.raises(IntegrityError):
        await create_onchain_payment(checkout)
    checkout.payment_hash = "retry-hash"
    assert await create_onchain_payment(checkout)
    stored = await get_tpos_payment_by_hash(checkout.payment_hash)
    assert stored and stored.onchain_address == checkout.onchain_address


@pytest.mark.asyncio
async def test_upgrade_closes_legacy_checkout_without_marking_it_paid():
    for identifier, address in [("legacy", _address(9)), ("lightning", None)]:
        await create_tpos_payment(
            TposPayment(
                id=identifier,
                tpos_id="upgrade",
                payment_hash=identifier,
                onchain_address=address,
            )
        )
    await migrations.m029_close_legacy_onchain_checkouts(db)
    legacy = await get_tpos_payment_by_hash("legacy")
    lightning = await get_tpos_payment_by_hash("lightning")
    assert legacy and legacy.status == TposPaymentStatus.ABANDONED and not legacy.paid
    assert lightning and lightning.status == TposPaymentStatus.PENDING
    assert not {"legacy", "lightning"}.intersection(
        row.id for row in await get_pending_tpos_payments()
    )


@pytest.mark.asyncio
async def test_unknown_onchain_network_fails_before_request():
    with pytest.raises(ValueError, match="Unknown on-chain network"):
        await services_onchain.fetch_onchain_balance(None, _address(10), "unknown")


@pytest.mark.asyncio
async def test_legacy_order_can_have_no_payment_timestamp(monkeypatch):
    client = AsyncMock()
    client.__aenter__.return_value = client
    monkeypatch.setattr(services_orders.httpx, "AsyncClient", lambda: client)
    payment = Payment.model_construct(
        payment_hash="legacy-order-hash",
        checking_id="legacy-order",
        amount=1000,
        fee=0,
        time=None,
    )
    await services_orders.push_order_to_orders(
        "owner", payment, Tpos.model_construct(name="Test")
    )
    client.post.assert_awaited_once()
    assert client.post.await_args.kwargs["json"]["created_at"] is None
