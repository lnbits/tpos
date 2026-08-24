import json
from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Query
from lnbits.core.crud import (
    get_user,
)
from lnbits.core.models import WalletTypeInfo
from lnbits.decorators import (
    require_admin_key,
    require_invoice_key,
)
from lnurl import LnurlPayResponse
from lnurl import handle as lnurl_handle

from .crud import (
    create_tpos,
    delete_tpos,
    get_tpos,
    get_tposs,
    update_tpos,
)
from .helpers import (
    inventory_tags_to_list,
    inventory_tags_to_string,
)
from .models import (
    CreateTposData,
    CreateUpdateItemData,
    Tpos,
)
from .services_inventory import (
    get_default_inventory,
    inventory_available_for_user,
)
from .views_inventory import tpos_inventory_router
from .views_onchain import _validate_watchonly_settings, tpos_onchain_router
from .views_payments import tpos_payments_router
from .views_tabs import (
    tpos_tabs_router,
)
from .views_wrapper import tpos_wrapper_router

tpos_api_router = APIRouter()
tpos_api_router.include_router(tpos_inventory_router)
tpos_api_router.include_router(tpos_onchain_router)
tpos_api_router.include_router(tpos_tabs_router)
tpos_api_router.include_router(tpos_wrapper_router)
tpos_api_router.include_router(tpos_payments_router)


@tpos_api_router.get("/api/v1/tposs", status_code=HTTPStatus.OK)
async def api_tposs(
    all_wallets: bool = Query(False),
    key_info: WalletTypeInfo = Depends(require_invoice_key),
) -> list[Tpos]:
    wallet_ids = [key_info.wallet.id]
    if all_wallets:
        user = await get_user(key_info.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return await get_tposs(wallet_ids)


@tpos_api_router.post("/api/v1/tposs", status_code=HTTPStatus.CREATED)
async def api_tpos_create(
    data: CreateTposData, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    data.wallet = wallet.wallet.id
    await _validate_watchonly_settings(
        wallet=wallet.wallet,
        onchain_enabled=data.onchain_enabled,
        onchain_wallet_id=data.onchain_wallet_id,
    )
    if not data.tabs_enabled:
        data.tabs_allow_create = False
    user = await get_user(wallet.wallet.user)
    if not (user and user.super_user):
        data.allow_cash_settlement = False
    if data.currency == "sats":
        data.allow_cash_settlement = False
    if data.use_inventory and not inventory_available_for_user(user):
        data.use_inventory = False
    if data.use_inventory and not data.inventory_id:
        inventory = await get_default_inventory(wallet.wallet.user)
        if not inventory:
            data.use_inventory = False
        else:
            data.inventory_id = inventory.get("id")
            data.inventory_tags = inventory.get("tags")
            data.inventory_omit_tags = inventory.get("omit_tags")
    tpos = await create_tpos(data)
    return tpos


@tpos_api_router.put("/api/v1/tposs/{tpos_id}")
async def api_tpos_update(
    data: CreateTposData,
    tpos_id: str,
    wallet: WalletTypeInfo = Depends(require_admin_key),
):
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    if wallet.wallet.id != tpos.wallet:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your TPoS.")
    user = await get_user(wallet.wallet.user)
    update_payload = data.dict(exclude_unset=True)
    update_payload.pop("wallet", None)
    desired_onchain_enabled = update_payload.get(
        "onchain_enabled", tpos.onchain_enabled
    )
    desired_onchain_wallet_id = update_payload.get(
        "onchain_wallet_id", tpos.onchain_wallet_id
    )
    await _validate_watchonly_settings(
        wallet=wallet.wallet,
        onchain_enabled=desired_onchain_enabled,
        onchain_wallet_id=desired_onchain_wallet_id,
    )
    desired_tabs_enabled = update_payload.get("tabs_enabled", tpos.tabs_enabled)
    if not desired_tabs_enabled:
        update_payload["tabs_allow_create"] = False
    desired_currency = update_payload.get("currency", tpos.currency)
    if desired_currency == "sats":
        update_payload["allow_cash_settlement"] = False
    if "allow_cash_settlement" in update_payload:
        if update_payload["allow_cash_settlement"] and not (user and user.super_user):
            raise HTTPException(
                status_code=HTTPStatus.FORBIDDEN,
                detail="Cash settlement can only be enabled by super users.",
            )
    if update_payload.get("use_inventory") and not update_payload.get("inventory_id"):
        inventory = await get_default_inventory(wallet.wallet.user)
        if inventory:
            update_payload["inventory_id"] = inventory.get("id")
        else:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="No inventory found for this user.",
            )
    if update_payload.get("use_inventory") and not inventory_available_for_user(user):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Inventory extension must be enabled to use it.",
        )
    if "inventory_tags" in update_payload:
        update_payload["inventory_tags"] = inventory_tags_to_string(
            inventory_tags_to_list(update_payload["inventory_tags"])
        )
    if "inventory_omit_tags" in update_payload:
        update_payload["inventory_omit_tags"] = inventory_tags_to_string(
            inventory_tags_to_list(update_payload["inventory_omit_tags"])
        )
    for field, value in update_payload.items():
        setattr(tpos, field, value)
    tpos = await update_tpos(tpos)
    return tpos


@tpos_api_router.delete("/api/v1/tposs/{tpos_id}")
async def api_tpos_delete(
    tpos_id: str, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    tpos = await get_tpos(tpos_id)

    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    if tpos.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your TPoS.")

    await delete_tpos(tpos_id)
    return "", HTTPStatus.NO_CONTENT


@tpos_api_router.put("/api/v1/tposs/{tpos_id}/items", status_code=HTTPStatus.CREATED)
async def api_tpos_create_items(
    data: CreateUpdateItemData,
    tpos_id: str,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> Tpos:
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    if wallet.wallet.id != tpos.wallet:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your TPoS.")

    tpos.items = json.dumps(data.dict()["items"])
    tpos = await update_tpos(tpos)
    return tpos


@tpos_api_router.get("/api/v1/tposs/lnaddresscheck", status_code=HTTPStatus.OK)
async def api_tpos_check_lnaddress(lnaddress: str):
    try:
        res = await lnurl_handle(lnaddress)
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Error checking lnaddress: {exc!s}",
        ) from exc

    if not isinstance(res, LnurlPayResponse):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="The provided lnaddress returned an unexpected response type.",
        )

    return True
