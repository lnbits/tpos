import asyncio
import ipaddress
import json
import socket
from decimal import Decimal
from time import time
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from embit.script import Script  # type: ignore[import-untyped]
from lnbits.core.crud import get_wallet
from lnbits.core.crud.payments import get_standalone_payment
from lnbits.core.crud.wallets import create_wallet
from lnbits.core.models import CreateInvoice
from lnbits.core.services import (
    create_payment_request,
    pay_invoice,
    update_wallet_balance,
)
from lnbits.core.services import lnurl as core_lnurl
from lnbits.core.services import payments as core_payments
from lnbits.helpers import create_access_token
from lnbits.settings import settings
from lnbits.utils import safe_http
from lnbits.utils.electrum import network_from_name
from tabs.crud import get_tab_settlements  # type: ignore[import]

from .. import (
    services,
    services_inventory,
    services_onchain,
    tasks,
    views_atm,
    views_lnurl,
    views_onchain,
    views_payments,
)
from ..crud import (
    get_lnurlcharge,
    get_tpos,
    get_tpos_payment_by_hash,
    update_lnurlcharge,
    update_tpos,
)
from ..models import CreateTposInvoice, TposClean
from .test_api import _tpos_payload, _user_with_tabs


@pytest.mark.asyncio
@pytest.mark.parametrize("early_event", [False, True])
async def test_paid_tab_invoice_during_creation_is_preserved_and_processed_once(
    client, monkeypatch, early_event
):
    _, wallet, pos = await terminal(client, tabs_enabled=True, tabs_allow_create=True)
    base = f"/tpos/api/v1/tposs/{pos['id']}"
    tab = (await client.post(f"{base}/tabs", json={"name": "Customer"})).json()
    await client.post(
        f"{base}/tabs/{tab['id']}/charges",
        json={"amount": 21, "idempotency_key": "early-payment-charge"},
    )
    await update_wallet_balance(wallet, 100)
    create_request = views_payments.create_payment_request
    replay = asyncio.Queue()
    paid_events = []
    monkeypatch.setattr(views_payments, "tpos_invoice_queue", replay)

    async def pay_during_creation(wallet_id, invoice_data):
        payment = await create_request(wallet_id, invoice_data)
        await pay_invoice(wallet_id=wallet.id, payment_request=payment.bolt11)
        paid = await get_standalone_payment(payment.payment_hash, incoming=True)
        assert paid is not None and paid.success
        paid_events.append(paid)
        if early_event:
            await tasks.on_invoice_paid(paid)
        return payment

    monkeypatch.setattr(views_payments, "create_payment_request", pay_during_creation)
    response = await client.post(
        f"{base}/invoices",
        json={
            "amount": 21,
            "tab_settlement": {
                "tab_id": tab["id"],
                "amount": 21,
                "idempotency_key": "early-payment-settlement",
            },
        },
    )
    assert response.status_code == 201, response.text
    payment_hash = response.json()["payment_hash"]
    payment = await get_standalone_payment(payment_hash, incoming=True)
    assert payment is not None and payment.success
    assert payment.preimage
    assert payment.extra["tab_settlement"]["amount_msat"] == 21_000
    assert replay.qsize() == 1
    replayed_payment = replay.get_nowait()
    assert replayed_payment.checking_id == payment.checking_id

    order = AsyncMock()
    monkeypatch.setattr(tasks, "maybe_push_order", order)
    # The original event predates the amount binding; the replay is also unprocessed.
    for event in [*paid_events, replayed_payment]:
        await tasks.on_invoice_paid(event.model_copy(deep=True))
    order.assert_awaited_once()
    settlements = await get_tab_settlements(tab["id"])
    assert len(settlements) == 1
    assert settlements[0].idempotency_key == f"tpos:{payment_hash}"
    tracked = await get_tpos_payment_by_hash(payment_hash)
    assert tracked is not None and tracked.paid


