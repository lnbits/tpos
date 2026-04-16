from http import HTTPStatus
from typing import Any

import httpx
from fastapi import HTTPException
from lnbits.core.crud import (
    get_installed_extension,
    get_user_active_extensions_ids,
    get_wallet,
)
from lnbits.core.models import User
from lnbits.helpers import create_access_token
from lnbits.settings import settings
from loguru import logger

from .helpers import from_csv, inventory_tags_to_list
from .models import Tpos

_TAB_STATUSES = {"open", "suspended", "closed"}


async def get_tpos_owner_user_id(tpos: Tpos) -> str:
    wallet = await get_wallet(tpos.wallet)
    if not wallet:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="TPoS is not ready for tabs integration.",
        )
    return wallet.user


async def deduct_inventory_stock(wallet_id: str, inventory_payload: dict) -> None:
    wallet = await get_wallet(wallet_id)
    if not wallet:
        return
    inventory_id = inventory_payload.get("inventory_id")
    items = inventory_payload.get("items") or []
    if not inventory_id or not items:
        return
    items_to_update = []
    for item in items:
        item_id = item.get("id")
        qty = item.get("quantity") or 0
        if not item_id or qty <= 0:
            continue
        items_to_update.append({"id": item_id, "quantity": int(qty)})
    if not items_to_update:
        return

    ids = [item["id"] for item in items_to_update]
    quantities = [item["quantity"] for item in items_to_update]

    # Needed to accomodate admin users, as using user ID is not possible
    access = create_access_token(
        {"sub": "", "usr": wallet.user}, token_expire_minutes=1
    )
    async with httpx.AsyncClient() as client:
        await client.patch(
            url=f"http://{settings.host}:{settings.port}/inventory/api/v1/items/{inventory_id}/quantities",
            headers={"Authorization": f"Bearer {access}"},
            params={"source": "tpos", "ids": ids, "quantities": quantities},
        )
    return


async def get_default_inventory(user_id: str) -> dict[str, Any] | None:
    access = _create_internal_user_access_token(user_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/inventory/api/v1",
            headers={"Authorization": f"Bearer {access}"},
        )
        inventory = resp.json()
    if not inventory:
        return None
    if isinstance(inventory, list):
        inventory = inventory[0] if inventory else None
    if not isinstance(inventory, dict):
        return None
    inventory["tags"] = inventory_tags_to_list(inventory.get("tags"))
    inventory["omit_tags"] = inventory_tags_to_list(inventory.get("omit_tags"))
    return inventory


async def get_inventory_items_for_tpos(
    user_id: str,
    inventory_id: str,
    tags: str | list[str] | None,
    omit_tags: str | list[str] | None,
) -> list[Any]:
    tag_list = inventory_tags_to_list(tags)
    omit_list = [tag.lower() for tag in inventory_tags_to_list(omit_tags)]
    allowed_tags = [tag.lower() for tag in tag_list]
    access = _create_internal_user_access_token(user_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/inventory/api/v1/items/{inventory_id}/paginated",
            headers={"Authorization": f"Bearer {access}"},
            params={"limit": 500, "offset": 0, "is_active": True},
        )
        payload = resp.json()
        items = payload.get("data", []) if isinstance(payload, dict) else payload

        # item images are a comma separated string; make a list
        for item in items:
            images = item.get("images")
            item["images"] = from_csv(images)

    def has_allowed_tag(item_tags: str | list[str] | None) -> bool:
        # When no tags are configured for this TPoS, show no items
        if not tag_list:
            return False
        item_tag_list = [tag.lower() for tag in inventory_tags_to_list(item_tags)]
        return any(tag in item_tag_list for tag in allowed_tags)

    def has_omit_tag(item_omit_tags: str | list[str] | None) -> bool:
        if not omit_list:
            return False
        item_tag_list = [tag.lower() for tag in inventory_tags_to_list(item_omit_tags)]
        return any(tag in item_tag_list for tag in omit_list)

    filtered = [
        item
        for item in items
        if has_allowed_tag(item.get("tags")) and not has_omit_tag(item.get("omit_tags"))
    ]
    # If no items matched the provided tags, fall back to all items minus omitted ones.
    if tag_list and not filtered:
        filtered = [item for item in items if not has_omit_tag(item.get("omit_tags"))]

    # hide items with no stock when stock tracking is enabled
    return [
        item
        for item in filtered
        if item.get("quantity_in_stock") is None or item.get("quantity_in_stock") > 0
    ]


def inventory_available_for_user(user: User | None) -> bool:
    return bool(user and "inventory" in (user.extensions or []))


async def tabs_available_for_user(user_id: str) -> bool:
    installed = await get_installed_extension("tabs")
    if not installed or not installed.active:
        return False
    active_extensions = await get_user_active_extensions_ids(user_id)
    return "tabs" in active_extensions


async def ensure_tpos_tabs_access(tpos: Tpos) -> str:
    if not tpos.tabs_enabled:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Tabs integration is not enabled for this TPoS.",
        )
    user_id = await get_tpos_owner_user_id(tpos)
    if not await tabs_available_for_user(user_id):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Tabs integration is unavailable for this TPoS.",
        )
    return user_id


