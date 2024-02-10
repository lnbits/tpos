from typing import List, Optional, Union

from lnbits.helpers import urlsafe_short_hash

from . import db
from .models import CreateTposData, TPoS, TPoSClean, LNURLCharge
from loguru import logger


async def get_current_timestamp():
    # Get current DB timestamp
    if db.type in {"POSTGRES", "COCKROACH"}:
        timestamp_query = f"SELECT EXTRACT(EPOCH FROM {db.timestamp_now})"
    elif db.type == "SQLITE":
        timestamp_query = f"SELECT {db.timestamp_now}"
    current_timestamp = (await db.fetchone(timestamp_query))[0]
    return int(current_timestamp)


async def create_tpos(wallet_id: str, data: CreateTposData) -> TPoS:
    tpos_id = urlsafe_short_hash()
    await db.execute(
        """
        INSERT INTO tpos.pos (id, wallet, name, currency, tip_options, tip_wallet, withdrawlimit, withdrawpin, withdrawamt, withdrawtime, withdrawbtwn, withdrawtimeopt, withdrawpindisabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            0,
            data.withdrawbtwn,
            data.withdrawtimeopt,
            data.withdrawpindisabled,
        ),
    )
    tpos = await get_tpos(tpos_id)
    assert tpos, "Newly created tpos couldn't be retrieved"
    return tpos


async def get_tpos(tpos_id: str) -> Optional[TPoS]:
    row = await db.fetchone("SELECT * FROM tpos.pos WHERE id = ?", (tpos_id,))
    return TPoS(**row) if row else None


async def start_lnurlcharge(tpos_id: str):
    tpos = await get_tpos(tpos_id)
    assert tpos, f"TPoS with {tpos_id} not found!"

    now = await get_current_timestamp()
    withdraw_time_seconds = tpos.withdrawbtwn * 60
    if tpos.withdrawtimeopt == "secs":
        withdraw_time_seconds = tpos.withdrawbtwn
    assert (
        now - tpos.withdrawtime > withdraw_time_seconds
    ), f"Last withdraw was made too recently, please try again in {int(withdraw_time_seconds - (now - tpos.withdrawtime))} secs"

    token = urlsafe_short_hash()
    await db.execute(
        """
        INSERT INTO tpos.withdraws (id, tpos_id)
        VALUES (?, ?)
        """,
        (token, tpos_id),
    )
    lnurlcharge = await get_lnurlcharge(token)
    return lnurlcharge


async def get_lnurlcharge(lnurlcharge_id: str) -> Optional[LNURLCharge]:
    row = await db.fetchone(
        "SELECT * FROM tpos.withdraws WHERE id = ?", (lnurlcharge_id,)
    )
    return LNURLCharge(**row) if row else None


async def update_lnurlcharge(data: LNURLCharge) -> LNURLCharge:
    # Construct the SET clause for the SQL query
    set_clause = ", ".join([f"{field[0]} = ?" for field in data.dict().items()])

    # Get the values for the SET clause
    set_values = list(data.dict().values())
    set_values.append(data.id)  # Add the ID for the WHERE clause

    # Execute the UPDATE statement
    await db.execute(
        f"UPDATE tpos.withdraws SET {set_clause} WHERE id = ?", tuple(set_values)
    )

    lnurlcharge = await get_lnurlcharge(data.id)
    assert lnurlcharge, "Withdraw couldn't be retrieved"

    return lnurlcharge


async def get_clean_tpos(tpos_id: str) -> Optional[TPoSClean]:
    row = await db.fetchone("SELECT * FROM tpos.pos WHERE id = ?", (tpos_id,))
    return TPoSClean(**row) if row else None


async def update_tpos_withdraw(data: TPoS, tpos_id: str) -> TPoS:
    # Calculate the time between withdrawals in seconds
    now = await get_current_timestamp()
    time_elapsed = now - data.withdrawtime
    withdraw_time_seconds = data.withdrawbtwn * 60

    logger.debug(f"Time between: {time_elapsed} seconds")

    # Check if the time between withdrawals is less than withdrawbtwn
    assert (
        time_elapsed > withdraw_time_seconds
    ), f"Last withdraw was made too recently, please try again in {int(withdraw_time_seconds - (time_elapsed))} secs"

    # Update the withdraw time in the database
    await db.execute(
        "UPDATE tpos.pos SET withdrawtime = ? WHERE id = ?", (now, tpos_id)
    )

    tpos = await get_tpos(tpos_id)
    assert tpos, "Newly updated tpos couldn't be retrieved"
    return tpos


async def update_tpos(tpos_id: str, **kwargs) -> TPoS:
    q = ", ".join([f"{field[0]} = ?" for field in kwargs.items()])
    await db.execute(
        f"UPDATE tpos.pos SET {q} WHERE id = ?", (*kwargs.values(), tpos_id)
    )
    tpos = await get_tpos(tpos_id)
    assert tpos, "Newly updated tpos couldn't be retrieved"
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
