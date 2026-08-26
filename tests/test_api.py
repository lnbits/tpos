import asyncio
import json
from uuid import uuid4

import pytest
from httpx import AsyncClient
from lnbits.core.crud import get_standalone_payment
from lnbits.core.crud.payments import create_payment, update_payment_checking_id
from lnbits.core.crud.wallets import create_wallet
from lnbits.core.models import CreateInvoice, CreatePayment, PaymentState
from lnbits.core.models.users import Account
from lnbits.core.services import (
    create_payment_request,
    pay_invoice,
    update_wallet_balance,
)
from lnbits.core.services.users import create_user_account_no_ckeck
from lnbits.settings import settings
from lnbits.tasks import internal_invoice_queue
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
    settle_onchain_tpos_payment,
)


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
    assert settlements[0].idempotency_key == "tpos-settlement-1"


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
    client: AsyncClient, monkeypatch
):
    _user, wallet = await _user_with_tabs("terminaluser")
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(
            currency="USD",
            fiat_provider="stripe",
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
            wallet_id=wallet.id,
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
        assert wallet_id == wallet.id
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
async def test_onchain_invoice_option_creates_internal_payment(
    client: AsyncClient, monkeypatch
):
    user, wallet = await _user_with_tabs("onchainuser")
    settings.super_user = user.id
    headers = {"X-API-KEY": wallet.adminkey}

    async def fake_watchonly_settings(**kwargs):
        return {"mempool_endpoint": "https://mempool.example"}

    async def fake_onchain_address(inkey, wallet_id):
        assert wallet_id == "watch-wallet"
        return {"address": "bc1qtposaddress"}

    monkeypatch.setattr(
        views_api, "_validate_watchonly_settings", fake_watchonly_settings
    )
    monkeypatch.setattr(
        views_payments, "_validate_watchonly_settings", fake_watchonly_settings
    )
    monkeypatch.setattr(views_payments, "fetch_onchain_address", fake_onchain_address)

    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(onchain_enabled=True, onchain_wallet_id="watch-wallet"),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    invoice_response = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
        json={"amount": 42, "memo": "Onchain", "payment_method": "btc_onchain"},
    )
    assert invoice_response.status_code == 201
    invoice = invoice_response.json()
    assert invoice["payment_request"] == "bc1qtposaddress"
    assert invoice["payment_options"] == ["btc", "btc_onchain"]
    assert invoice["payment_method"] == "onchain"

    payment = await get_standalone_payment(invoice["payment_hash"], incoming=True)
    assert payment is not None
    assert payment.is_internal
    assert payment.checking_id.startswith("internal_onchain_")

    tpos_payment = await get_tpos_payment_by_hash(invoice["payment_hash"])
    assert tpos_payment is not None
    assert tpos_payment.onchain_address == "bc1qtposaddress"
    assert tpos_payment.mempool_endpoint == "https://mempool.example"

    queued_checking_ids = []

    async def fake_internal_invoice_queue_put(checking_id):
        queued_checking_ids.append(checking_id)

    monkeypatch.setattr(
        tpos_tasks, "internal_invoice_queue_put", fake_internal_invoice_queue_put
    )
    await settle_onchain_tpos_payment(tpos_payment)
    settled_payment = await get_standalone_payment(
        invoice["payment_hash"], incoming=True
    )
    assert settled_payment is not None
    assert settled_payment.success is True
    unsettled_tpos_payment = await get_tpos_payment_by_hash(invoice["payment_hash"])
    assert unsettled_tpos_payment is not None
    assert unsettled_tpos_payment.paid is False

    paid_response = await client.get(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}"
    )
    assert paid_response.status_code == 200
    assert paid_response.json() == {"paid": True}

    await settle_onchain_tpos_payment(tpos_payment)
    assert queued_checking_ids == [payment.checking_id, payment.checking_id]


