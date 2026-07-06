from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from lnbits.core.crud import get_user, get_wallet
from lnbits.core.models import WalletTypeInfo
from lnbits.decorators import require_admin_key

from .crud import get_tpos
from .helpers import first_image, inventory_tags_to_list
from .services_inventory import (
    get_default_inventory,
    get_inventory_items_for_tpos,
    inventory_available_for_user,
)

tpos_inventory_router = APIRouter()


@tpos_inventory_router.get("/api/v1/inventory/status", status_code=HTTPStatus.OK)
async def api_inventory_status(
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> dict:
    user = await get_user(wallet.wallet.user)
    if not inventory_available_for_user(user):
        return {"enabled": False, "inventory_id": None, "tags": [], "omit_tags": []}
    inventory = await get_default_inventory(wallet.wallet.user)
    tags = inventory_tags_to_list(inventory.get("tags")) if inventory else []
    omit_tags = inventory_tags_to_list(inventory.get("omit_tags")) if inventory else []
    return {
        "enabled": True,
        "inventory_id": inventory.get("id") if inventory else None,
        "tags": tags,
        "omit_tags": omit_tags,
    }


@tpos_inventory_router.get(
    "/api/v1/tposs/{tpos_id}/inventory-items", status_code=HTTPStatus.OK
)
async def api_tpos_inventory_items(tpos_id: str) -> list[dict[str, Any]]:
    tpos = await get_tpos(tpos_id)
    if not tpos or not tpos.use_inventory:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Inventory not enabled for this TPoS.",
        )

    wallet = await get_wallet(tpos.wallet)
    if not wallet:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Wallet not found for this TPoS.",
        )

    inventory_id = tpos.inventory_id
    inventory_data: dict[str, Any] | None = None
    if not inventory_id:
        inventory_data = await get_default_inventory(wallet.user)
        inventory_id = inventory_data.get("id") if inventory_data else None
    else:
        inventory_data = await get_default_inventory(wallet.user)
    if not inventory_id:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="No inventory found for this TPoS.",
        )

    items = await get_inventory_items_for_tpos(
        wallet.user,
        inventory_id,
        tpos.inventory_tags,
        tpos.inventory_omit_tags,
    )
    return [
        {
            "id": item.get("id"),
            "title": item.get("name"),
            "description": item.get("description"),
            "price": item.get("price"),
            "tax": item.get("tax_rate"),
            "image": first_image(item.get("images")),
            "categories": inventory_tags_to_list(item.get("tags")),
            "quantity_in_stock": item.get("quantity_in_stock"),
            "disabled": (not item.get("is_active"))
            or (
                item.get("quantity_in_stock") is not None
                and item.get("quantity_in_stock") <= 0
            ),
        }
        for item in items
    ]
