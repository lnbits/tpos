import httpx
from lnbits.core.views.api import api_lnurlscan
from loguru import logger

async def get_pr(ln_address, amount):
    logger.debug(ln_address)
    logger.debug(amount)
    try:
        data = await api_lnurlscan(ln_address)
        if data.get("status") == "ERROR":
            return
        async with httpx.AsyncClient() as client:
            response = await client.get(url=f"{data['callback']}?amount={int(amount) * 1000}")
            if response.status_code != 200:
                logger.debug(response.status_code)
                return
            return response.json()["pr"]
    except Exception:
        return None