@pytest.mark.asyncio
async def test_poll_onchain_payments_balance_updates_and_settlement(
    client: AsyncClient, monkeypatch
):
    user, wallet = await _user_with_tabs("polleruser")
    settings.super_user = user.id
    headers = {"X-API-KEY": wallet.adminkey}

    async def fake_watchonly_settings(**kwargs):
        return {"mempool_endpoint": "https://mempool.example"}

    async def fake_onchain_address(inkey, wallet_id):
        return {"address": "bc1qtposaddress"}

    monkeypatch.setattr(
        views_api, "_validate_watchonly_settings", fake_watchonly_settings
    )
    monkeypatch.setattr(
        views_payments, "_validate_watchonly_settings", fake_watchonly_settings
    )
    monkeypatch.setattr(views_payments, "fetch_onchain_address", fake_onchain_address)

    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(onchain_enabled=True, onchain_wallet_id="watch-wallet"),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    invoice_response = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
        json={"amount": 42, "memo": "Onchain", "payment_method": "btc_onchain"},
    )
    assert invoice_response.status_code == 201
    payment_hash = invoice_response.json()["payment_hash"]

    # single poll iteration: break the loop when it sleeps
    class PollingStoppedError(Exception):
        pass

    async def fake_sleep(_seconds):
        raise PollingStoppedError

    monkeypatch.setattr(tpos_tasks.asyncio, "sleep", fake_sleep)

    balance = {"confirmed": 0, "unconfirmed": 0}

    async def fake_onchain_balance(endpoint, address):
        assert endpoint == "https://mempool.example"
        assert address == "bc1qtposaddress"
        return dict(balance)

    sent_messages = []

    async def fake_websocket_updater(channel, message):
        sent_messages.append((channel, json.loads(message)))

    settle_calls = []

    async def fake_settle(tpos_payment):
        settle_calls.append(tpos_payment.payment_hash)

    monkeypatch.setattr(tpos_tasks, "fetch_onchain_balance", fake_onchain_balance)
    monkeypatch.setattr(tpos_tasks, "websocket_updater", fake_websocket_updater)
    monkeypatch.setattr(tpos_tasks, "settle_onchain_tpos_payment", fake_settle)

    async def poll_once():
        with pytest.raises(PollingStoppedError):
            await tpos_tasks.poll_onchain_payments()

    def messages_for(hash_):
        return [
            message
            for _channel, message in sent_messages
            if message["payment_hash"] == hash_
        ]

    # iteration 1: nothing changed, no broadcast, no settlement
    await poll_once()
    assert messages_for(payment_hash) == []
    assert payment_hash not in settle_calls

    # iteration 2: unconfirmed balance only -> broadcast, no settlement
    balance["unconfirmed"] = 20
    await poll_once()
    message = messages_for(payment_hash)[0]
    assert message == {
        "pending": True,
        "payment_hash": payment_hash,
        "onchain_balance": 20,
        "onchain_pending": 20,
        "payment_method": None,
    }
    assert payment_hash not in settle_calls

    # iteration 3: confirmed amount reached -> broadcast, settlement triggered.
    # TposPayment.paid stays False by design: the invoice listener marks it.
    balance["confirmed"] = 42
    balance["unconfirmed"] = 0
    await poll_once()
    message = messages_for(payment_hash)[1]
    assert message == {
        "pending": True,
        "payment_hash": payment_hash,
        "onchain_balance": 42,
        "onchain_pending": 0,
        "payment_method": "onchain",
    }
    assert settle_calls.count(payment_hash) == 1

    tpos_payment = await get_tpos_payment_by_hash(payment_hash)
    assert tpos_payment is not None
    assert tpos_payment.balance == 42
    assert tpos_payment.pending == 0
    assert tpos_payment.paid is False


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

    monkeypatch.setattr(views_api, "lnurl_handle", bad_lnaddress)
    lnaddress = await client.get(
        "/tpos/api/v1/tposs/lnaddresscheck?lnaddress=alice@example.com"
    )
    assert lnaddress.status_code == 400
    assert "unexpected response type" in lnaddress.json()["detail"]


