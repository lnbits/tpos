from typing import List, Optional, Union

from lnbits.helpers import urlsafe_short_hash
import datetime

from . import db
from .models import CreateTposData, TPoS, TPoSClean, LNURLCharge

async def create_tpos(wallet_id: str, data: CreateTposData) -> TPoS:
    tpos_id = urlsafe_short_hash()
    await db.execute(
        """
        INSERT INTO tpos.tpos (id, wallet, name, currency, tip_options, tip_wallet, withdrawlimit, withdrawpin, withdrawamt, withdrawtime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tpos_id,
            wallet_id,
            data.name,
            data.currency,
            data.tip_options,
            data.tip_wallet,
            data.withdrawlimit,
            data.withdrawpin,
            0,
            db.timestamp_now
        ),
    )

    tpos = await get_tpos(tpos_id)
    assert tpos, "Newly created tpos couldn't be retrieved"
    return tpos

async def start_lnurlcharge(tpos_id: str):
    tpos = await get_tpos(tpos_id)
    if db.timestamp_now - tpos.withdrawtime < 10000:
        assert tpos, "TPoS could not be retreived"
    token = urlsafe_short_hash()
    await db.execute(
        """
        INSERT INTO tpos.withdraws (id, tpos_id)
        VALUES (?, ?)
        """,
        (
            token,
            tpos_id
        ),
    )
    lnurlcharge = await get_lnurlcharge(token)
    return lnurlcharge

async def get_lnurlcharge(lnurlcharge_id: str) -> Optional[LNURLCharge]:
    row = await db.fetchone("SELECT * FROM tpos.withdraws WHERE id = ?", (lnurlcharge_id,))
    return LNURLCharge(**row) if row else None

async def update_lnurlcharge(
    data: LNURLCharge, lnurlcharge_id: str
) -> LNURLCharge:
    q = ", ".join([f"{field[0]} = ?" for field in data])
    items = [f"{field[1]}" for field in data]
    items.append(lnurlcharge_id)
    await db.execute(f"UPDATE tpos.tpos SET {q} WHERE id = ?", (items,))
    lnurlcharge = await get_lnurlcharge(lnurlcharge_id)
    assert lnurlcharge, "Withdraw couldnt be retreived"
    return lnurlcharge

async def get_tpos(tpos_id: str) -> Optional[TPoS]:
    row = await db.fetchone("SELECT * FROM tpos.tpos WHERE id = ?", (tpos_id,))
    return TPoS(**row) if row else None

async def get_clean_tpos(tpos_id: str) -> Optional[TPoSClean]:
    row = await db.fetchone("SELECT * FROM tpos.tpos WHERE id = ?", (tpos_id,))
    return TPoSClean(**row) if row else None

async def update_tpos(
    data: CreateTposData, tpos_id: str, timebool: Optional[bool]
) -> TPoS:
    q = ", ".join([f"{field[0]} = ?" for field in data])
    items = [f"{field[1]}" for field in data]
    items.append(tpos_id)
    await db.execute(f"UPDATE tpos.tpos SET {q} WHERE id = ?", (items,))
    tpos = await get_tpos(tpos_id)
    if timebool:
        timebetween = db.timestamp_now - tpos.time
        if timebetween < 600000:
            assert tpos, f"Last withdraw was made too recently,  please try again in {(600000 - timebetween) / 1000} secs"
        await db.execute(f"UPDATE tpos.tpos WHERE time = {db.timestamp_now};")
    assert tpos, "Newly created tpos couldn't be retrieved"
    return tpos

async def get_tpos(wallet_ids: Union[str, List[str]]) -> List[TPoS]:
    if isinstance(wallet_ids, str):
        wallet_ids = [wallet_ids]

    q = ",".join(["?"] * len(wallet_ids))
    rows = await db.fetchall(
        f"SELECT * FROM tpos.tpos WHERE wallet IN ({q})", (*wallet_ids,)
    )
    return [TPoS(**row) for row in rows]


async def delete_tpos(tpos_id: str) -> None:
    await db.execute("DELETE FROM tpos.tpos WHERE id = ?", (tpos_id,))