@pytest.fixture
def mempool_transport(client, monkeypatch):
    monkeypatch.setattr(settings, "lnbits_baseurl", "https://instance.example")
    monkeypatch.setattr(settings, "lnbits_outbound_host_ips", ["9.9.9.9"])
    monkeypatch.setattr(
        settings,
        "lnbits_onchain_explorer_urls",
        ["https://mempool.example", "https://mempool.space"],
    )
    destinations = {"127.0.0.1.nip.io": "127.0.0.1"}
    requests = []
    original_send = type(client).send

    async def resolve(self, host, port, **kwargs):
        address = destinations.get(host, host)
        assert address is not None
        try:
            ipaddress.ip_address(address)
        except ValueError:
            address = "8.8.8.8"
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port))]

    async def send(self, request, **kwargs):
        if request.url.host in {"testserver", "mempool.example", "mempool.space"}:
            return await original_send(self, request, **kwargs)
        requests.append(request)
        address = request.url.path.split("/")[-2]
        return httpx.Response(
            200,
            json=[
                {
                    "txid": "a" * 64,
                    "status": {"confirmed": True},
                    "vout": [
                        {"scriptpubkey_address": address, "value": 20},
                        {"scriptpubkey_address": "other", "value": 500},
                    ],
                },
                {
                    "txid": "b" * 64,
                    "status": {"confirmed": False},
                    "vout": [{"scriptpubkey_address": address, "value": 10}],
                },
            ],
            request=request,
        )

    def reject_callback_rules(_url):
        pytest.fail("Mempool requests must not apply callback rules")

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", resolve)
    # Outbound clients must not inherit conftest's ASGI base URL.
    monkeypatch.setattr(httpx, "AsyncClient", type(client))
    monkeypatch.setattr(type(client), "send", send)
    monkeypatch.setattr(safe_http, "check_callback_url", reject_callback_rules)
    return requests, destinations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:5099",
        "http://192.168.1.200:5099",
        "http://127.0.0.1.nip.io:5099",
        "http://169.254.169.254",
        "http://[fc00::1]",
        "https://instance.example",
        "https://9.9.9.9",
    ],
)
async def test_onchain_balance_blocks_unsafe_destinations(mempool_transport, target):
    requests, _ = mempool_transport
    address = Script(b"\x00\x14" + bytes(20)).address(network_from_name("main"))
    assert address
    balance = await services_onchain.fetch_onchain_balance(
        "https://mempool.example", address, "Mainnet"
    )
    assert balance == {
        "confirmed": 20,
        "unconfirmed": 10,
    }
    assert len(requests) == 1
    requests.clear()
    with pytest.raises(ValueError):
        await services_onchain.fetch_onchain_balance(target, address, "Mainnet")
    assert requests == []


@pytest.mark.asyncio
async def test_onchain_checkout_rechecks_explorer_and_distinguishes_testnet4(
    client, monkeypatch, mempool_transport
):
    requests, destinations = mempool_transport
    _, _, pos = await terminal(client)
    stored = await get_tpos(pos["id"])
    assert stored
    stored.onchain_enabled = True
    stored.onchain_wallet_id = "watch-wallet"
    stored.onchain_zero_conf = False
    await update_tpos(stored)
    address = Script(b"\x00\x14" + bytes([99]) * 20).address(network_from_name("test"))
    config = {
        "network": "Testnet4",
        "mempool_endpoint": "https://mempool.example/testnet",
    }
    monkeypatch.setattr(
        views_onchain, "watchonly_available_for_user", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        views_onchain, "fetch_watchonly_config", AsyncMock(return_value=config)
    )
    monkeypatch.setattr(
        views_onchain,
        "fetch_watchonly_wallet",
        AsyncMock(return_value={"network": "Testnet4"}),
    )
    fetch_address = AsyncMock(return_value={"address": address})
    monkeypatch.setattr(views_payments, "fetch_onchain_address", fetch_address)
    url = f"/tpos/api/v1/tposs/{pos['id']}/invoices"
    payload = {"amount": 42, "payment_method": "btc_onchain"}
    monkeypatch.setattr(
        settings,
        "lnbits_onchain_explorer_urls",
        [
            "https://mempool.example/testnet",
            "https://mempool.space",
            "http://127.0.0.1:5099",
        ],
    )
    rejected = await client.post(url, json=payload)
    assert rejected.status_code == 400
    config["mempool_endpoint"] = "http://127.0.0.1:5099"
    rejected = await client.post(url, json=payload)
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "Invalid on-chain explorer."
    fetch_address.assert_not_awaited()
    assert requests == []

    config["mempool_endpoint"] = "https://mempool.space"
    response = await client.post(url, json=payload)
    assert response.status_code == 201, response.text
    checkout = await get_tpos_payment_by_hash(response.json()["payment_hash"])
    assert checkout and checkout.onchain_network == "Testnet4"
    await tasks.check_onchain_payment(checkout)
    assert requests[0].url.path == f"/testnet4/api/address/{address}/txs"
    assert requests[0].headers["host"] == "mempool.space"
    assert requests[0].url.host == "8.8.8.8"
    assert checkout.balance == 20
    assert checkout.pending == 10

    requests.clear()
    destinations["mempool.space"] = "127.0.0.1"
    with pytest.raises(ValueError):
        await tasks.check_onchain_payment(checkout)
    assert requests == []