@pytest.mark.asyncio
async def test_cash_validate_and_print_invoice_endpoints(
    client: AsyncClient, monkeypatch
):
    await _drain_internal_invoice_queue()
    user, wallet = await _user_with_tabs("cashuser")
    settings.super_user = user.id
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(currency="EUR", allow_cash_settlement=True),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    payment = await create_payment_request(
        wallet.id,
        CreateInvoice(
            unit="sat",
            out=False,
            amount=10,
            memo="Cash sale",
            internal=True,
            extra={
                "tag": "tpos",
                "tpos_id": tpos["id"],
                "amount": 10,
                "fiat_method": "cash",
                "details": {
                    "currency": "EUR",
                    "exchangeRate": 1,
                    "taxValue": 0,
                    "taxIncluded": True,
                    "items": [],
                },
            },
        ),
    )
    await update_payment_checking_id(
        payment.checking_id, f"internal_cash_{payment.payment_hash}"
    )
    await create_tpos_payment(
        TposPayment(
            id=uuid4().hex,
            tpos_id=tpos["id"],
            payment_hash=payment.payment_hash,
            amount=10,
            payment_method="cash",
        )
    )
    invoice = {"payment_hash": payment.payment_hash}

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

    retried = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}/cash/validate"
    )
    assert retried.status_code == 200
    expected_checking_id = f"internal_cash_{payment.payment_hash}"
    assert queued_checking_ids == [expected_checking_id, expected_checking_id]

    await on_invoice_paid(settled_payment)

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
async def test_custom_validate_invoice_endpoint(client: AsyncClient, monkeypatch):
    await _drain_internal_invoice_queue()
    user, wallet = await _user_with_tabs("customuser")
    settings.super_user = user.id
    headers = {"X-API-KEY": wallet.adminkey}
    create = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(currency="EUR", allow_cash_settlement=True),
        headers=headers,
    )
    assert create.status_code == 201
    tpos = create.json()

    payment = await create_payment_request(
        wallet.id,
        CreateInvoice(
            unit="sat",
            out=False,
            amount=10,
            memo="Custom sale",
            internal=True,
            extra={
                "tag": "tpos",
                "tpos_id": tpos["id"],
                "amount": 10,
                "fiat_method": "custom",
                "details": {
                    "currency": "EUR",
                    "exchangeRate": 1,
                    "taxValue": 0,
                    "taxIncluded": True,
                    "items": [],
                },
            },
        ),
    )
    await update_payment_checking_id(
        payment.checking_id, f"internal_custom_{payment.payment_hash}"
    )
    await create_tpos_payment(
        TposPayment(
            id=uuid4().hex,
            tpos_id=tpos["id"],
            payment_hash=payment.payment_hash,
            amount=10,
            payment_method="custom",
        )
    )

    queued_checking_ids = []

    async def fake_internal_invoice_queue_put(checking_id):
        queued_checking_ids.append(checking_id)

    monkeypatch.setattr(
        views_payments, "internal_invoice_queue_put", fake_internal_invoice_queue_put
    )

    # the cash route must not accept a custom invoice
    wrong_route = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{payment.payment_hash}/cash/validate"
    )
    assert wrong_route.status_code == 400
    assert queued_checking_ids == []

    validated = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}"
        f"/invoices/{payment.payment_hash}/custom/validate"
    )
    assert validated.status_code == 200
    assert validated.json() == {"success": True}
    assert queued_checking_ids == [f"internal_custom_{payment.payment_hash}"]

    settled_payment = await get_standalone_payment(payment.payment_hash, incoming=True)
    assert settled_payment is not None
    assert settled_payment.success is True

    poll = await client.get(
        f"/tpos/api/v1/tposs/{tpos['id']}"
        f"/invoices/{payment.payment_hash}?extra=true"
    )
    assert poll.status_code == 200
    assert poll.json()["extra"]["fiat_method"] == "custom"

    await on_invoice_paid(settled_payment)

    latest_response = await client.get(f"/tpos/api/v1/tposs/{tpos['id']}/invoices")
    assert latest_response.status_code == 200
    latest = latest_response.json()
    assert latest[0]["payment_method"] == "custom"


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
        f"/tpos/api/v1/atm/{tpos['id']}/create?usr={user.id}"
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
        return None

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
        f"/tpos/api/v1/atm/{tpos['id']}/create?usr={user.id}"
    )
    assert charge_response.status_code == 200
    charge = charge_response.json()

    async def fake_lnurl_handle(pay_link, user_agent=None):
        return views_atm.LnurlPayResponse(
            callback="https://example.com/cb",
            minSendable=1000,
            maxSendable=1000,
            metadata='[["text/plain","test"]]',
        )

    class FakePayResponse:
        pr = "lnbc1test"

    async def fake_execute_pay_request(response, msat, user_agent=None):
        assert msat == 25_000
        return FakePayResponse()

    async def fake_execute_withdraw(response, pr, user_agent=None):
        assert pr == "lnbc1test"
        return None

    monkeypatch.setattr(views_atm, "lnurl_handle", fake_lnurl_handle)
    monkeypatch.setattr(views_atm, "execute_pay_request", fake_execute_pay_request)
    monkeypatch.setattr(views_atm, "execute_withdraw", fake_execute_withdraw)
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

    class FakeResponse:
        is_error = False

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            if "callback" in str(url):
                return FakeResponse({"status": "OK"})
            return FakeResponse(
                {
                    "tag": "withdrawRequest",
                    "callback": "https://example.com/callback",
                    "k1": "abc",
                }
            )

    monkeypatch.setattr(
        views_payments.httpx, "AsyncClient", lambda *args, **kwargs: FakeClient()
    )
    paid = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/lnbc1test/pay",
        json={"lnurl": "example.com/withdraw"},
    )
    assert paid.status_code == 200
    assert paid.json()["success"] is True
