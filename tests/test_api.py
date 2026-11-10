import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from httpx import AsyncClient
from lnbits.core.crud import get_standalone_payment
from lnbits.core.crud.payments import create_payment
from lnbits.core.crud.wallets import create_wallet, get_total_balance, get_wallet
from lnbits.core.models import CreatePayment, PaymentState
from lnbits.core.models.users import Account
from lnbits.core.models.wallets import WalletType
from lnbits.core.services import (
    pay_invoice,
    update_wallet_balance,
)
from lnbits.core.services.users import create_user_account_no_ckeck
from lnbits.helpers import create_access_token
from lnbits.settings import settings
from tabs.crud import (  # type: ignore[import]
    get_tab_by_id,
    get_tab_entries,
    get_tab_settlements,
)

import tpos.tasks as tpos_tasks  # type: ignore[import]
import tpos.views_api as views_api  # type: ignore[import]
import tpos.views_atm as views_atm  # type: ignore[import]
import tpos.views_inventory as views_inventory  # type: ignore[import]
import tpos.views_lnurl as views_lnurl  # type: ignore[import]
import tpos.views_onchain as views_onchain  # type: ignore[import]
import tpos.views_payments as views_payments  # type: ignore[import]
import tpos.views_wrapper as views_wrapper  # type: ignore[import]
from tpos.crud import (  # type: ignore[import]
    create_tpos_payment,
    get_tpos,
    get_tpos_payment_by_hash,
    update_tpos,
)
from tpos.models import TposPayment  # type: ignore[import]
from tpos.tasks import (  # type: ignore[import]
    on_invoice_paid,
)

from . import internal_invoice_queue


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
    fiat_wallet = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    headers = {"X-API-KEY": wallet.adminkey}

    listed_empty = await client.get("/tpos/api/v1/tposs", headers=headers)
    assert listed_empty.status_code == 200
    assert listed_empty.json() == []

    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(
            currency="EUR", allow_cash_settlement=True, fiat_wallet_id=fiat_wallet.id
        ),
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
async def test_manual_invoice_tax_value_extracts_inclusive_tax(
    client: AsyncClient,
):
    _user, wallet = await _user_with_tabs("manualtaxtest")
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(currency="EUR", tax_default=21),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    response = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
        json={"amount": 350, "exchange_rate": 100},
    )
    assert response.status_code == 201

    payment = await get_standalone_payment(
        response.json()["payment_hash"], incoming=True
    )
    assert payment is not None
    details = payment.extra["details"]
    assert details["taxIncluded"] is True
    assert details["taxValue"] == pytest.approx(3.5 * 0.21 / 1.21)


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
async def test_tpos_tabs_reject_foreign_wallet_tab(client: AsyncClient):
    user, wallet = await _user_with_tabs("tabswalletuser")
    second_wallet = await create_wallet(user_id=user.id)

    first_tpos = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(tabs_enabled=True, tabs_allow_create=True),
        headers={"X-API-KEY": wallet.adminkey},
    )
    second_tpos = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(tabs_enabled=True, tabs_allow_create=True),
        headers={"X-API-KEY": second_wallet.adminkey},
    )
    assert first_tpos.status_code == second_tpos.status_code == 201

    foreign_tab = await client.post(
        f"/tpos/api/v1/tposs/{second_tpos.json()['id']}/tabs",
        json={"name": "Other wallet"},
    )
    assert foreign_tab.status_code == 200

    charge = await client.post(
        f"/tpos/api/v1/tposs/{first_tpos.json()['id']}/tabs/{foreign_tab.json()['id']}/charges",
        json={"amount": 1, "idempotency_key": "foreign-tab-charge"},
    )
    assert charge.status_code == 404

    settlement = await client.post(
        f"/tpos/api/v1/tposs/{first_tpos.json()['id']}/invoices",
        json={
            "amount": 1,
            "tab_settlement": {
                "tab_id": foreign_tab.json()["id"],
                "amount": 1,
                "idempotency_key": "foreign-tab-settlement",
            },
        },
    )
    assert settlement.status_code == 404


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
async def test_paid_tpos_invoice_settles_tab_via_real_tabs_api(
    client: AsyncClient, monkeypatch
):
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
    paid_messages = []

    async def fake_paid_websocket(channel, message):
        paid_messages.append((channel, json.loads(message)))

    monkeypatch.setattr(tpos_tasks, "websocket_updater", fake_paid_websocket)
    await on_invoice_paid(payment)

    assert {channel for channel, _message in paid_messages} >= {
        tpos["id"],
        invoice["payment_hash"],
    }
    assert all(message["pending"] is False for _channel, message in paid_messages)
    assert all(
        message["payment_method"] == "lightning" for _channel, message in paid_messages
    )

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
    assert settlements[0].idempotency_key == f"tpos:{payment.payment_hash}"


