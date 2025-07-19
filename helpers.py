from lnbits.settings import settings
from lnurl import LnurlPayResponse
from lnurl import execute_pay_request as lnurlp
from lnurl import handle as lnurl_handle


async def get_pr(ln_address: str, amount: int) -> str | None:
    try:
        res = await lnurl_handle(ln_address)
        if not isinstance(res, LnurlPayResponse):
            return None
        res2 = await lnurlp(
            res,
            msat=str(amount * 1000),
            user_agent=settings.user_agent,
            timeout=5,
        )
        return res2.pr
    except Exception as e:
        print(f"Error handling LNURL: {e}")
        return None
