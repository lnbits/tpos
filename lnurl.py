from http import HTTPStatus
from fastapi import Request
from typing import Optional
from starlette.exceptions import HTTPException
from lnbits.helpers import urlsafe_short_hash
from lnbits.core.services import websocketUpdater
from lnbits.core.views.api import pay_invoice

from . import tpos_ext
from .crud import get_tpos, get_lnurlcharge, update_lnurlcharge, update_tpos_withdraw
from .models import LNURLCharge
from loguru import logger


class LNURLChargeNotFoundException(Exception):
    def __init__(self, lnurlcharge_id):
        self.lnurlcharge_id = lnurlcharge_id
        super().__init__(f"LNURLCharge with ID {lnurlcharge_id} not found")


class TPoSNotFoundException(Exception):
    def __init__(self, tpos_id):
        self.tpos_id = tpos_id
        super().__init__(f"TPoS with ID {tpos_id} not found")


class WithdrawAmountTooHighException(Exception):
    def __init__(self, requested_amount, max_withdrawable):
        self.requested_amount = requested_amount
        self.max_withdrawable = max_withdrawable
        super().__init__(
            f"Amount requested {requested_amount} is too high, maximum withdrawable is {max_withdrawable}"
        )


@tpos_ext.get(
    "/api/v1/lnurl/{lnurlcharge_id}/{amount}",
    status_code=HTTPStatus.OK,
    name="tpos.tposlnurlcharge",
)
async def lnurl_params(
    request: Request,
    lnurlcharge_id: str,
    amount: int,
):
    logger.debug(amount)
    lnurlcharge = await get_lnurlcharge(lnurlcharge_id)
    if not lnurlcharge:
        raise LNURLChargeNotFoundException(lnurlcharge_id)

    tpos = await get_tpos(lnurlcharge.tpos_id)
    if not tpos:
        raise TPoSNotFoundException(lnurlcharge.tpos_id)

    # assert amount, "amount is required"
    # # convert amount to int
    # _amount = int(amount)

    if amount > tpos.withdrawamtposs:
        raise WithdrawAmountTooHighException(amount, tpos.withdrawamtposs)

    logger.debug(f"Amount to withdraw: {amount}")
    return {
        "tag": "withdrawRequest",
        "callback": str(request.url_for("tpos.tposlnurlcharge.callback")),
        "k1": lnurlcharge_id,
        "minWithdrawable": amount * 1000,
        "maxWithdrawable": amount * 1000,
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
    if not lnurlcharge:
        raise LNURLChargeNotFoundException(k1)

    assert lnurlcharge.amount, f"LNURLCharge {k1} has no amount"

    if lnurlcharge.claimed:
        raise Exception(f"LNURLCharge {k1} has already been claimed")

    tpos = await get_tpos(lnurlcharge.tpos_id)
    if not tpos:
        raise TPoSNotFoundException(lnurlcharge.tpos_id)

    if lnurlcharge.amount > tpos.withdrawamtposs:
        raise WithdrawAmountTooHighException(lnurlcharge.amount, tpos.withdrawamtposs)

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
    await update_tpos_withdraw(data=tpos, tpos_id=lnurlcharge.tpos_id)
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
