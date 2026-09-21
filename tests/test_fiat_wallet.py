from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
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
