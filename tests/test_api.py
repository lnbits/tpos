import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from lnbits.core.models import KeyType, Payment, PaymentState, Wallet, WalletTypeInfo
from lnbits.core.models.users import User, UserAcls, UserExtra
from lnbits.core.models.wallets import WalletExtra

from tpos import tpos_ext  # type: ignore[import]
from tpos.crud import (  # type: ignore[import]
    get_tpos,
    get_tpos_payment_by_hash,
    update_tpos_payment,
)

from ..views_api import require_admin_key, require_invoice_key
from ..views_atm import check_user_exists


def _wallet(wallet_id: str = "wallet-one", user_id: str = "user-one") -> Wallet:
    return Wallet(
        id=wallet_id,
        user=user_id,
        adminkey=f"{wallet_id}-admin",
        inkey=f"{wallet_id}-invoice",
        name=f"{wallet_id} wallet",
        balance_msat=250_000,
        extra=WalletExtra(),
    )


def _user(user_id: str, wallet: Wallet, *, super_user: bool = True) -> User:
    now = datetime.now(timezone.utc)
    return User(
        id=user_id,
        created_at=now,
        updated_at=now,
        username=f"{user_id}-name",
        wallets=[wallet],
        extensions=["tpos", "inventory", "watchonly"],
        super_user=super_user,
        fiat_providers=["stripe"],
    )


def _account(user_id: str, *, username: str | None = None, super_user: bool = True):
    return SimpleNamespace(
        id=user_id,
        username=username if username is not None else f"{user_id}-name",
        is_super_user=super_user,
        fiat_providers=["stripe"],
        extra=UserExtra(),
    )