async def fetch_tabs_for_tpos(
    user_id: str,
    wallet_id: str,
    status: str | None = "open",
    query: str | None = None,
) -> list[dict[str, Any]]:
    if status and status not in _TAB_STATUSES:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Invalid tab status filter.",
        )
    access = _create_internal_user_access_token(user_id)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url=f"http://{settings.host}:{settings.port}/tabs/api/v1/tabs",
                headers={"Authorization": f"Bearer {access}"},
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _raise_tabs_bridge_error(exc) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_GATEWAY,
            detail="Tabs service is temporarily unavailable.",
        ) from exc
    payload = resp.json()
    if not isinstance(payload, list):
        return []
    tabs = [tab for tab in payload if tab.get("wallet") == wallet_id]
    if status:
        tabs = [tab for tab in tabs if tab.get("status") == status]
    if query:
        needle = query.lower()
        tabs = [
            tab
            for tab in tabs
            if needle in (tab.get("name") or "").lower()
            or needle in (tab.get("customer_name") or "").lower()
            or needle in (tab.get("reference") or "").lower()
            or needle in (tab.get("id") or "").lower()
        ]
    tabs.sort(key=lambda tab: tab.get("updated_at") or "", reverse=True)
    return tabs[:50]


async def create_tab_for_tpos(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    access = _create_internal_user_access_token(user_id)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url=f"http://{settings.host}:{settings.port}/tabs/api/v1/tabs",
                headers={"Authorization": f"Bearer {access}"},
                json=payload,
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _raise_tabs_bridge_error(exc) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_GATEWAY,
            detail="Tabs service is temporarily unavailable.",
        ) from exc
    return resp.json()


async def create_tab_charge_for_tpos(
    user_id: str,
    tab_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    access = _create_internal_user_access_token(user_id)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url=f"http://{settings.host}:{settings.port}/tabs/api/v1/tabs/{tab_id}/entries",
                headers={"Authorization": f"Bearer {access}"},
                json=payload,
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _raise_tabs_bridge_error(exc) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_GATEWAY,
            detail="Tabs service is temporarily unavailable.",
        ) from exc
    return resp.json()


async def fetch_single_tab_for_tpos(user_id: str, tab_id: str) -> dict[str, Any]:
    access = _create_internal_user_access_token(user_id)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url=f"http://{settings.host}:{settings.port}/tabs/api/v1/tabs/{tab_id}",
                headers={"Authorization": f"Bearer {access}"},
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _raise_tabs_bridge_error(exc) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_GATEWAY,
            detail="Tabs service is temporarily unavailable.",
        ) from exc
    return resp.json()


async def create_tab_settlement_for_tpos(
    user_id: str,
    tab_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    access = _create_internal_user_access_token(user_id)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url=f"http://{settings.host}:{settings.port}/tabs/api/v1/tabs/{tab_id}/settlements",
                headers={"Authorization": f"Bearer {access}"},
                json=payload,
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _raise_tabs_bridge_error(exc) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_GATEWAY,
            detail="Tabs service is temporarily unavailable.",
        ) from exc
    return resp.json()


async def push_order_to_orders(
    user_id: str,
    payment,
    tpos,
    base_url: str | None = None,
) -> None:
    details = payment.extra.get("details") or {}
    payload = {
        "source": "tpos",
        "tpos_id": payment.extra.get("tpos_id"),
        "tpos_name": tpos.name if tpos else None,
        "payment_hash": payment.payment_hash,
        "checking_id": payment.checking_id,
        "amount_msat": payment.amount,
        "fee_msat": payment.fee,
        "memo": payment.memo,
        "paid_in_fiat": bool(payment.extra.get("paid_in_fiat")),
        "currency": details.get("currency"),
        "exchange_rate": details.get("exchangeRate")
        or payment.extra.get("exchangeRate"),
        "tax_included": details.get("taxIncluded"),
        "tax_value": details.get("taxValue"),
        "items": details.get("items") or [],
        "notes": payment.extra.get("notes"),
        "created_at": payment.time.isoformat() if payment.time else None,
        "paid": True,
        "shipped": True,
    }

    access = _create_internal_user_access_token(user_id)
    params = {}
    if base_url:
        params["base_url"] = base_url
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                url=f"http://{settings.host}:{settings.port}/orders/api/v1/orders",
                headers={"Authorization": f"Bearer {access}"},
                params=params,
                json=payload,
            )
        except Exception as exc:
            logger.warning(f"tpos: failed to push order to orders: {exc}")


def _create_internal_user_access_token(user_id: str) -> str:
    return create_access_token({"sub": "", "usr": user_id}, token_expire_minutes=1)


def _raise_tabs_bridge_error(exc: httpx.HTTPStatusError) -> HTTPException:
    status_code = exc.response.status_code if exc.response else HTTPStatus.BAD_GATEWAY
    if status_code == HTTPStatus.NOT_FOUND:
        detail = "Tab not found."
    elif status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        detail = "Tabs action not allowed for this TPoS."
    elif status_code == HTTPStatus.BAD_REQUEST:
        detail = "Invalid tabs request."
    else:
        detail = "Tabs service is temporarily unavailable."
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        status_code = HTTPStatus.BAD_GATEWAY
    return HTTPException(status_code=status_code, detail=detail)