@pytest.mark.asyncio
async def test_lnaddress_forwarding_uses_whole_sat_amount(
    client: AsyncClient, monkeypatch
):
    _user, wallet = await _user_with_tabs("lnaddressuser")
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(lnaddress=True, lnaddress_cut=0),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()
    payment_hash = uuid4().hex
    payment = await create_payment(
        f"checking-{payment_hash}",
        CreatePayment(
            wallet_id=wallet.id,
            payment_hash=payment_hash,
            bolt11="bolt11",
            amount_msat=395_920,
            memo="lnaddress sale",
            extra={
                "tag": "tpos",
                "tpos_id": tpos["id"],
                "lnaddress": "user@example.com",
            },
        ),
        status=PaymentState.SUCCESS,
    )
    requested_amounts = []

    await create_tpos_payment(
        TposPayment(id=uuid4().hex, tpos_id=tpos["id"], payment_hash=payment_hash)
    )

    async def fake_get_pr_from_lnurl(address, amount):
        assert address == "user@example.com"
        requested_amounts.append(amount)
        return "bolt11-forward"

    async def fake_pay_invoice(**_kwargs):
        return payment

    async def fake_websocket_updater(*_args):
        return None

    monkeypatch.setattr(tpos_tasks, "get_pr_from_lnurl", fake_get_pr_from_lnurl)
    monkeypatch.setattr(tpos_tasks, "pay_invoice", fake_pay_invoice)
    monkeypatch.setattr(tpos_tasks, "websocket_updater", fake_websocket_updater)

    await on_invoice_paid(payment)

    assert requested_amounts == [395_000]


@pytest.mark.asyncio
async def test_remote_invoice_payload_keeps_fiat_tip_amount(
    client: AsyncClient, monkeypatch
):
    _user, wallet = await _user_with_tabs("remotetipuser")
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(currency="USD", enable_remote=True),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()
    sent_messages = []

    async def fake_websocket_updater(channel, message):
        sent_messages.append((channel, json.loads(message)))

    monkeypatch.setattr(views_payments, "websocket_updater", fake_websocket_updater)
    invoice_response = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
        json={
            "amount": 3700,
            "tip_amount": 3700,
            "amount_fiat": 0.46,
            "tip_amount_fiat": 0.02,
            "exchange_rate": 80000,
            "memo": "$0.46 with 5% tip",
            "pay_in_fiat": False,
        },
    )

    assert invoice_response.status_code == 201
    assert sent_messages[0][1]["tip_amount"] == 3700
    assert sent_messages[0][1]["tip_amount_fiat"] == 0.02


