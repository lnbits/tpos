import time

import httpx
from lnbits.settings import settings
from loguru import logger

from .crud import get_wrapper_assetlinks_cache, set_wrapper_assetlinks_cache

WRAPPER_ASSETLINKS_URL = (
    "https://github.com/lnbits/TPoS-Stripe-Tap-to-Pay-Wrapper-Stripev5"
    "/releases/latest/download/assetlinks.json"
)
WRAPPER_ASSETLINKS_CACHE_SECONDS = 60 * 60


async def fetch_wrapper_assetlinks() -> dict | list:
    now = int(time.time())
    cached = await get_wrapper_assetlinks_cache()
    if cached:
        cached_assetlinks, cached_at = cached
        cache_fresh = now - cached_at < WRAPPER_ASSETLINKS_CACHE_SECONDS
        if cache_fresh:
            return cached_assetlinks

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, headers={"User-Agent": settings.user_agent}
        ) as client:
            resp = await client.get(WRAPPER_ASSETLINKS_URL, timeout=10)
            resp.raise_for_status()
            assetlinks = resp.json()
    except Exception as exc:
        if cached:
            logger.warning(f"Using cached TPoS wrapper assetlinks.json: {exc!s}")
            return cached[0]
        raise RuntimeError("Unable to fetch TPoS wrapper assetlinks.json.") from exc

    if not isinstance(assetlinks, (dict, list)):
        if cached:
            logger.warning("Using cached TPoS wrapper assetlinks.json: invalid JSON")
            return cached[0]
        raise RuntimeError("TPoS wrapper assetlinks.json is not valid JSON.")

    await set_wrapper_assetlinks_cache(assetlinks, now)
    return assetlinks
