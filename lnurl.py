from http import HTTPStatus
from lnbits.core.services import websocketUpdater
from lnbits.core.views.api import pay_invoice
from fastapi import Request
from typing import Optional
from . import tpos_ext
from .crud import get_tpos, get_lnurlcharge, update_lnurlcharge, update_tpos
from .models import LNURLCharge
from loguru import logger
from starlette.exceptions import HTTPException


@tpos_ext.get(
    "/api/v1/lnurl/{lnurlcharge_id}/{amount}",
    status_code=HTTPStatus.OK,
    name="tpos.tposlnurlcharge",
)
async def lnurl_params(
    request: Request,
    lnurlcharge_id: Optional[str] = None,
    amount: Optional[str] = None,
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
    assert amount, "amount is required"
    # convert amount to int
    _amount = int(amount)
    if _amount > tpos.withdrawamtposs:
        return {
            "status": "ERROR",
            "reason": f"Amount requested {_amount} is too high, try again with a smaller amount.",
        }
    logger.debug(f"Amount to withdraw: {_amount}")
    return {
        "tag": "withdrawRequest",
        "callback": str(request.url_for("tpos.tposlnurlcharge.callback")),
        "k1": lnurlcharge_id,
        "minWithdrawable": _amount * 1000,
        "maxWithdrawable": _amount * 1000,
        "defaultDescription": "TPoS withdraw",
    }


@tpos_ext.get(
    "/api/v1/lnurl/cb",
    status_code=HTTPStatus.OK,
    name="tpos.tposlnurlcharge.callback",
)
async def lnurl_callback(
    request: Request,
    pr: Optional[str] = None,
    k1: Optional[str] = None,
):
    assert k1, "k1 is required"
    assert pr, "pr is required"

    lnurlcharge = await get_lnurlcharge(k1)
    assert lnurlcharge
    assert lnurlcharge.amount

    logger.debug(f"LNURLCharge: {lnurlcharge}, {lnurlcharge.amount}, {pr}")

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

    await update_lnurlcharge(
        LNURLCharge(
            id=k1,
            tpos_id=lnurlcharge.tpos_id,
            amount=int(lnurlcharge.amount),
            claimed=True,
        )
    )
    tpos.withdrawamt = int(tpos.withdrawamt) + int(lnurlcharge.amount)
    logger.debug(f"Payment request: {pr}")
    await update_tpos(data=tpos, tpos_id=lnurlcharge.tpos_id, timebool=True)
    logger.debug(f"Amount to withdraw: {int(lnurlcharge.amount)}")
    try:
        await pay_invoice(
            wallet_id=tpos.wallet,
            payment_request=pr,
            max_sat=int(lnurlcharge.amount),
            extra={"tag": "TPoSWithdraw", "tpos_id": lnurlcharge.tpos_id},
        )
        await websocketUpdater(k1, "paid")
    except Exception as e:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=f"withdraw not working. {str(e)}"
        )
    return {"status": "OK"}
