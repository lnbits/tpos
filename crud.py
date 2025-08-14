from typing import Optional, Union

from lnbits.db import Database
from lnbits.helpers import urlsafe_short_hash

from .models import CreateTposData, LnurlCharge, Tpos, TposClean

db = Database("ext_tpos")


async def create_tpos(data: CreateTposData) -> Tpos:
    tpos_id = urlsafe_short_hash()
    tpos = Tpos(id=tpos_id, **data.dict())
    await db.insert("tpos.pos", tpos)
    return tpos


async def get_tpos(tpos_id: str) -> Optional[Tpos]:
    return await db.fetchone(
        "SELECT * FROM tpos.pos WHERE id = :id", {"id": tpos_id}, Tpos
    )


async def create_lnurlcharge(tpos_id: str) -> LnurlCharge:
    # now = int(time())
    # seconds = (
    #     tpos.withdraw_between * 60
    #     if tpos.withdraw_time_option != "secs"
    #     else tpos.withdraw_between
    # )
    # last_withdraw = tpos.withdraw_time - now
    # assert (
    #     last_withdraw < seconds
    # ), f"""
    #     Last withdraw was made too recently, please try again in
    #     {int(seconds - (last_withdraw))} secs
    # """
    charge_id = urlsafe_short_hash()
    lnurlcharge = LnurlCharge(id=charge_id, tpos_id=tpos_id)
    await db.insert("tpos.withdraws", lnurlcharge)
    return lnurlcharge


async def get_lnurlcharge(lnurlcharge_id: str) -> Optional[LnurlCharge]:
    return await db.fetchone(
        "SELECT * FROM tpos.withdraws WHERE id = :id",
        {"id": lnurlcharge_id},
        LnurlCharge,
    )


async def update_lnurlcharge(charge: LnurlCharge) -> LnurlCharge:
    await db.update("tpos.withdraws", charge)
    return charge


async def get_clean_tpos(tpos_id: str) -> Optional[TposClean]:
    return await db.fetchone(
        "SELECT * FROM tpos.pos WHERE id = :id", {"id": tpos_id}, TposClean
    )


async def update_tpos(tpos: Tpos) -> Tpos:
    await db.update("tpos.pos", tpos)
    return tpos


async def get_tposs(wallet_ids: Union[str, list[str]]) -> list[Tpos]:
    if isinstance(wallet_ids, str):
        wallet_ids = [wallet_ids]
    q = ",".join([f"'{wallet_id}'" for wallet_id in wallet_ids])
    tposs = await db.fetchall(
        f"SELECT * FROM tpos.pos WHERE wallet IN ({q})", model=Tpos
    )
    return tposs


async def delete_tpos(tpos_id: str) -> None:
    await db.execute("DELETE FROM tpos.pos WHERE id = :id", {"id": tpos_id})
