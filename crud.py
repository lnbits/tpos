from lnbits.db import Database
from lnbits.helpers import urlsafe_short_hash

from .models import CreateTposData, LnurlCharge, Tpos, TposClean

db = Database("ext_tpos")


def _serialize_inventory_tags(tags: list[str] | str | None) -> str | None:
    if isinstance(tags, list):
        return ",".join([tag for tag in tags if tag])
    return tags


async def create_tpos(data: CreateTposData) -> Tpos:
    tpos_id = urlsafe_short_hash()
    data_dict = data.dict()
    data_dict["inventory_tags"] = _serialize_inventory_tags(data.inventory_tags)
    data_dict["inventory_omit_tags"] = _serialize_inventory_tags(
        data.inventory_omit_tags
    )
    tpos = Tpos(id=tpos_id, **data_dict)
    await db.insert("tpos.pos", tpos)
    return tpos


async def get_tpos(tpos_id: str) -> Tpos | None:
    return await db.fetchone(
        "SELECT * FROM tpos.pos WHERE id = :id", {"id": tpos_id}, Tpos
    )


async def create_lnurlcharge(tpos_id: str) -> LnurlCharge:
    charge_id = urlsafe_short_hash()
    lnurlcharge = LnurlCharge(id=charge_id, tpos_id=tpos_id)
    await db.insert("tpos.withdraws", lnurlcharge)
    return lnurlcharge


async def get_lnurlcharge(lnurlcharge_id: str) -> LnurlCharge | None:
    return await db.fetchone(
        "SELECT * FROM tpos.withdraws WHERE id = :id",
        {"id": lnurlcharge_id},
        LnurlCharge,
    )


async def update_lnurlcharge(charge: LnurlCharge) -> LnurlCharge:
    await db.update("tpos.withdraws", charge)
    return charge


async def get_clean_tpos(tpos_id: str) -> TposClean | None:
    return await db.fetchone(
        "SELECT * FROM tpos.pos WHERE id = :id", {"id": tpos_id}, TposClean
    )


async def update_tpos(tpos: Tpos) -> Tpos:
    tpos.inventory_tags = _serialize_inventory_tags(tpos.inventory_tags)
    tpos.inventory_omit_tags = _serialize_inventory_tags(tpos.inventory_omit_tags)
    await db.update("tpos.pos", tpos)
    return tpos


async def get_tposs(wallet_ids: str | list[str]) -> list[Tpos]:
    if isinstance(wallet_ids, str):
        wallet_ids = [wallet_ids]
    q = ",".join([f"'{wallet_id}'" for wallet_id in wallet_ids])
    tposs = await db.fetchall(
        f"SELECT * FROM tpos.pos WHERE wallet IN ({q})", model=Tpos
    )
    return tposs


async def delete_tpos(tpos_id: str) -> None:
    await db.execute("DELETE FROM tpos.pos WHERE id = :id", {"id": tpos_id})
