import json
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .crud import get_tpos
from .models import CreateTposTabCharge, CreateTposTabData, Tpos, TposTab, TposTabList
from .services import ensure_tpos_tabs_access
from .services_tabs import (
    create_tab_charge_for_tpos,
    create_tab_for_tpos,
    fetch_single_tab_for_tpos,
    fetch_tabs_for_tpos,
)

tpos_tabs_router = APIRouter()


async def _get_tpos_or_404(tpos_id: str) -> Tpos:
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    return tpos


def _tpos_currency(tpos: Tpos) -> str:
    return (tpos.currency or "sats").lower()


def _ensure_tab_matches_tpos_currency(tab: dict[str, Any], tpos: Tpos) -> None:
    if (tab.get("currency") or "sats").lower() != _tpos_currency(tpos):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Tab currency must match TPoS currency.",
        )


def _tab_settlement_tolerance(currency: str | None) -> float:
    return 1 if (currency or "sats").lower() == "sats" else 0.01


@tpos_tabs_router.get("/api/v1/tposs/{tpos_id}/tabs", response_model=TposTabList)
async def api_tpos_tabs(
    tpos_id: str,
    status: str = Query("open"),
    q: str | None = Query(None),
) -> TposTabList:
    tpos = await _get_tpos_or_404(tpos_id)
    user_id = await ensure_tpos_tabs_access(tpos)
    tabs = await fetch_tabs_for_tpos(
        user_id=user_id,
        wallet_id=tpos.wallet,
        status=status,
        query=q,
    )
    return TposTabList(data=[TposTab(**tab) for tab in tabs])


@tpos_tabs_router.post("/api/v1/tposs/{tpos_id}/tabs", response_model=TposTab)
async def api_tpos_create_tab(
    tpos_id: str,
    data: CreateTposTabData,
) -> TposTab:
    tpos = await _get_tpos_or_404(tpos_id)
    user_id = await ensure_tpos_tabs_access(tpos)
    if not tpos.tabs_allow_create:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="Tab creation is not enabled for this TPoS.",
        )
    tab_currency = (data.currency or _tpos_currency(tpos)).lower()
    if tab_currency != _tpos_currency(tpos):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Tab currency must match TPoS currency.",
        )

    payload = {
        "wallet": tpos.wallet,
        "name": data.name,
        "customer_name": data.customer_name,
        "reference": data.reference,
        "currency": tab_currency,
        "limit_type": data.limit_type,
        "limit_amount": data.limit_amount,
    }
    tab = await create_tab_for_tpos(user_id=user_id, payload=payload)
    return TposTab(**tab)


@tpos_tabs_router.post("/api/v1/tposs/{tpos_id}/tabs/{tab_id}/charges")
async def api_tpos_add_tab_charge(
    tpos_id: str,
    tab_id: str,
    data: CreateTposTabCharge,
) -> dict[str, Any]:
    tpos = await _get_tpos_or_404(tpos_id)
    user_id = await ensure_tpos_tabs_access(tpos)

    tab = await fetch_single_tab_for_tpos(user_id=user_id, tab_id=tab_id)
    _ensure_tab_matches_tpos_currency(tab, tpos)

    metadata = {
        "source": "tpos",
        "tpos_id": tpos.id,
        "tpos_name": tpos.name,
        "currency": tpos.currency,
        "amount": data.amount,
        "items": data.items,
        "notes": data.notes,
        "internal_memo": data.internal_memo,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = {
        "entry_type": "charge",
        "amount": data.amount,
        "description": data.description or "TPoS order charge",
        "metadata": json.dumps(metadata),
        "source": "tpos",
        "source_id": tpos.id,
        "source_action": "order_charge",
        "idempotency_key": data.idempotency_key,
    }
    entry = await create_tab_charge_for_tpos(
        user_id=user_id,
        tab_id=tab_id,
        payload=payload,
    )
    updated_tab = await fetch_single_tab_for_tpos(user_id=user_id, tab_id=tab_id)
    return {"tab_id": tab_id, "entry": entry, "tab": TposTab(**updated_tab).dict()}