async def terminal(client, **settings):
    user, wallet = await _user_with_tabs(f"security_{uuid4().hex[:10]}")
    response = await client.post(
        "/tpos/api/v1/tposs",
        headers={"X-API-KEY": wallet.adminkey},
        json=_tpos_payload(**settings),
    )
    assert response.status_code == 201, response.text
    return user, wallet, response.json()


@pytest.mark.asyncio
async def test_owner_listing_requires_admin_key_and_stays_in_account(client):
    user, wallet, pos = await terminal(client)
    other_wallet = await create_wallet(user_id=user.id, wallet_name="Second")
    second = await client.post(
        "/tpos/api/v1/tposs",
        headers={"X-API-KEY": other_wallet.adminkey},
        json=_tpos_payload(),
    )
    await terminal(client)
    for query in ("", "?all_wallets=true"):
        denied = await client.get(
            f"/tpos/api/v1/tposs{query}", headers={"X-API-KEY": wallet.inkey}
        )
        assert denied.status_code == 403
    listing = await client.get(
        "/tpos/api/v1/tposs?all_wallets=true",
        headers={"X-API-KEY": wallet.adminkey},
    )
    assert {row["id"] for row in listing.json()} == {pos["id"], second.json()["id"]}
    assert all(set(row) == set(pos) for row in listing.json())
    public = TposClean(**pos).model_dump()
    assert "wallet" not in public
    assert "tip_wallet" not in public
    assert "adminkey" not in pos and "inkey" not in pos


@pytest.mark.asyncio
async def test_receipt_cannot_be_read_through_another_terminal(client):
    _, _, pos = await terminal(client)
    _, _, foreign = await terminal(client)
    invoice = await client.post(
        f"/tpos/api/v1/tposs/{pos['id']}/invoices", json={"amount": 21}
    )
    payment_hash = invoice.json()["payment_hash"]
    for extra in ("", "?extra=true"):
        valid = await client.get(
            f"/tpos/api/v1/tposs/{pos['id']}/invoices/{payment_hash}{extra}"
        )
        assert valid.status_code == 200
        denied = await client.get(
            f"/tpos/api/v1/tposs/{foreign['id']}/invoices/{payment_hash}{extra}"
        )
        assert denied.status_code == 404
    forged = await create_payment_request(
        foreign["wallet"],
        CreateInvoice(
            unit="sat", amount=21, extra={"tag": "tpos", "tpos_id": pos["id"]}
        ),
    )
    path = f"/tpos/api/v1/tposs/{pos['id']}/invoices/{forged.payment_hash}"
    assert (await client.get(path)).status_code == 404
    assert (
        await client.post(f"{path}/print", json={"receipt_type": "receipt"})
    ).status_code == 404