@pytest.mark.asyncio
async def test_terminal_invoice_keeps_reader_and_tap_to_pay_payload(
    client: AsyncClient, monkeypatch, enable_stripe
):
    user, wallet = await _user_with_tabs("terminaluser")
    enable_stripe(user.id)
    fiat_wallet = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="USD"
    )
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(
            currency="USD",
            fiat_provider="stripe",
            fiat_wallet_id=fiat_wallet.id,
            stripe_card_payments=True,
            stripe_reader_id="reader_test",
        ),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    payment_hash = uuid4().hex
    payment = await create_payment(
        "pi_test",
        CreatePayment(
            wallet_id=fiat_wallet.id,
            payment_hash=payment_hash,
            bolt11="bolt11",
            amount_msat=6_000,
            memo="Terminal test",
            extra={
                "fiat_checking_id": "pi_test",
                "fiat_payment_request": "pi_test_secret_test",
            },
        ),
        status=PaymentState.PENDING,
    )
    captured_invoice_data = []

    async def fake_create_payment_request(wallet_id, invoice_data):
        assert wallet_id == fiat_wallet.id
        captured_invoice_data.append(invoice_data)
        return payment

    sent_messages = []

    async def fake_websocket_updater(channel, message):
        sent_messages.append((channel, json.loads(message)))

    monkeypatch.setattr(
        views_payments, "create_payment_request", fake_create_payment_request
    )
    monkeypatch.setattr(views_payments, "websocket_updater", fake_websocket_updater)

    invoice_response = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
        json={
            "amount": 400,
            "tip_amount": 80,
            "amount_fiat": 5,
            "tip_amount_fiat": 1,
            "exchange_rate": 80,
            "memo": "$5 with tip",
            "pay_in_fiat": True,
            "fiat_method": "terminal",
        },
    )

    assert invoice_response.status_code == 201
    assert invoice_response.json()["payment_request"] == "tap_to_pay"
    invoice_data = captured_invoice_data[0]
    assert invoice_data.unit == "USD"
    assert invoice_data.amount == 6
    assert invoice_data.fiat_provider == "stripe"
    assert invoice_data.extra["terminal"] == {"reader_id": "reader_test"}

    assert [message["type"] for _channel, message in sent_messages] == [
        "invoice_created",
        "tap_to_pay",
    ]
    tap_to_pay = sent_messages[1][1]
    assert tap_to_pay == {
        "type": "tap_to_pay",
        "payment_intent_id": "pi_test",
        "client_secret": "pi_test_secret_test",
        "currency": "usd",
        "amount": 600,
        "tpos_id": tpos["id"],
        "payment_hash": payment.payment_hash,
        "paid": False,
    }


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


@pytest.mark.asyncio
async def test_wrapper_inventory_onchain_status_endpoints(
    client: AsyncClient, monkeypatch
):
    user, wallet = await _user_with_tabs("statususer")
    headers = {"X-API-KEY": wallet.adminkey}

    async def fake_assetlinks():
        return [{"relation": ["delegate_permission/common.handle_all_urls"]}]

    monkeypatch.setattr(views_wrapper, "fetch_wrapper_assetlinks", fake_assetlinks)
    assetlinks = await client.get("/tpos/api/v1/well-known/assetlinks.json")
    assert assetlinks.status_code == 200
    assert assetlinks.json()[0]["relation"] == [
        "delegate_permission/common.handle_all_urls"
    ]

    monkeypatch.setattr(
        views_inventory, "inventory_available_for_user", lambda user: False
    )
    inventory_disabled = await client.get(
        "/tpos/api/v1/inventory/status", headers=headers
    )
    assert inventory_disabled.status_code == 200
    assert inventory_disabled.json() == {
        "enabled": False,
        "inventory_id": None,
        "tags": [],
        "omit_tags": [],
    }

    async def fake_default_inventory(user_id):
        assert user_id == user.id
        return {"id": "inv1", "tags": "coffee,tea", "omit_tags": "hidden"}

    monkeypatch.setattr(
        views_inventory, "inventory_available_for_user", lambda user: True
    )
    monkeypatch.setattr(
        views_inventory, "get_default_inventory", fake_default_inventory
    )
    inventory_enabled = await client.get(
        "/tpos/api/v1/inventory/status", headers=headers
    )
    assert inventory_enabled.status_code == 200
    assert inventory_enabled.json() == {
        "enabled": True,
        "inventory_id": "inv1",
        "tags": ["coffee", "tea"],
        "omit_tags": ["hidden"],
    }

    async def fake_watchonly_status(wallet):
        return False

    monkeypatch.setattr(
        views_onchain, "watchonly_available_for_user", fake_watchonly_status
    )
    onchain = await client.get("/tpos/api/v1/onchain/status", headers=headers)
    assert onchain.status_code == 200
    assert onchain.json()["available"] is False


