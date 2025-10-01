from time import time

from fastapi import APIRouter, Request
from lnbits.core.services import get_pr_from_lnurl, pay_invoice, websocket_updater
from lnurl import (
    CallbackUrl,
    LnurlErrorResponse,
    LnurlSuccessResponse,
    LnurlWithdrawResponse,
    MilliSatoshi,
)
from loguru import logger
from pydantic import parse_obj_as

from .crud import get_lnurlcharge, get_tpos, update_lnurlcharge, update_tpos

tpos_lnurl_router = APIRouter(prefix="/api/v1/lnurl", tags=["LNURL"])


async def pay_tribute(
    withdraw_amount: int, wallet_id: str, percent: float = 0.5
) -> None:
    try:
        tribute = int(percent * (withdraw_amount / 100))
        tribute = max(1, tribute)
        try:
            pr = await get_pr_from_lnurl(
                "lnbits@nostr.com",
                tribute * 1000,
                comment="LNbits TPoS tribute",
            )
        except Exception:
            logger.warning("Error fetching tribute invoice")
            return
        await pay_invoice(
            wallet_id=wallet_id,
            payment_request=pr,
            max_sat=tribute,
            description="Tribute to help support LNbits",
        )
    except Exception:
        logger.warning("Error paying tribute")
    return


@tpos_lnurl_router.get("/{lnurlcharge_id}/{amount}", name="tpos.tposlnurlcharge")
async def lnurl_params(
    request: Request,
    lnurlcharge_id: str,
    amount: int,
) -> LnurlWithdrawResponse | LnurlErrorResponse:
    lnurlcharge = await get_lnurlcharge(lnurlcharge_id)
    if not lnurlcharge:
        return LnurlErrorResponse(
            reason=f"lnurlcharge {lnurlcharge_id} not found on this server"
        )

    tpos = await get_tpos(lnurlcharge.tpos_id)
    if not tpos:
        return LnurlErrorResponse(
            reason=f"TPoS {lnurlcharge.tpos_id} not found on this server"
        )

    if amount > tpos.withdraw_maximum:
        return LnurlErrorResponse(
            reason=(
                f"Amount requested {amount} is too high, "
                f"maximum withdrawable is {tpos.withdraw_maximum}"
            )
        )

    logger.debug(f"Amount to withdraw: {amount}")
    callback = parse_obj_as(
        CallbackUrl, str(request.url_for("tpos.tposlnurlcharge.callback"))
    )
    return LnurlWithdrawResponse(
        callback=callback,
        k1=lnurlcharge_id,
        minWithdrawable=MilliSatoshi(amount * 1000),
        maxWithdrawable=MilliSatoshi(amount * 1000),
        defaultDescription="TPoS withdraw",
    )


@tpos_lnurl_router.get("/cb", name="tpos.tposlnurlcharge.callback")
async def lnurl_callback(
    pr: str | None = None,
    k1: str | None = None,
) -> LnurlErrorResponse | LnurlSuccessResponse:
    if not pr:
        return LnurlErrorResponse(reason="Payment request (pr) is required")
    if not k1:
        return LnurlErrorResponse(reason="k1 is required")

    lnurlcharge = await get_lnurlcharge(k1)
    if not lnurlcharge:
        return LnurlErrorResponse(reason=f"lnurlcharge {k1} not found on this server")

    if not lnurlcharge.amount:
        return LnurlErrorResponse(reason=f"LnurlCharge {k1} has no amount specified")

    if lnurlcharge.claimed:
        return LnurlErrorResponse(reason=f"LnurlCharge {k1} has already been claimed")

    tpos = await get_tpos(lnurlcharge.tpos_id)
    if not tpos:
        return LnurlErrorResponse(
            reason=f"TPoS {lnurlcharge.tpos_id} not found on this server"
        )

    if lnurlcharge.amount > tpos.withdraw_maximum:
        return LnurlErrorResponse(
            reason=(
                f"Amount requested {lnurlcharge.amount} is too high, "
                f"maximum withdrawable is {tpos.withdraw_maximum}"
            )
        )

    lnurlcharge.claimed = True
    await update_lnurlcharge(lnurlcharge)

    tpos.withdrawn_amount = int(tpos.withdrawn_amount or 0) + int(lnurlcharge.amount)
    tpos.withdraw_time = int(time())
    await update_tpos(tpos)

    try:
        await pay_invoice(
            wallet_id=tpos.wallet,
            payment_request=pr,
            max_sat=int(lnurlcharge.amount),
            extra={"tag": "TPoSWithdraw", "tpos_id": lnurlcharge.tpos_id},
        )
        await websocket_updater(k1, "paid")
    except Exception as exc:
        return LnurlErrorResponse(reason=f"withdraw not working. {exc!s}")

    # pay tribute to help support LNbits
    await pay_tribute(
        withdraw_amount=int(lnurlcharge.amount), wallet_id=tpos.wallet
    )

    return LnurlSuccessResponse()
