from http import HTTPStatus
from typing import Any

import httpx
from fastapi import HTTPException
from lnbits.core.crud import (
    get_installed_extension,
    get_user_active_extensions_ids,
)
from lnbits.settings import settings

from .helpers import create_internal_user_access_token

_TAB_STATUSES = {"open", "suspended", "closed"}


async def tabs_available_for_user(user_id: str) -> bool:
    installed = await get_installed_extension("tabs")
    if not installed or not installed.active:
        return False
    active_extensions = await get_user_active_extensions_ids(user_id)
    return "tabs" in active_extensions


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