@pytest.mark.asyncio
async def test_inventory_items_and_lnaddress_check(client: AsyncClient, monkeypatch):
    _user, wallet = await _user_with_tabs("inventoryuser")
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs", json=_tpos_payload(), headers=headers
    )
    assert create.status_code == 201
    tpos = await get_tpos(create.json()["id"])
    assert tpos is not None
    tpos.use_inventory = True
    tpos.inventory_id = "inv1"
    tpos.inventory_tags = "coffee"
    tpos.inventory_omit_tags = "hidden"
    await update_tpos(tpos)

    async def unexpected_default_inventory(_user_id):
        raise AssertionError(
            "Configured inventory must not fetch the default inventory."
        )

    monkeypatch.setattr(
        views_inventory, "get_default_inventory", unexpected_default_inventory
    )

    async def fake_inventory_items(user_id, inventory_id, tags, omit_tags):
        assert inventory_id == "inv1"
        assert tags == "coffee"
        assert omit_tags == "hidden"
        return [
            {
                "id": "item1",
                "name": "Coffee",
                "description": "Hot",
                "price": 250,
                "tax_rate": 10,
                "images": ["https://example.com/coffee.png"],
                "tags": "coffee",
                "quantity_in_stock": 3,
                "is_active": True,
            }
        ]

    monkeypatch.setattr(
        views_inventory, "get_inventory_items_for_tpos", fake_inventory_items
    )
    items = await client.get(f"/tpos/api/v1/tposs/{tpos.id}/inventory-items")
    assert items.status_code == 200
    assert items.json()[0] == {
        "id": "item1",
        "title": "Coffee",
        "description": "Hot",
        "price": 250,
        "tax": 10,
        "image": "https://example.com/coffee.png",
        "categories": ["coffee"],
        "quantity_in_stock": 3,
        "disabled": False,
    }

    async def bad_lnaddress(_lnaddress):
        return object()

    monkeypatch.setattr(views_api.core_lnurl, "handle", bad_lnaddress)
    lnaddress = await client.get(
        "/tpos/api/v1/tposs/lnaddresscheck?lnaddress=alice@example.com"
    )
    assert lnaddress.status_code == 400
    assert "unexpected response type" in lnaddress.json()["detail"]


@pytest.mark.asyncio
async def test_cash_validate_and_print_invoice_endpoints(
    client: AsyncClient, monkeypatch, fixed_fiat_rate
):
    await _drain_internal_invoice_queue()
    user, wallet = await _user_with_tabs("cashuser")
    fiat_wallet = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(
            currency="EUR", allow_cash_settlement=True, fiat_wallet_id=fiat_wallet.id
        ),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    lightning_total = await get_total_balance()
    funding_source = Mock(side_effect=AssertionError("Cash must not use Lightning"))
    monkeypatch.setattr(
        "lnbits.core.services.payments.get_funding_source", funding_source
    )
    sale = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
        json={
            "amount": 1000,
            "amount_fiat": 1,
            "pay_in_fiat": True,
            "fiat_method": "cash",
        },
    )
    assert sale.status_code == 201, sale.text
    invoice = sale.json()
    assert invoice["payment_request"] == "cash"
    assert not invoice["bolt11"]
    payment = await get_standalone_payment(invoice["payment_hash"], incoming=True)
    assert payment and payment.wallet_id == fiat_wallet.id and payment.pending

    sent_messages = []

    async def fake_websocket_updater(channel, message):
        sent_messages.append((channel, message))

    monkeypatch.setattr(views_payments, "websocket_updater", fake_websocket_updater)
    printed = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}/print",
        json={"receipt_type": "receipt"},
    )
    assert printed.status_code == 200
    assert printed.json() == {"success": True}
    order_printed = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}/print",
        json={"receipt_type": "order_receipt"},
    )
    assert order_printed.status_code == 200
    assert order_printed.json() == {"success": True}
    assert sent_messages
    assert {
        json.loads(message)["receipt_type"] for _channel, message in sent_messages
    } == {
        "receipt",
        "order_receipt",
    }

    poll = await client.get(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}?extra=true"
    )
    assert poll.status_code == 200
    assert poll.json()["extra"]["fiat_method"] == "cash"

    queued_checking_ids = []

    async def fake_internal_invoice_queue_put(checking_id):
        queued_checking_ids.append(checking_id)

    monkeypatch.setattr(
        views_payments, "internal_invoice_queue_put", fake_internal_invoice_queue_put
    )
    validated = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}/cash/validate"
    )
    assert validated.status_code == 200
    assert validated.json() == {"success": True}

    settled_payment = await get_standalone_payment(payment.payment_hash, incoming=True)
    assert settled_payment is not None
    assert settled_payment.success is True

    await on_invoice_paid(settled_payment)

    retried = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}/cash/validate"
    )
    assert retried.status_code == 200
    expected_checking_id = f"internal_cash_{payment.payment_hash}"
    assert queued_checking_ids == [expected_checking_id]
    credited = await get_wallet(fiat_wallet.id)
    lightning = await get_wallet(wallet.id)
    assert credited and credited.balance_msat == 1_000_000
    assert credited.withdrawable_balance == 0
    assert lightning and lightning.balance_msat == wallet.balance_msat
    assert await get_total_balance() == lightning_total
    funding_source.assert_not_called()

    paid_response = await client.get(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}"
    )
    assert paid_response.status_code == 200
    assert paid_response.json() == {"paid": True}

    latest_response = await client.get(f"/tpos/api/v1/tposs/{tpos['id']}/invoices")
    assert latest_response.status_code == 200
    latest = latest_response.json()
    assert latest[0]["pending"] is False
    assert latest[0]["payment_method"] == "cash"


