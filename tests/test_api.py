import asyncio
import json
from uuid import uuid4

import pytest
from httpx import AsyncClient
from lnbits.core.crud import get_standalone_payment
from lnbits.core.models.users import Account
from lnbits.core.services import pay_invoice, update_wallet_balance
from lnbits.core.services.users import create_user_account_no_ckeck
from lnbits.settings import settings
from lnbits.tasks import internal_invoice_queue
from tabs.crud import (  # type: ignore[import]
    get_tab_by_id,
    get_tab_entries,
    get_tab_settlements,
)

from tpos.crud import get_tpos, get_tpos_payment_by_hash  # type: ignore[import]
from tpos.tasks import on_invoice_paid  # type: ignore[import]


def _tpos_payload(**overrides):
    payload = {
        "wallet": None,
        "name": "Main TPoS",
        "currency": "sats",
        "business_name": "Main Shop",
        "business_address": "1 Market Street",
        "business_vat_id": "VAT123",
        "tip_options": "[]",
        "tip_wallet": "",
        "withdraw_between": 1,
        "withdraw_limit": 100,
        "withdraw_time_option": "secs",
        "enable_receipt_print": True,
        "enable_remote": True,
    }
    payload.update(overrides)
    return payload


async def _user_with_tabs(username: str = "tposuser"):
    account = Account(id=uuid4().hex, username=username)
    user = await create_user_account_no_ckeck(account=account, default_exts=["tabs"])
    return user, user.wallets[0]


async def _drain_internal_invoice_queue() -> None:
    while True:
        try:
            internal_invoice_queue.get_nowait()
        except asyncio.QueueEmpty:
            return


@pytest.mark.asyncio
async def test_tpos_crud_settings_and_wrapper_token(client: AsyncClient):
    user, wallet = await _user_with_tabs()
    settings.super_user = user.id
    headers = {"X-API-KEY": wallet.adminkey}

    listed_empty = await client.get("/tpos/api/v1/tposs", headers=headers)
    assert listed_empty.status_code == 200
    assert listed_empty.json() == []

    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(currency="EUR", allow_cash_settlement=True),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()
    assert tpos["wallet"] == wallet.id
    assert tpos["allow_cash_settlement"] is True

    listed = await client.get("/tpos/api/v1/tposs?all_wallets=true", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [tpos["id"]]

    update = await client.put(
        f"/tpos/api/v1/tposs/{tpos['id']}",
        json=_tpos_payload(
            name="Updated TPoS",
            currency="EUR",
            allow_cash_settlement=True,
            tabs_enabled=True,
            tabs_allow_create=True,
            inventory_tags=["coffee", "tea"],
            inventory_omit_tags=["hidden"],
        ),
        headers=headers,
    )
    assert update.status_code == 200
    updated = update.json()
    assert updated["name"] == "Updated TPoS"
    assert updated["tabs_enabled"] is True
    assert updated["tabs_allow_create"] is True
    assert updated["inventory_tags"] == "coffee,tea"

    token = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/wrapper-token", headers=headers
    )
    assert token.status_code == 200
    assert token.json()["auth"]
    assert token.json()["expiration_time_minutes"] > 500_000

    items = await client.put(
        f"/tpos/api/v1/tposs/{tpos['id']}/items",
        json={
            "items": [
                {
                    "image": None,
                    "price": 2.5,
                    "title": "Coffee",
                    "description": "Hot",
                    "tax": 10,
                    "disabled": False,
                    "categories": ["coffee"],
                }
            ]
        },
        headers=headers,
    )
    assert items.status_code == 201
    assert json.loads(items.json()["items"])[0]["title"] == "Coffee"

    delete = await client.delete(f"/tpos/api/v1/tposs/{tpos['id']}", headers=headers)
    assert delete.status_code == 200
    assert await get_tpos(tpos["id"]) is None


