from typing import List, Optional, Union

from lnbits.helpers import urlsafe_short_hash
from datetime import datetime

from . import db
from .models import CreateTposData, TPoS, TPoSClean, LNURLCharge
from loguru import logger

async def create_tpos(wallet_id: str, data: CreateTposData) -> TPoS:
    tpos_id = urlsafe_short_hash()
    await db.execute(
        """
        INSERT INTO tpos.pos (id, wallet, name, currency, tip_options, tip_wallet, withdrawlimit, withdrawpin, withdrawamt, withdrawtime)
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
            datetime.timestamp(datetime.now())
        ),
    )
    tpos = await get_tpos(tpos_id)
    assert tpos, "Newly created tpos couldn't be retrieved"
    return tpos

async def get_tpos(tpos_id: str) -> TPoS:
    row = await db.fetchone("SELECT * FROM tpos.pos WHERE id = ?", (tpos_id,))
    return TPoS(**row) if row else None

async def start_lnurlcharge(tpos_id: str):
    tpos = await get_tpos(tpos_id)
    if datetime.timestamp(datetime.now()) - tpos.withdrawtime < 10000:
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
    data: LNURLCharge
) -> LNURLCharge:
    q = ", ".join([f"{field[0]} = ?" for field in data])
    logger.debug(q)
    items = [f"{field[1]}" for field in data]
    logger.debug(items)
    items.append(data.id)
    await db.execute(f"UPDATE tpos.withdraws SET {q} WHERE id = ?", (items,))
    lnurlcharge = await get_lnurlcharge(data.id)
    assert lnurlcharge, "Withdraw couldnt be retreived"
    return lnurlcharge

async def get_clean_tpos(tpos_id: str) -> Optional[TPoSClean]:
    row = await db.fetchone("SELECT * FROM tpos.pos WHERE id = ?", (tpos_id,))
    return TPoSClean(**row) if row else None

async def update_tpos(
    data: CreateTposData, tpos_id: str, timebool: Optional[bool]
) -> TPoS:
    tpos = await get_tpos(tpos_id)
    q = ", ".join([f"{field[0]} = ?" for field in data])
    items = [f"{field[1]}" for field in data]
    items.append(tpos_id)
    await db.execute(f"UPDATE tpos.pos SET {q} WHERE id = ?", (items,))
    tpos = await get_tpos(tpos_id)
    if timebool:
        timebetween = db.timestamp_now - tpos.withdrawtime
        if timebetween < 600000:
            assert tpos, f"Last withdraw was made too recently,  please try again in {(600000 - timebetween) / 1000} secs"
        await db.execute(f"UPDATE tpos.pos WHERE withdrawtime = {datetime.timestamp(datetime.now())};")
    assert tpos, "Newly created tpos couldn't be retrieved"
    return tpos

async def get_tposs(wallet_ids: Union[str, List[str]]) -> List[TPoS]:
    if isinstance(wallet_ids, str):
        wallet_ids = [wallet_ids]

    q = ",".join(["?"] * len(wallet_ids))
    rows = await db.fetchall(
        f"SELECT * FROM tpos.pos WHERE wallet IN ({q})", (*wallet_ids,)
    )
    return [TPoS(**row) for row in rows]


async def delete_tpos(tpos_id: str) -> None:
    await db.execute("DELETE FROM tpos.pos WHERE id = ?", (tpos_id,))