@pytest.mark.asyncio
async def test_atm_and_lnurl_withdraw_routes(client: AsyncClient, monkeypatch):
    user, wallet = await _user_with_tabs("atmuser")
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(withdraw_limit=100),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    charge_response = await client.post(
        f"/tpos/api/v1/atm/{tpos['id']}/create",
        headers={
            "Authorization": f"Bearer {create_access_token({'sub': user.username})}"
        },
    )
    assert charge_response.status_code == 200
    charge = charge_response.json()

    await update_wallet_balance(wallet, 50_000)
    withdraw = await client.get(f"/tpos/api/v1/atm/withdraw/{charge['id']}/25")
    assert withdraw.status_code == 200
    assert withdraw.json()["amount"] == 25

    params = await client.get(
        f"/tpos/api/v1/lnurl/{charge['id']}/25",
        headers={"host": "localhost"},
    )
    assert params.status_code == 200
    assert params.json()["k1"] == charge["id"]

    async def fake_pay_invoice(**kwargs):
        return SimpleNamespace(success=True, failed=False)

    async def fake_websocket_updater(channel, message):
        return None

    async def fake_pay_tribute(withdraw_amount, wallet_id, percent=0.5):
        return None

    monkeypatch.setattr(views_lnurl, "pay_invoice", fake_pay_invoice)
    monkeypatch.setattr(views_lnurl, "websocket_updater", fake_websocket_updater)
    monkeypatch.setattr(views_lnurl, "pay_tribute", fake_pay_tribute)
    callback = await client.get(f"/tpos/api/v1/lnurl/cb?k1={charge['id']}&pr=lnbc1test")
    assert callback.status_code == 200
    assert callback.json()["status"] == "OK"

    claimed_again = await client.get(
        f"/tpos/api/v1/lnurl/cb?k1={charge['id']}&pr=lnbc1test"
    )
    assert claimed_again.status_code == 200
    assert "already been claimed" in claimed_again.json()["reason"]