@pytest.mark.asyncio
async def test_tabs_endpoints_use_real_tabs_api(client: AsyncClient):
    _user, wallet = await _user_with_tabs("tabsuser")
    headers = {"X-API-KEY": wallet.adminkey}

    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(tabs_enabled=True, tabs_allow_create=True),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    create_tab = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/tabs",
        json={
            "name": "Patio",
            "customer_name": "Alice",
            "reference": "Table 7",
        },
    )
    assert create_tab.status_code == 200
    tab = create_tab.json()
    assert tab["name"] == "Patio"
    assert tab["currency"] == "sats"

    tabs = await client.get(f"/tpos/api/v1/tposs/{tpos['id']}/tabs?status=open")
    assert tabs.status_code == 200
    assert [item["id"] for item in tabs.json()["data"]] == [tab["id"]]

    charge = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/tabs/{tab['id']}/charges",
        json={
            "amount": 25000,
            "description": "Drinks",
            "items": [{"title": "Coffee", "quantity": 2, "price": 12500}],
            "idempotency_key": "tpos-charge-1",
        },
    )
    assert charge.status_code == 200
    charge_payload = charge.json()
    assert charge_payload["entry"]["entry_type"] == "charge"
    assert charge_payload["entry"]["amount"] == 25000
    assert charge_payload["tab"]["balance"] == 25000

    entries = await get_tab_entries(tab["id"])
    assert len(entries) == 1
    assert entries[0].source == "tpos"


@pytest.mark.asyncio
async def test_tabs_bridge_returns_tabs_error_detail(client: AsyncClient):
    _user, wallet = await _user_with_tabs("tabserroruser")
    headers = {"X-API-KEY": wallet.adminkey}

    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(tabs_enabled=True, tabs_allow_create=True),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    create_tab = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/tabs",
        json={
            "name": "Patio",
            "limit_type": "hard",
            "limit_amount": 100,
        },
    )
    assert create_tab.status_code == 200
    tab = create_tab.json()

    charge = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/tabs/{tab['id']}/charges",
        json={
            "amount": 101,
            "description": "Over limit",
            "idempotency_key": "tpos-charge-over-limit",
        },
    )
    assert charge.status_code == 400
    assert charge.json()["detail"] == "Charge would exceed the configured tab limit."


@pytest.mark.asyncio
async def test_paid_tpos_invoice_settles_tab_via_real_tabs_api(client: AsyncClient):
    await _drain_internal_invoice_queue()
    _user, wallet = await _user_with_tabs("settlementuser")
    headers = {"X-API-KEY": wallet.adminkey}

    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(tabs_enabled=True, tabs_allow_create=True),
        headers=headers,
    )
    tpos = create.json()
    create_tab = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/tabs",
        json={"name": "Counter"},
    )
    tab = create_tab.json()
    charge = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/tabs/{tab['id']}/charges",
        json={
            "amount": 21,
            "description": "Cake",
            "idempotency_key": "tpos-charge-settlement",
        },
    )
    assert charge.status_code == 200

    invoice_response = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
        json={
            "amount": 21,
            "memo": "Settle tab",
            "tab_settlement": {
                "tab_id": tab["id"],
                "amount": 21,
                "reference": "counter-close",
                "description": "TPoS settlement",
                "idempotency_key": "tpos-settlement-1",
            },
        },
    )
    assert invoice_response.status_code == 201
    invoice = invoice_response.json()

    await update_wallet_balance(wallet, 100)
    await _drain_internal_invoice_queue()
    await pay_invoice(wallet_id=wallet.id, payment_request=invoice["bolt11"])
    await _drain_internal_invoice_queue()

    payment = await get_standalone_payment(invoice["payment_hash"], incoming=True)
    assert payment is not None
    await on_invoice_paid(payment)

    tpos_payment = await get_tpos_payment_by_hash(invoice["payment_hash"])
    assert tpos_payment is not None
    assert tpos_payment.paid is True

    settled_tab = await get_tab_by_id(tab["id"])
    assert settled_tab is not None
    assert settled_tab.balance == 0
    assert settled_tab.status == "closed"

    settlements = await get_tab_settlements(tab["id"])
    assert len(settlements) == 1
    assert settlements[0].status == "completed"
    assert settlements[0].method == "other"
    assert settlements[0].idempotency_key == "tpos-settlement-1"


@pytest.mark.asyncio
async def test_tpos_rejects_invalid_tab_flows(client: AsyncClient):
    _user, wallet = await _user_with_tabs("invalidtabsuser")
    headers = {"X-API-KEY": wallet.adminkey}

    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(tabs_enabled=False, tabs_allow_create=True),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()
    assert tpos["tabs_allow_create"] is False

    tabs_disabled = await client.get(f"/tpos/api/v1/tposs/{tpos['id']}/tabs")
    assert tabs_disabled.status_code == 400

    update = await client.put(
        f"/tpos/api/v1/tposs/{tpos['id']}",
        json=_tpos_payload(tabs_enabled=True, tabs_allow_create=False),
        headers=headers,
    )
    assert update.status_code == 200

    create_denied = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/tabs",
        json={"name": "Denied"},
    )
    assert create_denied.status_code == 403
