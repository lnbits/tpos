import re
from typing import Any

import httpx
from embit.script import Script  # type: ignore[import-untyped]
from lnbits.core.crud import (
    get_installed_extension,
    get_user_active_extensions_ids,
)
from lnbits.core.models.wallets import OnchainConfig
from lnbits.core.services.blockexplorer import (
    NETWORKS,
    MempoolExplorer,
    explorer_client,
    mempool_url,
)
from lnbits.settings import settings
from lnbits.utils.electrum import address_to_scriptpubkey, network_from_name
from lnbits.utils.safe_http import SafeHttpClient


async def watchonly_available_for_user(user_id: str) -> bool:
    installed = await get_installed_extension("watchonly")
    if not installed or not installed.active:
        return False
    active_extensions = await get_user_active_extensions_ids(user_id)
    return "watchonly" in active_extensions


# Watchonly bridge URLs use deployment-trusted host/port, not user destinations.
async def fetch_watchonly_config(api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/watchonly/api/v1/config",
            headers={"X-API-KEY": api_key},
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_watchonly_wallets(api_key: str, network: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/watchonly/api/v1/wallet",
            headers={"X-API-KEY": api_key},
            params={"network": network},
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_watchonly_wallet(api_key: str, wallet_id: str) -> dict[str, Any]:
    _validate_wallet_id(wallet_id)
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/watchonly/api/v1/wallet/{wallet_id}",
            headers={"X-API-KEY": api_key},
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_onchain_address(
    api_key: str, wallet_id: str, *, core: bool = False
) -> dict[str, Any]:
    _validate_wallet_id(wallet_id)
    route = "api/v1/onchain" if core else "watchonly/api/v1"
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/{route}/address/{wallet_id}",
            headers={"X-API-KEY": api_key},
        )
        resp.raise_for_status()
        return resp.json()


def onchain_mempool_url(config: OnchainConfig, network: str) -> str:
    suffix = httpx.URL(config.mempool_endpoint).path.rstrip("/").rsplit("/", 1)[-1]
    if suffix in {"testnet", "testnet4", "signet"} and suffix != network.lower():
        raise ValueError("Explorer network mismatch.")
    return mempool_url(config, network)


def validate_onchain_address(address: str, network: str) -> None:
    network_id = next(key for key, name in NETWORKS.items() if name == network)
    if (
        Script(address_to_scriptpubkey(address)).address(network_from_name(network_id))
        != address
    ):
        raise ValueError("Address network mismatch.")


async def fetch_onchain_balance(
    mempool_endpoint: str | None, onchain_address: str, network: str
) -> dict[str, int]:
    validate_onchain_address(onchain_address, network)
    if mempool_endpoint is None:
        config = OnchainConfig(explorer_provider="lnbits")
        async with explorer_client(config, network) as explorer:
            data = await explorer.history(onchain_address)
    else:
        config = OnchainConfig(mempool_endpoint=mempool_endpoint)
        async with SafeHttpClient(apply_callback_rules=False) as safe_client:
            client = httpx.AsyncClient(
                base_url=onchain_mempool_url(config, network) + "/",
                transport=_SafeExplorerTransport(safe_client),
            )
            async with MempoolExplorer(config, network, client) as explorer:
                data = await explorer.history(onchain_address)
    data = list({tx["txid"]: tx for tx in data}.values())
    confirmed_txs = [tx for tx in data if tx["status"]["confirmed"]]
    unconfirmed_txs = [tx for tx in data if not tx["status"]["confirmed"]]
    return {
        "confirmed": sum_transactions(onchain_address, confirmed_txs),
        "unconfirmed": sum_transactions(onchain_address, unconfirmed_txs),
    }


def sum_outputs(address: str, vouts: list[dict[str, Any]]) -> int:
    return sum(
        vout["value"] for vout in vouts if vout.get("scriptpubkey_address") == address
    )


def sum_transactions(address: str, txs: list[dict[str, Any]]) -> int:
    return sum(sum_outputs(address, tx.get("vout", [])) for tx in txs)


def _validate_wallet_id(wallet_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", wallet_id):
        raise ValueError("Invalid on-chain wallet ID.")


class _SafeExplorerTransport(httpx.AsyncBaseTransport):
    # Core's explorer handles pagination. Keep TPoS DNS pinning on each request.
    def __init__(self, client: SafeHttpClient):
        self.client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.client.get(request.url)
