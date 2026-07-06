from http import HTTPStatus
from typing import Any

import httpx
from fastapi import HTTPException
from lnbits.core.crud import (
    get_installed_extension,
    get_user_active_extensions_ids,
    get_wallet,
)
from lnbits.settings import settings
from loguru import logger

from .helpers import create_internal_user_access_token
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
    access = create_internal_user_access_token(user_id)
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
    access = create_internal_user_access_token(user_id)
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
    access = create_internal_user_access_token(user_id)
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
    access = create_internal_user_access_token(user_id)
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
    access = create_internal_user_access_token(user_id)
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

    access = create_internal_user_access_token(user_id)
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


def _raise_tabs_bridge_error(exc: httpx.HTTPStatusError) -> HTTPException:
    status_code = exc.response.status_code if exc.response else HTTPStatus.BAD_GATEWAY
    if status_code == HTTPStatus.NOT_FOUND:
        detail = "Tab not found."
    elif status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        detail = "Tabs action not allowed for this TPoS."
    elif status_code == HTTPStatus.BAD_REQUEST:
        try:
            response_detail = exc.response.json().get("detail")
        except Exception:
            response_detail = None
        detail = response_detail or "Invalid tabs request."
    else:
        detail = "Tabs service is temporarily unavailable."
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        status_code = HTTPStatus.BAD_GATEWAY
    return HTTPException(status_code=status_code, detail=detail)
