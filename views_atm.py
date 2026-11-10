from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Path
from lnbits.core.crud import (
    get_account,
    get_wallet,
)
from lnbits.core.models import User
from lnbits.core.models.misc import SimpleStatus
from lnbits.core.services import get_pr_from_lnurl
from lnbits.decorators import check_user_exists
from lnbits.utils.cache import cache
from lnurl import LnurlErrorResponse
from loguru import logger

from .crud import (
    create_lnurlcharge,
    get_lnurlcharge,
    get_tpos,
    update_lnurlcharge,
)
from .models import (
    AuthorizeAtm,
    CreateWithdrawPay,
    LnurlCharge,
)
from .views_lnurl import lnurl_callback

tpos_atm_router = APIRouter(prefix="/api/v1/atm", tags=["TPoS ATM"])

ATM_AUTH_CACHE_PREFIX = "tpos:atm:auth:"
ATM_AUTH_WINDOW = 15 * 60
ATM_AUTH_COOLDOWN = 60
ATM_AUTH_MAX_FAILURES = 5


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
            HTTPStatus.BAD_REQUEST,
            "Withdrawals are not allowed at this time. Try again later.",
        )
    return await create_lnurlcharge(tpos.id)


@tpos_atm_router.post("/{tpos_id}/authorize")
async def api_tpos_atm_authorize(tpos_id: str, data: AuthorizeAtm) -> LnurlCharge:
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(HTTPStatus.NOT_FOUND, "TPoS does not exist.")

    wallet = await get_wallet(tpos.wallet)
    account = await get_account(wallet.user) if wallet else None
    key = f"{ATM_AUTH_CACHE_PREFIX}{tpos_id}"
    failures = cache.get(key, 0) or 0
    if failures >= ATM_AUTH_MAX_FAILURES:
        raise HTTPException(
            HTTPStatus.TOO_MANY_REQUESTS,
            "Too many failed attempts. Try again later.",
        )
    if not account or not account.verify_password(data.password):
        failures += 1
        cache.set(
            key,
            failures,
            expiry=(
                ATM_AUTH_COOLDOWN
                if failures >= ATM_AUTH_MAX_FAILURES
                else ATM_AUTH_WINDOW
            ),
        )
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "Invalid credentials.")

    cache.pop(key)
    if not tpos.can_withdraw:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            "Withdrawals are not allowed at this time. Try again later.",
        )
    return await create_lnurlcharge(tpos.id)


@tpos_atm_router.get("/withdraw/{charge_id}/{amount}")
async def api_tpos_create_withdraw(
    charge_id: str,
    amount: int = Path(..., gt=0),
) -> LnurlCharge:
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
    if not tpos.can_withdraw or amount > tpos.withdraw_maximum:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Withdrawal is not allowed.")

    wallet = await get_wallet(tpos.wallet)
    if not wallet:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail=f"Wallet {tpos.wallet} does not exist.",
        )

    balance = int(wallet.balance_msat / 1000)
    if balance < amount:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Insufficient balance. Your balance is {balance} sats",
        )
    lnurlcharge.amount = amount
    await update_lnurlcharge(lnurlcharge)
    return lnurlcharge


@tpos_atm_router.post("/withdraw/{charge_id}/{amount}/pay", status_code=HTTPStatus.OK)
async def api_tpos_atm_pay(
    charge_id: str,
    data: CreateWithdrawPay,
    amount: int = Path(..., gt=0),
) -> SimpleStatus:
    charge = await get_lnurlcharge(charge_id)
    if not charge or charge.claimed or charge.amount != amount:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid ATM charge.")
    try:
        pr = await get_pr_from_lnurl(data.pay_link, amount * 1000)
        result = await lnurl_callback(pr=pr, k1=charge_id)
        if isinstance(result, LnurlErrorResponse):
            raise ValueError("ATM payment was not completed.")
        return SimpleStatus(success=True, message="Withdraw processed successfully.")

    except Exception as exc:
        logger.warning("tpos: ATM payment failed: {}", exc)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Cannot process atm withdraw",
        ) from exc
