import json
from http import HTTPStatus
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from .. import services, tasks, views_api
from ..models import (
    CreateTposTabCharge,
    CreateTposTabData,
    Tpos,
)


def make_tpos(**overrides) -> Tpos:
    data = {
        "id": "tpos-id",
        "wallet": "wallet-id",
        "name": "Shop",
        "currency": "eur",
        "tax_inclusive": True,
        "withdraw_time": 0,
        "withdraw_between": 10,
        "enable_receipt_print": False,
        "tabs_enabled": True,
        "tabs_allow_create": True,
    }
    data.update(overrides)
    return Tpos(**data)


@pytest.mark.asyncio
async def test_ensure_tpos_tabs_access_requires_enabled():
    tpos = make_tpos(tabs_enabled=False)

    with pytest.raises(HTTPException) as exc:
        await services.ensure_tpos_tabs_access(tpos)

    assert exc.value.status_code == HTTPStatus.BAD_REQUEST
    assert exc.value.detail == "Tabs integration is not enabled for this TPoS."


@pytest.mark.asyncio
async def test_ensure_tpos_tabs_access_checks_extension(monkeypatch):
    async def fake_owner_user_id(tpos: Tpos) -> str:
        assert tpos.id == "tpos-id"
        return "user-id"

    async def fake_tabs_available(user_id: str) -> bool:
        assert user_id == "user-id"
        return True

    monkeypatch.setattr(services, "get_tpos_owner_user_id", fake_owner_user_id)
    monkeypatch.setattr(services, "tabs_available_for_user", fake_tabs_available)

    assert await services.ensure_tpos_tabs_access(make_tpos()) == "user-id"


@pytest.mark.asyncio
async def test_fetch_tabs_for_tpos_filters_sorts_and_limits(monkeypatch):
    tabs = [
        {
            "id": "old",
            "wallet": "wallet-id",
            "name": "Old table",
            "currency": "eur",
            "status": "open",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": "new",
            "wallet": "wallet-id",
            "name": "Alice",
            "currency": "eur",
            "status": "open",
            "updated_at": "2026-01-02T00:00:00Z",
        },
        {
            "id": "other-wallet",
            "wallet": "other-wallet",
            "name": "Alice",
            "currency": "eur",
            "status": "open",
            "updated_at": "2026-01-03T00:00:00Z",
        },
        {
            "id": "closed",
            "wallet": "wallet-id",
            "name": "Alice",
            "currency": "eur",
            "status": "closed",
            "updated_at": "2026-01-04T00:00:00Z",
        },
    ]

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return tabs

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer token"
            assert kwargs["url"].endswith("/tabs/api/v1/tabs")
            return FakeResponse()

    monkeypatch.setattr(
        services,
        "_create_internal_user_access_token",
        lambda _: "token",
    )
    monkeypatch.setattr(services.httpx, "AsyncClient", FakeClient)

    result = await services.fetch_tabs_for_tpos(
        user_id="user-id",
        wallet_id="wallet-id",
        status="open",
        query="ali",
    )

    assert [tab["id"] for tab in result] == ["new"]


@pytest.mark.asyncio
async def test_fetch_tabs_for_tpos_rejects_invalid_status():
    with pytest.raises(HTTPException) as exc:
        await services.fetch_tabs_for_tpos(
            user_id="user-id",
            wallet_id="wallet-id",
            status="missing",
        )

    assert exc.value.status_code == HTTPStatus.BAD_REQUEST
    assert exc.value.detail == "Invalid tab status filter."


@pytest.mark.asyncio
async def test_api_tpos_create_tab_builds_payload(monkeypatch):
    calls = {}
    tpos = make_tpos(currency="EUR")

    async def fake_get_tpos_or_404(tpos_id: str) -> Tpos:
        assert tpos_id == "tpos-id"
        return tpos

    async def fake_access(access_tpos: Tpos) -> str:
        assert access_tpos == tpos
        return "user-id"

    async def fake_create_tab_for_tpos(user_id: str, payload: dict) -> dict:
        calls["user_id"] = user_id
        calls["payload"] = payload
        return {
            "id": "tab-id",
            "name": payload["name"],
            "customer_name": payload["customer_name"],
            "reference": payload["reference"],
            "currency": payload["currency"],
            "status": "open",
            "balance": 0,
            "is_archived": False,
        }

    monkeypatch.setattr(views_api, "_get_tpos_or_404", fake_get_tpos_or_404)
    monkeypatch.setattr(views_api, "ensure_tpos_tabs_access", fake_access)
    monkeypatch.setattr(views_api, "create_tab_for_tpos", fake_create_tab_for_tpos)

    result = await views_api.api_tpos_create_tab(
        "tpos-id",
        CreateTposTabData(
            name="Table 1",
            customer_name="Alice",
            reference="A1",
            currency="eur",
            limit_type="total",
            limit_amount=100,
        ),
    )

    assert result.id == "tab-id"
    assert calls == {
        "user_id": "user-id",
        "payload": {
            "wallet": "wallet-id",
            "name": "Table 1",
            "customer_name": "Alice",
            "reference": "A1",
            "currency": "eur",
            "limit_type": "total",
            "limit_amount": 100,
        },
    }


