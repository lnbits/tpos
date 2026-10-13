import ipaddress
import json
from decimal import Decimal
from time import time
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
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
from tabs.crud import get_tab_settlements  # type: ignore[import]

from .. import services, services_inventory, tasks, views_atm, views_lnurl
from ..crud import get_lnurlcharge, get_tpos, update_lnurlcharge, update_tpos
from ..models import CreateTposInvoice, TposClean
from .test_api import _tpos_payload, _user_with_tabs


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
    public = TposClean(**pos).dict()
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
    stored_pos.withdraw_time = int(time())
    await update_tpos(stored_pos)
    assert not stored_pos.can_withdraw
    cooldown = await client.get(f"/tpos/api/v1/lnurl/cb?k1={charge['id']}&pr=bad")
    assert cooldown.json()["status"] == "ERROR"
    stored_pos.withdraw_time -= 2
    await update_tpos(stored_pos)
    assert stored_pos.can_withdraw
    saved = await get_lnurlcharge(charge["id"])
    saved.amount = -25
    await update_lnurlcharge(saved)
    negative = await client.get(f"/tpos/api/v1/lnurl/cb?k1={charge['id']}&pr=bad")
    assert negative.json()["status"] == "ERROR"
    assert not (await get_lnurlcharge(charge["id"])).claimed
    assert (await get_tpos(pos["id"])).withdrawn_amount == 0


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
            payment.copy(update=changed), stored_pos, "lightning"
        )
    bridge.assert_not_awaited()


@pytest.mark.asyncio
async def test_inventory_validates_catalog_quantities_and_value(client, monkeypatch):
    _, _, pos = await terminal(client)
    stored = await get_tpos(pos["id"])
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
    catalog.assert_awaited_with(
        (await get_wallet(stored.wallet)).user, "inventory", "allowed", None
    )
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
    original_bridge = tasks.create_tab_settlement_for_tpos
    bridge = AsyncMock()
    monkeypatch.setattr(tasks, "create_tab_settlement_for_tpos", bridge)
    await tasks.maybe_settle_tab(payment, await get_tpos(pos["id"]), "lightning")
    bridge.assert_awaited_once()
    monkeypatch.setattr(tasks, "create_tab_settlement_for_tpos", original_bridge)
    payment.extra["tab_settlement"].pop("amount_msat")
    for _ in range(2):
        await tasks.maybe_settle_tab(payment, await get_tpos(pos["id"]), "lightning")
    settlements = await get_tab_settlements(tab["id"])
    assert len(settlements) == 1
    assert settlements[0].idempotency_key == "fiat-security-settlement"
    assert (await client.get(f"{base}/tabs")).json()["data"] == []


@pytest.mark.asyncio
async def test_fractional_sats_tab_balance_rounds_up_without_changing_balance(client):
    _, _, pos = await terminal(client)
    data = CreateTposInvoice(amount=22)
    await services.validate_tpos_sale_amount(await get_tpos(pos["id"]), data, 21.5)
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
