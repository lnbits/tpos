from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from lnbits.core.crud.wallets import create_wallet, delete_wallet, update_wallet
from lnbits.core.models.users import Account
from lnbits.core.models.wallets import WalletType
from lnbits.core.services.users import create_user_account_no_ckeck
from lnbits.db import Connection

from tpos.crud import db  # type: ignore[import]
from tpos.migrations import m028_backfill_fiat_wallets  # type: ignore[import]
from tpos.services_fiat import (  # type: ignore[import]
    create_user_fiat_wallet,
    find_fiat_wallet,
    get_user_fiat_wallets,
)


async def _user(username: str):
    account = Account(id=uuid4().hex, username=username)
    user = await create_user_account_no_ckeck(account=account)
    return user, user.wallets[0]


def _tpos_payload(**overrides) -> dict:
    payload = {
        "wallet": None,
        "name": "Fiat TPoS",
        "currency": "EUR",
        "business_name": "Fiat Shop",
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


async def _add_tpos(
    conn: Connection,
    *,
    tpos_id: str,
    wallet: str,
    currency: str,
    cash: bool = False,
    provider: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO tpos.pos
            (id, wallet, name, currency, allow_cash_settlement, fiat_provider)
        VALUES (:id, :wallet, :name, :currency, :cash, :provider)
        """,
        {
            "id": tpos_id,
            "wallet": wallet,
            "name": tpos_id,
            "currency": currency,
            "cash": cash,
            "provider": provider,
        },
    )


async def _tpos_row(conn: Connection, tpos_id: str) -> dict:
    row: Any = await conn.fetchone(
        """
        SELECT fiat_wallet_id, allow_cash_settlement, fiat_provider
        FROM tpos.pos WHERE id = :id
        """,
        {"id": tpos_id},
    )
    return dict(row)


@pytest.mark.asyncio
async def test_backfill_shares_one_fiat_wallet():
    user, wallet = await _user("fiat_shared")
    async with db.connect() as conn:
        await _add_tpos(
            conn, tpos_id="cash-pos", wallet=wallet.id, currency="EUR", cash=True
        )
        await _add_tpos(
            conn,
            tpos_id="card-pos",
            wallet=wallet.id,
            currency="EUR",
            provider="stripe",
        )
        await m028_backfill_fiat_wallets(conn)
        cash_row = await _tpos_row(conn, "cash-pos")
        card_row = await _tpos_row(conn, "card-pos")

    assert cash_row["fiat_wallet_id"]
    assert cash_row["fiat_wallet_id"] == card_row["fiat_wallet_id"]

    fiat_wallets = await get_user_fiat_wallets(user.id)
    assert [fiat.id for fiat in fiat_wallets] == [cash_row["fiat_wallet_id"]]
    assert fiat_wallets[0].wallet_type == WalletType.FIAT.value
    assert fiat_wallets[0].currency == "EUR"
    assert fiat_wallets[0].user == user.id


@pytest.mark.asyncio
async def test_backfill_is_idempotent():
    user, wallet = await _user("fiat_idempotent")
    async with db.connect() as conn:
        await _add_tpos(
            conn, tpos_id="cash-pos", wallet=wallet.id, currency="EUR", cash=True
        )
        await m028_backfill_fiat_wallets(conn)
        first = await _tpos_row(conn, "cash-pos")
        await m028_backfill_fiat_wallets(conn)
        second = await _tpos_row(conn, "cash-pos")

    assert first["fiat_wallet_id"] == second["fiat_wallet_id"]
    assert len(await get_user_fiat_wallets(user.id)) == 1


@pytest.mark.asyncio
async def test_backfill_reuses_an_existing_fiat_wallet():
    user, wallet = await _user("fiat_reuse")
    existing = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    async with db.connect() as conn:
        await _add_tpos(
            conn, tpos_id="cash-pos", wallet=wallet.id, currency="EUR", cash=True
        )
        await m028_backfill_fiat_wallets(conn)
        row = await _tpos_row(conn, "cash-pos")

    assert row["fiat_wallet_id"] == existing.id
    assert len(await get_user_fiat_wallets(user.id)) == 1


@pytest.mark.asyncio
async def test_backfill_disables_cash_when_no_wallet_can_be_assigned():
    user, wallet = await _user("fiat_disabled")
    await delete_wallet(user.id, wallet.id)
    async with db.connect() as conn:
        await _add_tpos(
            conn, tpos_id="sats-pos", wallet=wallet.id, currency="sats", cash=True
        )
        await _add_tpos(
            conn,
            tpos_id="unsupported-pos",
            wallet=wallet.id,
            currency="XYZ",
            cash=True,
            provider="stripe",
        )
        await _add_tpos(
            conn, tpos_id="orphan-pos", wallet=wallet.id, currency="USD", cash=True
        )
        await m028_backfill_fiat_wallets(conn)
        sats_row = await _tpos_row(conn, "sats-pos")
        unsupported_row = await _tpos_row(conn, "unsupported-pos")
        orphan_row = await _tpos_row(conn, "orphan-pos")

    assert sats_row["allow_cash_settlement"] in (False, 0)
    assert sats_row["fiat_wallet_id"] is None
    assert unsupported_row["allow_cash_settlement"] in (False, 0)
    assert unsupported_row["fiat_provider"] == "stripe"
    assert unsupported_row["fiat_wallet_id"] is None
    assert orphan_row["allow_cash_settlement"] in (False, 0)
    assert orphan_row["fiat_wallet_id"] is None
    assert await get_user_fiat_wallets(user.id) == []


@pytest.mark.asyncio
async def test_find_fiat_wallet_returns_the_oldest_match():
    user, _ = await _user("fiat_canonical")
    older = await create_wallet(
        user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR"
    )
    older.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    await update_wallet(older)
    await create_wallet(user_id=user.id, wallet_type=WalletType.FIAT, currency="EUR")
    await create_wallet(user_id=user.id, wallet_type=WalletType.FIAT, currency="USD")

    assert (await find_fiat_wallet(user.id, "eur")).id == older.id
    assert await find_fiat_wallet(user.id, "XYZ") is None


@pytest.mark.asyncio
async def test_create_user_fiat_wallet_is_idempotent():
    user, _ = await _user("fiat_create")
    wallet = await create_user_fiat_wallet(user.id, "eur")
    again = await create_user_fiat_wallet(user.id, "EUR", name="Other name")

    assert wallet.id == again.id
    assert wallet.name == "TPoS EUR"
    assert wallet.wallet_type == WalletType.FIAT.value
    assert wallet.currency == "EUR"
    assert len(await get_user_fiat_wallets(user.id)) == 1


@pytest.mark.asyncio
async def test_create_user_fiat_wallet_rejects_unsupported_currency():
    user, _ = await _user("fiat_bad_currency")
    with pytest.raises(HTTPException) as exc:
        await create_user_fiat_wallet(user.id, "XYZ")

    assert exc.value.status_code == 400
    assert await get_user_fiat_wallets(user.id) == []


@pytest.mark.asyncio
async def test_cash_settlement_needs_no_superuser(client: AsyncClient):
    user, wallet = await _user("fiat_cash_api")
    assert not user.super_user

    response = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(allow_cash_settlement=True),
        headers={"X-API-KEY": wallet.adminkey},
    )

    assert response.status_code == 201, response.text
    tpos = response.json()
    assert tpos["fiat_wallet_id"]
    fiat_wallet = await find_fiat_wallet(user.id, "EUR")
    assert fiat_wallet and fiat_wallet.id == tpos["fiat_wallet_id"]
    assert fiat_wallet.currency == "EUR"
    assert fiat_wallet.user == user.id


@pytest.mark.asyncio
async def test_tpos_requires_a_lightning_wallet(client: AsyncClient):
    user, _ = await _user("fiat_key_rejected")
    fiat_wallet = await create_user_fiat_wallet(user.id, "EUR")

    response = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(),
        headers={"X-API-KEY": fiat_wallet.adminkey},
    )

    assert response.status_code == 400
    assert "Lightning wallet" in response.json()["detail"]


@pytest.mark.asyncio
async def test_card_payments_require_admin_enablement(
    client: AsyncClient, enable_stripe
):
    user, wallet = await _user("fiat_card_api")
    headers = {"X-API-KEY": wallet.adminkey}
    payload = _tpos_payload(fiat_provider="stripe")

    refused = await client.post("/tpos/api/v1/tposs", json=payload, headers=headers)
    assert refused.status_code == 400
    assert "Card payments are not enabled" in refused.json()["detail"]

    enable_stripe(user.id)
    allowed = await client.post("/tpos/api/v1/tposs", json=payload, headers=headers)

    assert allowed.status_code == 201, allowed.text
    fiat_wallet = await find_fiat_wallet(user.id, "EUR")
    assert allowed.json()["fiat_wallet_id"] == (fiat_wallet and fiat_wallet.id)


@pytest.mark.asyncio
async def test_explicit_fiat_wallet_is_validated(client: AsyncClient):
    user, wallet = await _user("fiat_explicit")
    other_user, _ = await _user("fiat_explicit_other")
    own = await create_user_fiat_wallet(user.id, "EUR")
    other = await create_user_fiat_wallet(other_user.id, "EUR")
    usd = await create_user_fiat_wallet(user.id, "USD")
    headers = {"X-API-KEY": wallet.adminkey}

    accepted = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(
            name="Explicit", allow_cash_settlement=True, fiat_wallet_id=own.id
        ),
        headers=headers,
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["fiat_wallet_id"] == own.id

    cases = {
        other.id: 403,
        wallet.id: 400,
        usd.id: 400,
        "does-not-exist": 400,
    }
    for wallet_id, status in cases.items():
        response = await client.post(
            "/tpos/api/v1/tposs",
            json=_tpos_payload(
                name=f"Bad {wallet_id}",
                allow_cash_settlement=True,
                fiat_wallet_id=wallet_id,
            ),
            headers=headers,
        )
        assert response.status_code == status, response.text


@pytest.mark.asyncio
async def test_update_reresolves_the_fiat_wallet(client: AsyncClient):
    user, wallet = await _user("fiat_update")
    headers = {"X-API-KEY": wallet.adminkey}
    created = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(allow_cash_settlement=True),
        headers=headers,
    )
    tpos_id = created.json()["id"]
    eur_wallet_id = created.json()["fiat_wallet_id"]

    usd = await client.put(
        f"/tpos/api/v1/tposs/{tpos_id}",
        json=_tpos_payload(currency="USD", allow_cash_settlement=True),
        headers=headers,
    )
    assert usd.status_code == 200, usd.text
    usd_wallet_id = usd.json()["fiat_wallet_id"]
    assert usd_wallet_id and usd_wallet_id != eur_wallet_id

    sats = await client.put(
        f"/tpos/api/v1/tposs/{tpos_id}",
        json=_tpos_payload(currency="sats", allow_cash_settlement=True),
        headers=headers,
    )
    assert sats.status_code == 200, sats.text
    assert sats.json()["fiat_wallet_id"] is None
    assert sats.json()["allow_cash_settlement"] is False

    back_to_eur = await client.put(
        f"/tpos/api/v1/tposs/{tpos_id}",
        json=_tpos_payload(currency="EUR", allow_cash_settlement=True),
        headers=headers,
    )
    assert back_to_eur.json()["fiat_wallet_id"] == eur_wallet_id

    released = await client.put(
        f"/tpos/api/v1/tposs/{tpos_id}",
        json=_tpos_payload(currency="EUR", allow_cash_settlement=False),
        headers=headers,
    )
    assert released.json()["fiat_wallet_id"] is None
    assert len(await get_user_fiat_wallets(user.id)) == 2


@pytest.mark.asyncio
async def test_wallet_endpoints_share_and_never_leak_keys(client: AsyncClient):
    user, wallet = await _user("fiat_wallets_api")
    headers = {"X-API-KEY": wallet.adminkey}
    for name in ("First", "Second"):
        created = await client.post(
            "/tpos/api/v1/tposs",
            json=_tpos_payload(name=name, allow_cash_settlement=True),
            headers=headers,
        )
        assert created.status_code == 201, created.text

    tposs = await client.get("/tpos/api/v1/tposs", headers=headers)
    wallet_ids = {tpos["fiat_wallet_id"] for tpos in tposs.json()}
    assert len(wallet_ids) == 1 and None not in wallet_ids

    existing = await client.post(
        "/tpos/api/v1/fiat/wallets", json={"currency": "eur"}, headers=headers
    )
    assert existing.status_code == 200, existing.text
    assert existing.json()["id"] in wallet_ids
    assert len(await get_user_fiat_wallets(user.id)) == 1

    new_wallet = await client.post(
        "/tpos/api/v1/fiat/wallets", json={"currency": "USD"}, headers=headers
    )
    assert new_wallet.status_code == 201, new_wallet.text
    assert new_wallet.json()["currency"] == "USD"

    bad_currency = await client.post(
        "/tpos/api/v1/fiat/wallets", json={"currency": "XYZ"}, headers=headers
    )
    assert bad_currency.status_code == 400

    status = await client.get(
        "/tpos/api/v1/wallets", headers={"X-API-KEY": wallet.inkey}
    )
    assert status.status_code == 200, status.text
    status_json = status.json()
    assert status_json["can_create_fiat_wallet"] is False
    assert [item["id"] for item in status_json["lightning_wallets"]] == [wallet.id]
    assert {item["currency"] for item in status_json["fiat_wallets"]} == {"EUR", "USD"}
    assert "adminkey" not in status.text and "inkey" not in status.text


@pytest.mark.asyncio
async def test_legacy_provider_tpos_is_tolerated_until_it_changes(
    client: AsyncClient, enable_stripe
):
    user, wallet = await _user("fiat_legacy_provider")
    enable_stripe(user.id)
    headers = {"X-API-KEY": wallet.adminkey}
    created = await client.post(
        "/tpos/api/v1/tposs",
        json=_tpos_payload(name="Legacy", fiat_provider="stripe"),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    tpos_id = created.json()["id"]
    # un-backfillable legacy state: provider kept, no fiat wallet assigned
    async with db.connect() as conn:
        await conn.execute(
            """
            UPDATE tpos.pos
            SET currency = 'XYZ', fiat_wallet_id = NULL
            WHERE id = :id
            """,
            {"id": tpos_id},
        )

    tolerated = await client.put(
        f"/tpos/api/v1/tposs/{tpos_id}",
        json=_tpos_payload(name="Renamed", currency="XYZ", fiat_provider="stripe"),
        headers=headers,
    )
    assert tolerated.status_code == 200, tolerated.text
    assert tolerated.json()["fiat_wallet_id"] is None

    refused = await client.put(
        f"/tpos/api/v1/tposs/{tpos_id}",
        json=_tpos_payload(name="Renamed", currency="XYZ", fiat_provider="paypal"),
        headers=headers,
    )
    assert refused.status_code == 400
    assert "Card payments are not enabled" in refused.json()["detail"]