@pytest.mark.asyncio
async def test_atm_create_requires_authenticated_tpos_owner(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "auth_allowed_methods", ["username-password"])
    owner_account = Account(id=uuid4().hex, username=f"atmowner_{uuid4().hex[:8]}")
    owner_account.hash_password("secret1234")
    owner = await create_user_account_no_ckeck(account=owner_account)
    other_account = Account(id=uuid4().hex, username=f"atmother_{uuid4().hex[:8]}")
    other = await create_user_account_no_ckeck(account=other_account)

    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(withdraw_limit=100),
        headers={"X-API-KEY": owner.wallets[0].adminkey},
    )
    assert create.status_code == 201
    tpos = create.json()

    anonymous_create = await client.post(f"/tpos/api/v1/atm/{tpos['id']}/create")
    assert anonymous_create.status_code == 401
    user_id_only_create = await client.post(
        f"/tpos/api/v1/atm/{tpos['id']}/create?usr={owner.id}"
    )
    assert user_id_only_create.status_code == 401

    owner_create = await client.post(
        f"/tpos/api/v1/atm/{tpos['id']}/create",
        headers={
            "Authorization": f"Bearer {create_access_token({'sub': owner.username})}"
        },
    )
    assert owner_create.status_code == 200

    authorize = await client.post(
        f"/tpos/api/v1/atm/{tpos['id']}/authorize",
        json={"password": "secret1234"},
    )
    assert authorize.status_code == 200

    invalid = await client.post(
        f"/tpos/api/v1/atm/{tpos['id']}/authorize",
        json={"password": "wrong"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "Invalid credentials."
    for _ in range(4):
        invalid = await client.post(
            f"/tpos/api/v1/atm/{tpos['id']}/authorize",
            json={"password": "wrong"},
        )
        assert invalid.status_code == 401
    locked = await client.post(
        f"/tpos/api/v1/atm/{tpos['id']}/authorize",
        json={"password": "secret1234"},
    )
    assert locked.status_code == 429

    other_create = await client.post(
        f"/tpos/api/v1/atm/{tpos['id']}/create",
        headers={
            "Authorization": f"Bearer {create_access_token({'sub': other.username})}"
        },
    )
    assert other_create.status_code == 403
    assert other_create.json()["detail"] == (
        "You do not have access to this TPoS wallet."
    )


@pytest.mark.asyncio
async def test_atm_pay_endpoint(client: AsyncClient, monkeypatch):
    user, wallet = await _user_with_tabs("atmpayuser")
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(withdraw_limit=100),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    charge_response = await client.post(
        f"/tpos/api/v1/atm/{tpos['id']}/create",
        headers={
            "Authorization": f"Bearer {create_access_token({'sub': user.username})}"
        },
    )
    assert charge_response.status_code == 200
    charge = charge_response.json()

    await update_wallet_balance(wallet, 100)
    configured = await client.get(f"/tpos/api/v1/atm/withdraw/{charge['id']}/25")
    assert configured.status_code == 200

    async def fake_get_pr_from_lnurl(pay_link, amount):
        assert amount == 25_000
        return "lnbc1test"

    async def fake_lnurl_callback(pr, k1):
        assert pr == "lnbc1test"
        assert k1 == charge["id"]
        return views_lnurl.LnurlSuccessResponse()

    monkeypatch.setattr(views_atm, "get_pr_from_lnurl", fake_get_pr_from_lnurl)
    monkeypatch.setattr(views_atm, "lnurl_callback", fake_lnurl_callback)
    paid = await client.post(
        f"/tpos/api/v1/atm/withdraw/{charge['id']}/25/pay",
        json={"pay_link": "lnurl1test"},
        headers={"host": "localhost"},
    )
    assert paid.status_code == 200
    assert paid.json() == {
        "success": True,
        "message": "Withdraw processed successfully.",
    }


@pytest.mark.asyncio
async def test_pay_invoice_lnurl_withdraw_endpoint(client: AsyncClient, monkeypatch):
    _user, wallet = await _user_with_tabs("lnurlpayuser")
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs", json=_tpos_payload(), headers=headers
    )
    assert create.status_code == 201
    tpos = create.json()

    async def fake_perform_withdraw(lnurl, payment_request):
        assert lnurl == "https://example.com/withdraw"
        assert payment_request == "lnbc1test"

    monkeypatch.setattr(views_payments, "perform_withdraw", fake_perform_withdraw)
    paid = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/lnbc1test/pay",
        json={"lnurl": "example.com/withdraw"},
    )
    assert paid.status_code == 200
    assert paid.json()["success"] is True
