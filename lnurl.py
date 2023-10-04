from http import HTTPStatus
from lnbits.core.services import websocketUpdater
from lnbits.core.views.api import pay_invoice

from . import tpos_ext
from .crud import (
    get_tpos,
    get_lnurlcharge,
    update_lnurlcharge,
)

@tpos_ext.get(
    "/api/v1/lnurl/{lnurlcharge_id}/{amount}",
    status_code=HTTPStatus.OK,
    name="tpos.tposlnurlcharge",
)
async def lnurl_params(
    request: Request,
    lnurlcharge_id: str = Query(None),
    amount: str = Query(None),
):
    logger.debug(amount)
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
    if int(amount) > tpos.withdrawamtposs:
        return {
            "status": "ERROR",
            "reason": f"Amount requested {int(amount)} is too high, try again with a smaller amount.",
        }
    await update_lnurlcharge(amount=int(amount), lnurlcharge_id=lnurlcharge_id)
    return {
        "tag": "withdrawRequest",
        "callback": request.url_for(
            "tpos.tposlnurlcharge.callback"
        ),
        "k1": lnurlcharge_id,
        "minWithdrawable": int(amount),
        "maxWithdrawable": int(amount),
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

    await update_lnurlcharge(LNURLCharge(id=k1, tpos_id=lnurlcharge.tpos_id, amount=int(lnurlcharge.amount), claimed=True))
    await update_tpos(CreateTposData(withdrawamt = int(withdrawamt) + int(lnurlcharge.amount)), tpos_id=lnurlcharge.tpos_id, timebool=True)
    await pay_invoice(
        wallet_id=tpos.wallet,
        payment_request=pr,
        max_sat=int(lnurlcharge.amount),
        extra={"tag": "TPoS"},
    )
    await websocketUpdater(k1, "paid")
