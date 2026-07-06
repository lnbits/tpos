from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from lnbits.core.models import WalletTypeInfo
from lnbits.decorators import require_admin_key

from .services import (
    fetch_watchonly_config,
    fetch_watchonly_wallet,
    fetch_watchonly_wallets,
    watchonly_available_for_user,
)

tpos_onchain_router = APIRouter()


async def _get_watchonly_status(wallet) -> dict[str, Any]:
    if not await watchonly_available_for_user(wallet.user):
        return {
            "available": False,
            "message": "Watchonly extension must be enabled for this user.",
            "network": None,
            "wallets": [],
        }

    try:
        config = await fetch_watchonly_config(wallet.inkey)
        network_value = config.get("network")
        if not isinstance(network_value, str) or not network_value:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Watchonly extension returned an invalid network configuration.",
            )
        network = network_value
        wallets = await fetch_watchonly_wallets(wallet.inkey, network)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Watchonly extension is not reachable: {exc!s}",
        ) from exc

    return {
        "available": True,
        "message": None,
        "network": network,
        "wallets": wallets,
        "mempool_endpoint": config.get("mempool_endpoint"),
    }


async def _validate_watchonly_settings(
    *,
    wallet,
    onchain_enabled: bool,
    onchain_wallet_id: str | None,
) -> dict[str, Any] | None:
    if not onchain_enabled:
        return None
    if not onchain_wallet_id:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Watchonly wallet is required when onchain payments are enabled.",
        )

    status = await _get_watchonly_status(wallet)
    if not status["available"]:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=status["message"] or "Watchonly extension is not available.",
        )

    try:
        watch_wallet = await fetch_watchonly_wallet(wallet.inkey, onchain_wallet_id)
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Cannot access watchonly wallet: {exc!s}",
        ) from exc

    if watch_wallet.get("network") != status["network"]:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Watchonly wallet network does not match the user watchonly config.",
        )

    return {
        "watch_wallet": watch_wallet,
        "network": status["network"],
        "mempool_endpoint": status["mempool_endpoint"],
    }


@tpos_onchain_router.get("/api/v1/onchain/status", status_code=HTTPStatus.OK)
async def api_onchain_status(
    key_info: WalletTypeInfo = Depends(require_admin_key),
) -> dict[str, Any]:
    return await _get_watchonly_status(key_info.wallet)
