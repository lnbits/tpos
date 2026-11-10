from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from httpx import InvalidURL
from lnbits.core.crud.wallets import get_onchain_wallet
from lnbits.core.models import Wallet, WalletTypeInfo
from lnbits.core.models.wallets import OnchainConfig
from lnbits.core.services.blockexplorer import (
    NETWORKS,
    local_explorer_network,
    provider_name,
    validate_mempool_endpoint,
)
from lnbits.decorators import require_admin_key
from lnbits.utils.safe_http import validate_url

from .services_onchain import (
    fetch_watchonly_config,
    fetch_watchonly_wallet,
    fetch_watchonly_wallets,
    onchain_mempool_url,
    watchonly_available_for_user,
)

tpos_onchain_router = APIRouter()


async def _get_watchonly_status(wallet) -> dict[str, Any]:
    if not await watchonly_available_for_user(wallet.user):
        return {
            "available": False,
            "message": "Set up an LNbits on-chain wallet or enable Watchonly.",
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
            detail="Watchonly extension is not reachable.",
        ) from exc

    return {
        "available": True,
        "message": None,
        "network": network,
        "wallets": wallets,
        "mempool_endpoint": config.get("mempool_endpoint"),
    }


async def validate_onchain_settings(
    *,
    wallet: Wallet,
    onchain_enabled: bool,
    onchain_wallet_id: str | None,
) -> tuple[str, str, str | None] | None:
    if not onchain_enabled:
        return None
    if not onchain_wallet_id:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Select an on-chain wallet.",
        )

    network: str
    if onchain_wallet_id.startswith("core:"):
        core_wallet = await get_onchain_wallet(onchain_wallet_id.removeprefix("core:"))
        if not core_wallet or core_wallet.user != wallet.user:
            raise HTTPException(
                HTTPStatus.BAD_REQUEST, "Cannot access on-chain wallet."
            )
        if not core_wallet.onchain_wallet_kind or (
            core_wallet.onchain_wallet_kind == "hot"
            and not core_wallet.onchain_meta.backup_confirmed
        ):
            raise HTTPException(HTTPStatus.CONFLICT, "Finish on-chain wallet setup.")
        core_network = core_wallet.onchain_network
        if not core_network:
            raise HTTPException(HTTPStatus.BAD_REQUEST, "Missing on-chain network.")
        network = core_network
        if provider_name(core_wallet.onchain_config, network) == "lnbits":
            if local_explorer_network() != network:
                raise HTTPException(HTTPStatus.CONFLICT, "Explorer network mismatch.")
            return core_wallet.inkey, network, None
        key = core_wallet.inkey
        endpoint = core_wallet.onchain_config.mempool_endpoint
    else:
        if not await watchonly_available_for_user(wallet.user):
            raise HTTPException(
                HTTPStatus.BAD_REQUEST, "Enable the Watchonly extension."
            )
        try:
            config = await fetch_watchonly_config(wallet.inkey)
            watch_wallet = await fetch_watchonly_wallet(wallet.inkey, onchain_wallet_id)
        except Exception as exc:
            raise HTTPException(
                HTTPStatus.BAD_REQUEST, "Cannot access Watchonly wallet."
            ) from exc
        watch_network = config.get("network")
        if (
            not isinstance(watch_network, str)
            or watch_network not in NETWORKS.values()
            or watch_wallet.get("network") != watch_network
        ):
            raise HTTPException(HTTPStatus.BAD_REQUEST, "Watchonly network mismatch.")
        network = watch_network
        key = wallet.inkey
        endpoint = config.get("mempool_endpoint") or "https://mempool.space"

    try:
        explorer_config = OnchainConfig(mempool_endpoint=endpoint)
        validate_mempool_endpoint(explorer_config.mempool_endpoint)
        await validate_url(
            onchain_mempool_url(explorer_config, network), apply_callback_rules=False
        )
    except (ValueError, InvalidURL) as exc:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, "Invalid on-chain explorer."
        ) from exc
    return key, network, explorer_config.mempool_endpoint


@tpos_onchain_router.get("/api/v1/onchain/status", status_code=HTTPStatus.OK)
async def api_onchain_status(
    key_info: WalletTypeInfo = Depends(require_admin_key),
) -> dict[str, Any]:
    return await _get_watchonly_status(key_info.wallet)
