from http import HTTPStatus
from typing import Callable, Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from lnbits.core.services import pay_invoice, websocket_updater
from loguru import logger
from starlette.exceptions import HTTPException

from .crud import get_lnurlcharge, get_tpos, update_lnurlcharge, update_tpos_withdraw
from .models import LnurlCharge


class LNURLErrorResponseHandler(APIRoute):
    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                response = await original_route_handler(request)
            except HTTPException as exc:
                logger.debug(f"HTTPException: {exc}")
                response = JSONResponse(
                    status_code=exc.status_code,
                    content={"status": "ERROR", "reason": f"{exc.detail}"},
                )
            except Exception as exc:
                raise exc

            return response

        return custom_route_handler


tpos_lnurl_router = APIRouter(route_class=LNURLErrorResponseHandler)


@tpos_lnurl_router.get(
    "/api/v1/lnurl/{lnurlcharge_id}/{amount}",
    status_code=HTTPStatus.OK,
    name="tpos.tposlnurlcharge",
)
async def lnurl_params(
    request: Request,
    lnurlcharge_id: str,
    amount: int,
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
            "reason": (
                f"Amount requested {amount} is too high, "
                f"maximum withdrawable is {tpos.withdrawamtposs}"
            ),
        }

    logger.debug(f"Amount to withdraw: {amount}")
    return {
        "tag": "withdrawRequest",
        "callback": str(request.url_for("tpos.tposlnurlcharge.callback")),
        "k1": lnurlcharge_id,
        "minWithdrawable": amount * 1000,
        "maxWithdrawable": amount * 1000,
        "defaultDescription": "TPoS withdraw",
    }


@tpos_lnurl_router.get(
    "/api/v1/lnurl/cb",
    status_code=HTTPStatus.OK,
    name="tpos.tposlnurlcharge.callback",
)
async def lnurl_callback(
    pr: Optional[str] = None,
    k1: Optional[str] = None,
):
    assert k1, "k1 is required"
    assert pr, "pr is required"

    lnurlcharge = await get_lnurlcharge(k1)
    if not lnurlcharge:
        return {
            "status": "ERROR",
            "reason": f"lnurlcharge {k1} not found on this server",
        }

    assert lnurlcharge.amount, f"LnurlCharge {k1} has no amount"

    if lnurlcharge.claimed:
        return {
            "status": "ERROR",
            "reason": f"LnurlCharge {k1} has already been claimed",
        }

    tpos = await get_tpos(lnurlcharge.tpos_id)
    assert tpos, f"TPoS with ID {lnurlcharge.tpos_id} not found"

    assert (
        lnurlcharge.amount < tpos.withdrawamtposs
    ), f"""
    Amount requested {lnurlcharge.amount} is too high,
    maximum withdrawable is {tpos.withdrawamtposs}
    """

    await update_lnurlcharge(
        LnurlCharge(
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
        await websocket_updater(k1, "paid")
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=f"withdraw not working. {exc!s}"
        ) from exc
    return {"status": "OK"}
