from typing import Any

import httpx
from lnbits.core.crud import (
    get_installed_extension,
    get_user_active_extensions_ids,
)
from lnbits.settings import settings


async def watchonly_available_for_user(user_id: str) -> bool:
    installed = await get_installed_extension("watchonly")
    if not installed or not installed.active:
        return False
    active_extensions = await get_user_active_extensions_ids(user_id)
    return "watchonly" in active_extensions


async def fetch_watchonly_config(api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/watchonly/api/v1/config",
            headers={"X-API-KEY": api_key},
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_watchonly_wallets(api_key: str, network: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/watchonly/api/v1/wallet",
            headers={"X-API-KEY": api_key},
            params={"network": network},
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_watchonly_wallet(api_key: str, wallet_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/watchonly/api/v1/wallet/{wallet_id}",
            headers={"X-API-KEY": api_key},
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_onchain_address(api_key: str, wallet_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/watchonly/api/v1/address/{wallet_id}",
            headers={"X-API-KEY": api_key},
        )
        resp.raise_for_status()
        return resp.json()


def normalize_mempool_endpoint(
    mempool_endpoint: str | None, onchain_address: str
) -> str:
    endpoint = (mempool_endpoint or "https://mempool.space").rstrip("/")
    if "/testnet" in endpoint or "/signet" in endpoint:
        return endpoint
    if onchain_address.lower().startswith("tb1"):
        return f"{endpoint}/testnet"
    return endpoint


async def fetch_onchain_balance(
    mempool_endpoint: str, onchain_address: str
) -> dict[str, Any]:
    endpoint = normalize_mempool_endpoint(mempool_endpoint, onchain_address)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{endpoint}/api/address/{onchain_address}/txs")
        resp.raise_for_status()
        data = resp.json()
    confirmed_txs = [tx for tx in data if tx["status"]["confirmed"]]
    unconfirmed_txs = [tx for tx in data if not tx["status"]["confirmed"]]
    return {
        "confirmed": sum_transactions(onchain_address, confirmed_txs),
        "unconfirmed": sum_transactions(onchain_address, unconfirmed_txs),
        "txids": [tx["txid"] for tx in data],
    }


def sum_outputs(address: str, vouts: list[dict[str, Any]]) -> int:
    return sum(
        vout["value"] for vout in vouts if vout.get("scriptpubkey_address") == address
    )


def sum_transactions(address: str, txs: list[dict[str, Any]]) -> int:
    return sum(sum_outputs(address, tx.get("vout", [])) for tx in txs)