def _tpos_payload(**overrides):
    payload = {
        "wallet": "wallet-one",
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


async def _client(monkeypatch, *, super_user: bool = True):
    wallet = _wallet()
    user = _user(wallet.user, wallet, super_user=super_user)
    account = _account(wallet.user, super_user=super_user)
    payments: dict[str, Payment] = {}
    websocket_messages: list[tuple[str, str]] = []
    queued_internal: list[str] = []
    updated_accounts: list[SimpleNamespace] = []
    updated_acls: list[UserAcls] = []
    checking_updates: list[tuple[str, str]] = []
    invoice_counter = {"value": 0}

    app = FastAPI()
    app.include_router(tpos_ext)

    async def fake_admin_key():
        return WalletTypeInfo(KeyType.admin, wallet)

    async def fake_invoice_key():
        return WalletTypeInfo(KeyType.invoice, wallet)

    async def fake_user():
        return user

    async def fake_get_user(user_id: str):
        return user if user_id == user.id else None

    async def fake_get_wallet(wallet_id: str):
        return wallet if wallet_id == wallet.id else None

    async def fake_get_account(user_id: str):
        return account if user_id == account.id else None

    async def fake_create_payment_request(wallet_id, invoice_data):
        invoice_counter["value"] += 1
        payment_hash = f"payment-hash-{invoice_counter['value']}"
        payment = Payment(
            checking_id=f"checking-{invoice_counter['value']}",
            payment_hash=payment_hash,
            wallet_id=wallet_id,
            amount=int(invoice_data.amount * 1000),
            fee=0,
            bolt11=f"lnbc{invoice_counter['value']}",
            status=PaymentState.PENDING,
            memo=invoice_data.memo,
            fiat_provider=invoice_data.fiat_provider,
            labels=invoice_data.labels,
            extra=invoice_data.extra or {},
        )
        payments[payment_hash] = payment
        return payment

    async def fake_get_standalone_payment(payment_hash: str, incoming: bool = True):
        return payments.get(payment_hash)

    async def fake_websocket_updater(channel: str, message: str):
        websocket_messages.append((channel, message))

    async def fake_internal_invoice_queue_put(checking_id: str):
        queued_internal.append(checking_id)

    async def fake_update_payment_checking_id(old: str, new: str):
        checking_updates.append((old, new))

    async def fake_update_account(updated):
        updated_accounts.append(updated)
        return updated

    async def fake_user_acls(user_id: str):
        return UserAcls(id=user_id)

    async def fake_update_user_acls(user_acls):
        updated_acls.append(user_acls)
        return user_acls

    app.dependency_overrides[require_admin_key] = fake_admin_key
    app.dependency_overrides[require_invoice_key] = fake_invoice_key
    app.dependency_overrides[check_user_exists] = fake_user
    monkeypatch.setattr("tpos.views_api.get_user", fake_get_user)
    monkeypatch.setattr("tpos.views_api.get_wallet", fake_get_wallet)
    monkeypatch.setattr("tpos.views_api.get_account", fake_get_account)
    monkeypatch.setattr("tpos.views_atm.get_wallet", fake_get_wallet)
    monkeypatch.setattr(
        "tpos.views_api.create_payment_request", fake_create_payment_request
    )
    monkeypatch.setattr(
        "tpos.views_api.get_standalone_payment", fake_get_standalone_payment
    )
    monkeypatch.setattr("tpos.views_api.websocket_updater", fake_websocket_updater)
    monkeypatch.setattr(
        "tpos.views_api.internal_invoice_queue_put", fake_internal_invoice_queue_put
    )
    monkeypatch.setattr(
        "tpos.views_api.update_payment_checking_id", fake_update_payment_checking_id
    )
    monkeypatch.setattr("tpos.views_api.update_account", fake_update_account)
    monkeypatch.setattr("tpos.views_api.get_user_access_control_lists", fake_user_acls)
    monkeypatch.setattr(
        "tpos.views_api.update_user_access_control_list", fake_update_user_acls
    )
    monkeypatch.setattr("tpos.views_lnurl.websocket_updater", fake_websocket_updater)

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    client = AsyncClient(transport=transport, base_url="http://testserver")
    ctx = SimpleNamespace(
        wallet=wallet,
        user=user,
        account=account,
        payments=payments,
        websocket_messages=websocket_messages,
        queued_internal=queued_internal,
        checking_updates=checking_updates,
        updated_accounts=updated_accounts,
        updated_acls=updated_acls,
    )
    return client, ctx


def _mark_paid(payment: Payment):
    payment.status = PaymentState.SUCCESS
    payment.extra = {**(payment.extra or {}), "tag": "tpos"}
    return payment


def test_tpos_crud_settings_and_wrapper_token(monkeypatch):
    async def _run():
        client, ctx = await _client(monkeypatch)
        try:
            assert (await client.get("/tpos/api/v1/tposs")).json() == []

            inventory_status = await client.get("/tpos/api/v1/inventory/status")
            assert inventory_status.status_code == 200
            assert inventory_status.json()["enabled"] is True

            create = await client.post("/tpos/api/v1/tposs", json=_tpos_payload())
            assert create.status_code == 201
            tpos = create.json()
            assert tpos["wallet"] == ctx.wallet.id
            assert tpos["allow_cash_settlement"] is False

            listed = await client.get("/tpos/api/v1/tposs?all_wallets=true")
            assert listed.status_code == 200
            assert [item["id"] for item in listed.json()] == [tpos["id"]]

            update = await client.put(
                f"/tpos/api/v1/tposs/{tpos['id']}",
                json=_tpos_payload(
                    name="Updated TPoS",
                    currency="EUR",
                    allow_cash_settlement=True,
                    inventory_tags=["coffee", "tea"],
                    inventory_omit_tags=["hidden"],
                ),
            )
            assert update.status_code == 200
            updated = update.json()
            assert updated["name"] == "Updated TPoS"
            assert updated["allow_cash_settlement"] is True
            assert updated["inventory_tags"] == "coffee,tea"

            token = await client.post(f"/tpos/api/v1/tposs/{tpos['id']}/wrapper-token")
            assert token.status_code == 200
            assert token.json()["auth"]
            assert token.json()["expiration_time_minutes"] > 500_000
            assert ctx.updated_acls

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
            )
            assert items.status_code == 201
            assert json.loads(items.json()["items"])[0]["title"] == "Coffee"

            delete = await client.delete(f"/tpos/api/v1/tposs/{tpos['id']}")
            assert delete.status_code == 200
            assert await get_tpos(tpos["id"]) is None
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_tpos_rejects_unauthorized_or_invalid_settings(monkeypatch):
    async def _run():
        client, _ctx = await _client(monkeypatch, super_user=False)
        try:
            create = await client.post(
                "/tpos/api/v1/tposs",
                json=_tpos_payload(currency="EUR", allow_cash_settlement=True),
            )
            assert create.status_code == 201
            tpos = create.json()
            assert tpos["allow_cash_settlement"] is False

            denied = await client.put(
                f"/tpos/api/v1/tposs/{tpos['id']}",
                json=_tpos_payload(currency="EUR", allow_cash_settlement=True),
            )
            assert denied.status_code == 403

            missing = await client.delete(f"/tpos/api/v1/tposs/{uuid4().hex}")
            assert missing.status_code == 404
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_invoice_payment_receipt_print_and_cash_endpoints(monkeypatch):
    async def _run():
        client, ctx = await _client(monkeypatch)
        try:
            create = await client.post(
                "/tpos/api/v1/tposs",
                json=_tpos_payload(currency="EUR", allow_cash_settlement=True),
            )
            tpos = create.json()

            invoice = await client.post(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
                json={
                    "amount": 100,
                    "memo": "Latte",
                    "exchange_rate": 2,
                    "details": {
                        "currency": "EUR",
                        "exchangeRate": 2,
                        "taxValue": 0.5,
                        "taxIncluded": True,
                        "items": [{"title": "Latte", "quantity": 1, "price": 5}],
                    },
                },
            )
            assert invoice.status_code == 201
            invoice_payload = invoice.json()
            payment_hash = invoice_payload["payment_hash"]
            assert invoice_payload["payment_request"].startswith("lightning:")
            assert invoice_payload["payment_options"] == ["lightning"]
            assert ctx.websocket_messages

            pending = await client.get(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{payment_hash}"
            )
            assert pending.status_code == 200
            assert pending.json() == {"paid": False}

            _mark_paid(ctx.payments[payment_hash])
            tpos_payment = await get_tpos_payment_by_hash(payment_hash)
            assert tpos_payment is not None
            await update_tpos_payment(tpos_payment.copy(update={"paid": True}))

            latest = await client.get(f"/tpos/api/v1/tposs/{tpos['id']}/invoices")
            assert latest.status_code == 200
            assert latest.json()[0]["pending"] is False

            receipt = await client.get(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{payment_hash}?extra=true"
            )
            assert receipt.status_code == 200
            assert receipt.json()["paid"] is True
            assert receipt.json()["business_name"] == "Main Shop"

            printed = await client.post(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{payment_hash}/print",
                json={"receipt_type": "order_receipt"},
            )
            assert printed.status_code == 200
            assert printed.json()["success"] is True

            cash_invoice = await client.post(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
                json={
                    "amount": 100,
                    "pay_in_fiat": True,
                    "fiat_method": "cash",
                    "amount_fiat": 5,
                },
            )
            assert cash_invoice.status_code == 201, cash_invoice.text
            cash_hash = cash_invoice.json()["payment_hash"]
            assert ctx.checking_updates[-1][1] == f"internal_cash_{cash_hash}"
            cash_validate = await client.post(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{cash_hash}/cash/validate"
            )
            assert cash_validate.status_code == 200
            assert cash_validate.json()["success"] is True
            assert ctx.queued_internal == [f"internal_cash_{cash_hash}"]

            wrong_cash = await client.post(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{payment_hash}/cash/validate"
            )
            assert wrong_cash.status_code == 400
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_invoice_error_paths_and_lnurl_pay_endpoint(monkeypatch):
    async def _run():
        client, _ctx = await _client(monkeypatch)
        try:
            create = await client.post("/tpos/api/v1/tposs", json=_tpos_payload())
            tpos = create.json()

            missing_tpos_invoice = await client.post(
                f"/tpos/api/v1/tposs/{uuid4().hex}/invoices",
                json={"amount": 1},
            )
            assert missing_tpos_invoice.status_code == 404

            cash_denied = await client.post(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
                json={
                    "amount": 100,
                    "pay_in_fiat": True,
                    "fiat_method": "cash",
                    "amount_fiat": 5,
                },
            )
            assert cash_denied.status_code == 403

            missing_payment = await client.get(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices/missing"
            )
            assert missing_payment.status_code == 404
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_inventory_endpoints(monkeypatch):
    async def _run():
        client, _ctx = await _client(monkeypatch)
        inventory = {
            "id": "inventory-one",
            "tags": "coffee,tea",
            "omit_tags": "hidden",
        }
        inventory_items = [
            {
                "id": "item-one",
                "name": "Coffee",
                "description": "Hot",
                "price": 3,
                "tax_rate": 10,
                "images": ["image-one"],
                "tags": "coffee",
                "quantity_in_stock": 5,
                "is_active": True,
            }
        ]

        async def fake_default_inventory(user_id: str):
            return inventory

        async def fake_inventory_items(*args, **kwargs):
            return inventory_items

        monkeypatch.setattr(
            "tpos.views_api.get_default_inventory", fake_default_inventory
        )
        monkeypatch.setattr(
            "tpos.views_api.get_inventory_items_for_tpos", fake_inventory_items
        )
        try:
            status = await client.get("/tpos/api/v1/inventory/status")
            assert status.status_code == 200
            assert status.json()["inventory_id"] == "inventory-one"
            assert status.json()["tags"] == ["coffee", "tea"]

            create = await client.post(
                "/tpos/api/v1/tposs",
                json=_tpos_payload(
                    currency="EUR",
                    use_inventory=True,
                ),
            )
            assert create.status_code == 201
            tpos = create.json()
            assert tpos["inventory_id"] == "inventory-one"

            items = await client.get(f"/tpos/api/v1/tposs/{tpos['id']}/inventory-items")
            assert items.status_code == 200
            assert items.json()[0]["title"] == "Coffee"
            assert items.json()[0]["image"].endswith(
                "/api/v1/assets/image-one/thumbnail"
            )

            mismatch_inventory = await client.post(
                f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
                json={
                    "amount": 100,
                    "inventory": {
                        "inventory_id": "other",
                        "tags": ["coffee"],
                        "items": [{"id": "item-one", "quantity": 1}],
                    },
                },
            )
            assert mismatch_inventory.status_code == 400

        finally:
            await client.aclose()

    asyncio.run(_run())
