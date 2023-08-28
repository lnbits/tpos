from http import HTTPStatus

from lnbits.core.views.api import pay_invoice

from . import tpos_ext
from .crud import (
    get_tpos,
    get_lnurlcharge,
    update_lnurlcharge,
    update_tpos
)

async def lnurl_params(
    request: Request,
    lnurlcharge_id: str,
    amount: str
):
    lnurlcharge = await get_lnurlcharge(lnurlcharge_id)
    if not lnurlcharge:
        return {
            "status": "ERROR",
            "reason": f"lnurlcharge {lnurlcharge_id} not found on this server",
        }
    tpos = await get_tpos(lnurlcharge.tpos_id)
    if not tpos:
        return {
            "status": "ERROR",
            "reason": f"TPoS {lnurlcharge.tpos_id} not found on this server",
        }
    if amount > tpos.withdrawamtposs:
        return {
            "status": "ERROR",
            "reason": f"Amount requested {amount} is too high, try again with a smaller amount.",
        }
    await update_lnurlcharge(amount=int(amount), lnurlcharge_id=lnurlcharge_id)
    return {
        "tag": "withdrawRequest",
        "callback": request.url_for(
            "tposlnurlcharge.callback", paymentid=lnurlcharge_id
        ),
        "k1": p,
        "minWithdrawable": amount,
        "maxWithdrawable": amount,
        "defaultDescription": "TPoS withdraw",
    }


@lnurldevice_ext.get(
    "/api/v1/lnurl/cb/{paymentid}",
    status_code=HTTPStatus.OK,
    name="tposlnurlcharge.callback",
)
async def lnurl_callback(
    request: Request,
    pr: str = Query(None),
    k1: str = Query(None),
):
    lnurlcharge = await get_lnurlcharge(k1)
    if not lnurlcharge:
        return {
            "status": "ERROR",
            "reason": f"lnurlcharge {k1} not found on this server",
        }
    if lnurlcharge.claimed:
        return {
            "status": "ERROR",
            "reason": f"lnurlcharge {k1} already claimed",
        }
    tpos = await get_tpos(lnurlcharge.tpos_id)
    if not tpos:
        return {
            "status": "ERROR",
            "reason": f"TPoS {lnurlcharge.tpos_id} not found on this server",
        }
    if lnurlcharge.amount > tpos.withdrawamtposs:
        return {
            "status": "ERROR",
            "reason": f"Amount requested {lnurlcharge.amount} is too high, try again with a smaller amount.",
        }   
    try:
        await update_lnurlcharge(claimed=True, lnurlcharge_id=k1)
        await update_tpos(withdrawamt=True, tpos_id=lnurlcharge.tpos_id, timebool=True)
        await pay_invoice(
            wallet_id=tpos.wallet,
            payment_request=pr,
            max_sat=lnurlcharge.amount,
            extra={"tag": "TPoS"},
        )
    except:
        await update_lnurlcharge(claimed=False, lnurlcharge_id=k1)
        return {"status": "ERROR", "reason": "Payment failed, use a different wallet."}