@pytest.mark.asyncio
async def test_api_tpos_create_tab_rejects_currency_mismatch(monkeypatch):
    async def fake_get_tpos_or_404(_tpos_id: str) -> Tpos:
        return make_tpos(currency="eur")

    monkeypatch.setattr(views_api, "_get_tpos_or_404", fake_get_tpos_or_404)

    async def fake_access(_tpos: Tpos) -> str:
        return "user-id"

    monkeypatch.setattr(views_api, "ensure_tpos_tabs_access", fake_access)

    with pytest.raises(HTTPException) as exc:
        await views_api.api_tpos_create_tab(
            "tpos-id",
            CreateTposTabData(name="Table 1", currency="usd"),
        )

    assert exc.value.status_code == HTTPStatus.BAD_REQUEST
    assert exc.value.detail == "Tab currency must match TPoS currency."


@pytest.mark.asyncio
async def test_api_tpos_add_tab_charge_checks_currency_and_payload(monkeypatch):
    calls = {}
    tpos = make_tpos(currency="eur")

    async def fake_get_tpos_or_404(_tpos_id: str) -> Tpos:
        return tpos

    async def fake_access(_tpos: Tpos) -> str:
        return "user-id"

    async def fake_fetch_tab(user_id: str, tab_id: str) -> dict:
        assert user_id == "user-id"
        assert tab_id == "tab-id"
        return {
            "id": "tab-id",
            "name": "Table 1",
            "currency": "eur",
            "status": "open",
            "balance": 12,
            "is_archived": False,
        }

    async def fake_create_charge(user_id: str, tab_id: str, payload: dict) -> dict:
        calls["user_id"] = user_id
        calls["tab_id"] = tab_id
        calls["payload"] = payload
        return {"id": "entry-id"}

    monkeypatch.setattr(views_api, "_get_tpos_or_404", fake_get_tpos_or_404)
    monkeypatch.setattr(views_api, "ensure_tpos_tabs_access", fake_access)
    monkeypatch.setattr(views_api, "fetch_single_tab_for_tpos", fake_fetch_tab)
    monkeypatch.setattr(views_api, "create_tab_charge_for_tpos", fake_create_charge)

    result = await views_api.api_tpos_add_tab_charge(
        "tpos-id",
        "tab-id",
        CreateTposTabCharge(
            amount=12.5,
            description="Lunch",
            items=[{"title": "Coffee"}],
            notes={"table": 1},
            internal_memo="memo",
            idempotency_key="charge-key",
        ),
    )

    assert result["entry"] == {"id": "entry-id"}
    assert result["tab"]["id"] == "tab-id"
    payload = calls["payload"]
    assert calls["user_id"] == "user-id"
    assert calls["tab_id"] == "tab-id"
    assert payload["entry_type"] == "charge"
    assert payload["amount"] == 12.5
    assert payload["description"] == "Lunch"
    assert payload["idempotency_key"] == "charge-key"
    metadata = json.loads(payload["metadata"])
    assert metadata["source"] == "tpos"
    assert metadata["tpos_id"] == "tpos-id"
    assert metadata["items"] == [{"title": "Coffee"}]


@pytest.mark.asyncio
async def test_maybe_settle_tab_builds_paid_settlement_payload(monkeypatch):
    calls = {}
    tpos = make_tpos(currency="eur")
    payment = SimpleNamespace(
        extra={
            "tab_settlement": {
                "tab_id": "tab-id",
                "amount": 20,
                "reference": "invoice-1",
                "description": "Paid",
                "idempotency_key": "settle-key",
            },
            "fiat_method": "terminal",
        },
        payment_hash="payment-hash",
    )

    async def fake_access(access_tpos: Tpos) -> str:
        assert access_tpos == tpos
        return "user-id"

    async def fake_create_settlement(user_id: str, tab_id: str, payload: dict) -> dict:
        calls["user_id"] = user_id
        calls["tab_id"] = tab_id
        calls["payload"] = payload
        return {"id": "settlement-id"}

    monkeypatch.setattr(
        tasks,
        "ensure_tpos_tabs_access",
        fake_access,
    )
    monkeypatch.setattr(
        tasks,
        "create_tab_settlement_for_tpos",
        fake_create_settlement,
    )

    await tasks.maybe_settle_tab(payment, tpos, "fiat")

    payload = calls["payload"]
    assert calls["user_id"] == "user-id"
    assert calls["tab_id"] == "tab-id"
    assert payload["amount"] == 20
    assert payload["method"] == "card"
    assert payload["reference"] == "invoice-1"
    assert payload["description"] == "Paid"
    assert payload["idempotency_key"] == "settle-key"
    metadata = json.loads(payload["metadata"])
    assert metadata["source"] == "tpos"
    assert metadata["source_action"] == "settlement_paid"
    assert metadata["payment_hash"] == "payment-hash"
