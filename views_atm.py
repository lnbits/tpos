from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Request
from lnbits.core.crud import (
    get_wallet,
)
from lnbits.core.models import User
from lnbits.core.models.misc import SimpleStatus
from lnbits.decorators import check_user_exists
from lnurl import (
    CallbackUrl,
    LnurlErrorResponse,
    LnurlPayResponse,
    LnurlWithdrawResponse,
    MilliSatoshi,
    execute_pay_request,
    execute_withdraw,
)
from lnurl import handle as lnurl_handle
from pydantic import parse_obj_as

from .crud import (
    create_lnurlcharge,
    get_lnurlcharge,
    get_tpos,
    update_lnurlcharge,
)
from .models import (
    CreateWithdrawPay,
    LnurlCharge,
)

tpos_atm_router = APIRouter(prefix="/api/v1/atm", tags=["TPoS ATM"])


@tpos_atm_router.post("/{tpos_id}/create")
async def api_tpos_atm_pin_check(
    tpos_id: str, user: User = Depends(check_user_exists)
) -> LnurlCharge:
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(HTTPStatus.NOT_FOUND, "TPoS does not exist.")

    # check if the user has access to the TPoS wallet
    if any(wallet.id == tpos.wallet for wallet in user.wallets) is False:
        raise HTTPException(
            HTTPStatus.FORBIDDEN,
            "You do not have access to this TPoS wallet.",
        )

    if not tpos.can_withdraw:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Withdrawals are not allowed at this time. Try again later.",
        )

    charge = await create_lnurlcharge(tpos.id)
    return charge


@tpos_atm_router.get("/withdraw/{charge_id}/{amount}")
async def api_tpos_create_withdraw(charge_id: str, amount: str) -> LnurlCharge:
    lnurlcharge = await get_lnurlcharge(charge_id)
    if not lnurlcharge:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail=f"lnurlcharge {charge_id} not found on this server.",
        )
    if lnurlcharge.claimed is True:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"lnurlcharge {charge_id} has already been claimed.",
        )
    tpos = await get_tpos(lnurlcharge.tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail=f"TPoS {lnurlcharge.tpos_id} does not exist.",
        )

    wallet = await get_wallet(tpos.wallet)
    if not wallet:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail=f"Wallet {tpos.wallet} does not exist.",
        )

    balance = int(wallet.balance_msat / 1000)
    if balance < int(amount):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Insufficient balance. Your balance is {balance} sats",
        )
    lnurlcharge.amount = int(amount)
    await update_lnurlcharge(lnurlcharge)
    return lnurlcharge


@tpos_atm_router.post("/withdraw/{charge_id}/{amount}/pay", status_code=HTTPStatus.OK)
async def api_tpos_atm_pay(
    request: Request, charge_id: str, amount: int, data: CreateWithdrawPay
) -> SimpleStatus:
    try:
        res = await lnurl_handle(data.pay_link, user_agent="lnbits/tpos")
        if not isinstance(res, LnurlPayResponse):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Excepted LNURL pay reponse.",
            )
        if isinstance(res, LnurlErrorResponse):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Error processing lnurl pay link: {res.reason}",
            )
        res2 = await execute_pay_request(
            res, msat=amount * 1000, user_agent="lnbits/tpos"
        )
        if isinstance(res2, LnurlErrorResponse):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Error processing pay request: {res2.reason}",
            )
        callback_url = str(request.url_for("tpos.tposlnurlcharge.callback"))
        withdraw_res = LnurlWithdrawResponse(
            k1=charge_id,
            callback=parse_obj_as(CallbackUrl, callback_url),
            maxWithdrawable=MilliSatoshi(amount * 1000),
            minWithdrawable=MilliSatoshi(amount * 1000),
        )
        try:
            res3 = await execute_withdraw(withdraw_res, res2.pr, user_agent="lnbits/tpos")
            if isinstance(res3, LnurlErrorResponse):
                raise HTTPException(
                    status_code=HTTPStatus.BAD_REQUEST,
                    detail=f"Error processing withdraw: {res3.reason}",
                )
        except Exception as exc:
            logger.error(f"Error processing withdraw: {exc}")

        return SimpleStatus(success=True, message="Withdraw processed successfully.")

    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Cannot process atm withdraw",
        ) from exc