@pytest.mark.asyncio
async def test_atm_rejects_non_positive_amounts_and_enforces_cooldown(client):
    user, wallet, pos = await terminal(client)
    response = await client.post(
        f"/tpos/api/v1/atm/{pos['id']}/create",
        headers={
            "Authorization": f"Bearer {create_access_token({'sub': user.username})}"
        },
    )
    assert response.status_code == 200
    charge = response.json()
    await update_wallet_balance(wallet, 100)
    url = f"/tpos/api/v1/atm/withdraw/{charge['id']}"
    for amount in (0, -25):
        assert (await client.get(f"{url}/{amount}")).status_code == 422
    assert (await client.get(f"{url}/25")).status_code == 200
    params = await client.get(
        f"/tpos/api/v1/lnurl/{charge['id']}/25", headers={"host": "localhost"}
    )
    assert params.json()["k1"] == charge["id"]
    stored_pos = await get_tpos(pos["id"])
    assert stored_pos is not None
    stored_pos.withdraw_time = int(time())
    await update_tpos(stored_pos)
    assert not stored_pos.can_withdraw
    cooldown = await client.get(f"/tpos/api/v1/lnurl/cb?k1={charge['id']}&pr=bad")
    assert cooldown.json()["status"] == "ERROR"
    stored_pos.withdraw_time -= 2
    await update_tpos(stored_pos)
    assert stored_pos.can_withdraw
    saved = await get_lnurlcharge(charge["id"])
    assert saved is not None
    saved.amount = -25
    await update_lnurlcharge(saved)
    negative = await client.get(f"/tpos/api/v1/lnurl/cb?k1={charge['id']}&pr=bad")
    assert negative.json()["status"] == "ERROR"
    saved = await get_lnurlcharge(charge["id"])
    stored_pos = await get_tpos(pos["id"])
    assert saved is not None and not saved.claimed
    assert stored_pos is not None and stored_pos.withdrawn_amount == 0


@pytest.mark.asyncio
async def test_atm_nfc_reports_failed_redemption(client, monkeypatch):
    user, wallet, pos = await terminal(client)
    charge = (
        await client.post(
            f"/tpos/api/v1/atm/{pos['id']}/create",
            headers={
                "Authorization": f"Bearer {create_access_token({'sub': user.username})}"
            },
        )
    ).json()
    await update_wallet_balance(wallet, 100)
    await client.get(f"/tpos/api/v1/atm/withdraw/{charge['id']}/25")
    monkeypatch.setattr(
        views_atm, "get_pr_from_lnurl", AsyncMock(return_value="invoice")
    )
    monkeypatch.setattr(
        views_atm,
        "lnurl_callback",
        AsyncMock(return_value=views_lnurl.LnurlErrorResponse(reason="failed")),
    )
    result = await client.post(
        f"/tpos/api/v1/atm/withdraw/{charge['id']}/25/pay", json={"pay_link": "link"}
    )
    assert result.status_code == 500
    assert "failed" not in result.text


@pytest.mark.asyncio
async def test_tab_settlement_matches_paid_amount_and_deduplicates(client, monkeypatch):
    _, wallet, pos = await terminal(client, tabs_enabled=True, tabs_allow_create=True)
    base = f"/tpos/api/v1/tposs/{pos['id']}"
    tab = (await client.post(f"{base}/tabs", json={"name": "Customer"})).json()
    await client.post(
        f"{base}/tabs/{tab['id']}/charges",
        json={"amount": 100, "idempotency_key": "security-charge"},
    )
    data = {
        "amount": 1,
        "tab_settlement": {
            "tab_id": tab["id"],
            "amount": 100,
            "idempotency_key": "security-settlement",
        },
    }
    rejected = await client.post(f"{base}/invoices", json=data)
    assert rejected.status_code == 400
    data["amount"] = 21
    data["tab_settlement"]["amount"] = 21
    invoice = (await client.post(f"{base}/invoices", json=data)).json()
    await update_wallet_balance(wallet, 100)
    await pay_invoice(wallet_id=wallet.id, payment_request=invoice["bolt11"])
    payment = await get_standalone_payment(invoice["payment_hash"], incoming=True)
    stored_pos = await get_tpos(pos["id"])
    assert payment is not None and stored_pos is not None
    for _ in range(2):
        await tasks.maybe_settle_tab(payment, stored_pos, "lightning")
    tabs = (await client.get(f"{base}/tabs")).json()["data"]
    assert tabs[0]["balance"] == 79
    bridge = AsyncMock()
    monkeypatch.setattr(tasks, "create_tab_settlement_for_tpos", bridge)
    for changed in (
        {"wallet_id": uuid4().hex},
        {"amount": 1000},
        {"status": "pending"},
        {"extra": {**payment.extra, "tpos_id": uuid4().hex}},
    ):
        await tasks.maybe_settle_tab(
            payment.model_copy(update=changed), stored_pos, "lightning"
        )
    bridge.assert_not_awaited()


