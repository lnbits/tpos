import httpx
from lnbits.core.views.api import api_lnurlscan


async def get_pr(ln_address, amount):
    try:
        data = await api_lnurlscan(ln_address)
        if data.get("status") == "ERROR":
            return
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url=f"{data['callback']}?amount={int(amount) * 1000}"
            )
            if response.status_code != 200:
                return
            return response.json()["pr"]
    except Exception:
        return None