@pytest.mark.asyncio
async def test_inventory_validates_catalog_quantities_and_value(client, monkeypatch):
    _, _, pos = await terminal(client)
    stored = await get_tpos(pos["id"])
    assert stored is not None
    stored.use_inventory = True
    stored.inventory_id = "inventory"
    stored.inventory_tags = "allowed"
    stored.allow_price_adjustment = False
    await update_tpos(stored)
    catalog = AsyncMock(
        return_value=[
            {"id": "item", "price": 10, "tax_rate": 0, "quantity_in_stock": 5}
        ]
    )
    monkeypatch.setattr(services_inventory, "get_inventory_items_for_tpos", catalog)
    payload = {
        "amount": 20,
        "details": {"items": [{"id": "item", "quantity": 2, "price": 10}]},
        "inventory": {
            "inventory_id": "inventory",
            "items": [{"id": "item", "quantity": 2}],
        },
    }
    url = f"/tpos/api/v1/tposs/{pos['id']}/invoices"
    valid = await client.post(url, json=payload)
    assert valid.status_code == 201
    without_details = {key: value for key, value in payload.items() if key != "details"}
    assert (await client.post(url, json=without_details)).status_code == 201
    for amount, item_id, cart_qty, stock_qty, price in (
        (1, "item", 2, 2, 10),
        (20, "hidden", 2, 2, 10),
        (20, "item", 2, 3, 10),
        (60, "item", 6, 6, 10),
        (2, "item", 2, 2, 1),
    ):
        modified = json.loads(json.dumps(payload))
        modified["amount"] = amount
        modified["details"]["items"][0].update(
            id=item_id, quantity=cart_qty, price=price
        )
        modified["inventory"]["items"][0].update(id=item_id, quantity=stock_qty)
        rejected = await client.post(url, json=modified)
        assert rejected.status_code == 400
    wallet = await get_wallet(stored.wallet)
    assert wallet is not None
    catalog.assert_awaited_with(wallet.user, "inventory", "allowed", None)
    stored.allow_price_adjustment = True
    await update_tpos(stored)
    payload["amount"] = 2
    payload["details"]["items"][0]["price"] = 1
    assert (await client.post(url, json=payload)).status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "https://127.0.0.1/x",
        "https://169.254.169.254/x",
        "https://[::1]/x",
        "file:///etc/passwd",
        "https://private.example/x",
    ],
)
async def test_public_lnurl_routes_block_private_initial_targets(
    client, monkeypatch, target
):
    _, _, pos = await terminal(client)
    sender = AsyncMock()
    monkeypatch.setattr(core_lnurl, "_send_lnurl_request", sender)
    monkeypatch.setattr(
        core_lnurl,
        "_resolve_lnurl_host",
        AsyncMock(return_value=[ipaddress.ip_address("10.0.0.1")]),
    )
    address = await client.get(
        "/tpos/api/v1/tposs/lnaddresscheck", params={"lnaddress": target}
    )
    assert address.status_code == 400
    withdraw = await client.post(
        f"/tpos/api/v1/tposs/{pos['id']}/invoices/invoice/pay", json={"lnurl": target}
    )
    assert withdraw.json()["success"] is False
    sender.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "currency,price,quantity,tax,amount",
    [
        ("sats", 10, 5, 8, 55),
        ("EUR", 1.99, 3, 8.1, 6450),
        ("EUR", 1, 5, 8.4, 5420),
        ("JPY", 101, 1, 8, 109000),
        ("KWD", 1, 1, 11.15, 1112),
    ],
)
async def test_inventory_preserves_tax_and_currency_rounding(
    client, monkeypatch, currency, price, quantity, tax, amount
):
    _, _, pos = await terminal(client, currency=currency)
    stored = await get_tpos(pos["id"])
    assert stored is not None
    stored.use_inventory = True
    stored.inventory_id = "inventory"
    stored.inventory_tags = "allowed"
    stored.tax_inclusive = False
    await update_tpos(stored)
    monkeypatch.setattr(
        services_inventory,
        "get_inventory_items_for_tpos",
        AsyncMock(
            return_value=[
                {"id": "item", "price": price, "tax_rate": tax, "quantity_in_stock": 20}
            ]
        ),
    )

    async def convert(value, _currency):
        return int(Decimal(str(value)) * 1000)

    monkeypatch.setattr(services, "fiat_amount_as_satoshis", convert)
    payload = {
        "amount": amount,
        "details": {"items": [{"id": "item", "quantity": quantity, "price": price}]},
        "inventory": {
            "inventory_id": "inventory",
            "items": [{"id": "item", "quantity": quantity}],
        },
    }
    url = f"/tpos/api/v1/tposs/{pos['id']}/invoices"
    if currency == "EUR" and tax == 8.4:
        payload.update(amount=5400, amount_fiat=5.4)
    response = await client.post(url, json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["extra"]["amount"] == amount
    if currency == "EUR" and tax == 8.4:
        assert response.json()["extra"]["amount_fiat"] == 5.42
    payload["amount"] = 1
    corrected = await client.post(url, json=payload)
    if currency == "sats":
        assert corrected.status_code == 400
    else:
        assert corrected.status_code == 201
        assert corrected.json()["extra"]["amount"] == amount


@pytest.mark.asyncio
async def test_fiat_tab_amount_uses_server_rate_and_preserves_legacy(
    client, monkeypatch
):
    _, wallet, pos = await terminal(
        client, currency="EUR", tabs_enabled=True, tabs_allow_create=True
    )
    base = f"/tpos/api/v1/tposs/{pos['id']}"
    tab = (await client.post(f"{base}/tabs", json={"name": "Customer"})).json()
    await client.post(
        f"{base}/tabs/{tab['id']}/charges",
        json={"amount": 100, "idempotency_key": "fiat-security-charge"},
    )

    async def convert(value, _currency):
        return int(Decimal(str(value)) * Decimal("1000.1"))

    monkeypatch.setattr(services, "fiat_amount_as_satoshis", convert)
    monkeypatch.setattr(core_payments, "fiat_amount_as_satoshis", convert)
    data = {
        "amount": 1,
        "exchange_rate": 0.01,
        "tab_settlement": {
            "tab_id": tab["id"],
            "amount": 100,
            "idempotency_key": "fiat-security-settlement",
        },
    }
    repriced = await client.post(f"{base}/invoices", json=data)
    assert repriced.status_code == 201
    assert repriced.json()["extra"]["amount"] == 100_010
    data.update(pay_in_fiat=True, amount_fiat=1)
    assert (await client.post(f"{base}/invoices", json=data)).status_code == 400
    data.update(amount_fiat=100, tip_amount=501, tip_amount_fiat=0.5)
    created = await client.post(f"{base}/invoices", json=data)
    assert created.status_code == 201, created.text
    invoice = created.json()
    assert invoice["extra"]["tab_settlement"]["amount_msat"] == 100_510_000
    await update_wallet_balance(wallet, 200_000)
    await pay_invoice(wallet_id=wallet.id, payment_request=invoice["bolt11"])
    payment = await get_standalone_payment(invoice["payment_hash"], incoming=True)
    stored_pos = await get_tpos(pos["id"])
    assert payment is not None and stored_pos is not None
    original_bridge = tasks.create_tab_settlement_for_tpos
    bridge = AsyncMock()
    monkeypatch.setattr(tasks, "create_tab_settlement_for_tpos", bridge)
    await tasks.maybe_settle_tab(payment, stored_pos, "lightning")
    bridge.assert_awaited_once()
    monkeypatch.setattr(tasks, "create_tab_settlement_for_tpos", original_bridge)
    payment.extra["tab_settlement"].pop("amount_msat")
    for _ in range(2):
        await tasks.maybe_settle_tab(payment, stored_pos, "lightning")
    settlements = await get_tab_settlements(tab["id"])
    assert len(settlements) == 1
    assert settlements[0].idempotency_key == "fiat-security-settlement"
    assert (await client.get(f"{base}/tabs")).json()["data"] == []


@pytest.mark.asyncio
async def test_fiat_keypad_sale_uses_server_rate(client, monkeypatch):
    _, _, pos = await terminal(client, currency="BRL")
    url = f"/tpos/api/v1/tposs/{pos['id']}/invoices"

    async def convert(value, _currency):
        return int(Decimal(str(value)) * 229)

    monkeypatch.setattr(services, "fiat_amount_as_satoshis", convert)
    # A client whose rate fetch failed and fell back to 1 sat per BRL (#264).
    data = {
        "amount": 15,
        "amount_fiat": 14.45,
        "exchange_rate": 1,
    }
    created = await client.post(url, json=data)
    assert created.status_code == 201, created.text
    extra = created.json()["extra"]
    assert extra["amount"] == 3309
    assert extra["exchangeRate"] == pytest.approx(3309 / 14.45)
    assert extra["details"]["exchangeRate"] == pytest.approx(3309 / 14.45)

    async def no_rate(_value, currency):
        raise ValueError(f"Could not get exchange rate for {currency}.")

    monkeypatch.setattr(services, "fiat_amount_as_satoshis", no_rate)
    unavailable = await client.post(url, json=data)
    assert unavailable.status_code == 503
    assert "Exchange rate not available" in unavailable.text


@pytest.mark.asyncio
async def test_fiat_keypad_sale_tax_uses_repriced_amount(client, monkeypatch):
    _, _, pos = await terminal(client, currency="BRL", tax_default=10)

    async def convert(value, _currency):
        return int(Decimal(str(value)) * 229)

    monkeypatch.setattr(services, "fiat_amount_as_satoshis", convert)
    created = await client.post(
        f"/tpos/api/v1/tposs/{pos['id']}/invoices",
        json={"amount": 15, "amount_fiat": 14.45, "exchange_rate": 1},
    )
    assert created.status_code == 201, created.text
    details = created.json()["extra"]["details"]
    assert details["taxValue"] == pytest.approx(14.45 * 0.1 / 1.1)


@pytest.mark.asyncio
async def test_sats_keypad_sale_amount_is_unchanged(client, monkeypatch):
    _, _, pos = await terminal(client)
    convert = AsyncMock(side_effect=AssertionError("no conversion for sats"))
    monkeypatch.setattr(services, "fiat_amount_as_satoshis", convert)
    created = await client.post(
        f"/tpos/api/v1/tposs/{pos['id']}/invoices",
        json={"amount": 21, "tip_amount": 3},
    )
    assert created.status_code == 201, created.text
    assert created.json()["extra"]["amount"] == 21
    assert created.json()["extra"]["tip_amount"] == 3


@pytest.mark.asyncio
async def test_fractional_sats_tab_balance_rounds_up_without_changing_balance(client):
    _, _, pos = await terminal(client)
    data = CreateTposInvoice(amount=22)
    stored_pos = await get_tpos(pos["id"])
    assert stored_pos is not None
    await services.validate_tpos_sale_amount(stored_pos, data, 21.5)
    assert data.amount == 22


@pytest.mark.asyncio
@pytest.mark.parametrize("redirect", [False, True])
async def test_lnurl_withdraw_rejects_private_callbacks_and_redirects(
    client, monkeypatch, redirect
):
    _, _, pos = await terminal(client)
    invoice = (
        await client.post(
            f"/tpos/api/v1/tposs/{pos['id']}/invoices", json={"amount": 21}
        )
    ).json()
    if redirect:
        result = (302, {"location": "https://127.0.0.1/private"}, b"")
    else:
        result = (
            200,
            {},
            json.dumps(
                {
                    "tag": "withdrawRequest",
                    "callback": "https://127.0.0.1/private",
                    "k1": "claim",
                    "minWithdrawable": 21000,
                    "maxWithdrawable": 21000,
                    "defaultDescription": "test",
                }
            ).encode(),
        )
    sender = AsyncMock(return_value=result)
    monkeypatch.setattr(core_lnurl, "_send_lnurl_request", sender)
    response = await client.post(
        f"/tpos/api/v1/tposs/{pos['id']}/invoices/{invoice['bolt11']}/pay",
        json={"lnurl": "https://1.1.1.1/withdraw"},
    )
    assert response.json() == {
        "success": False,
        "detail": "Cannot process LNURL withdrawal.",
    }
    assert sender.await_count == 1
