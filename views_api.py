import json
from http import HTTPStatus

import httpx
from fastapi import APIRouter, Depends, Query, Request
from lnbits import bolt11
from lnbits.core.crud import (
    get_latest_payments_by_extension,
    get_standalone_payment,
    get_user,
    get_wallet,
)
from lnbits.core.models import Payment, WalletTypeInfo
from lnbits.core.services import create_invoice
from lnbits.decorators import (
    get_key_type,
    require_admin_key,
)
from lnbits.utils.exchange_rates import get_fiat_rate_satoshis
from lnurl import decode as decode_lnurl
from loguru import logger
from starlette.exceptions import HTTPException

from .crud import (
    create_tpos,
    delete_tpos,
    get_lnurlcharge,
    get_tpos,
    get_tposs,
    start_lnurlcharge,
    update_lnurlcharge,
    update_tpos,
)
from .models import (
    CreateTposData,
    CreateUpdateItemData,
    LNURLCharge,
    PayLnurlWData,
)

tpos_api_router = APIRouter()


@tpos_api_router.get("/api/v1/tposs", status_code=HTTPStatus.OK)
async def api_tposs(
    all_wallets: bool = Query(False), wallet: WalletTypeInfo = Depends(get_key_type)
):
    wallet_ids = [wallet.wallet.id]
    if all_wallets:
        user = await get_user(wallet.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return [tpos.dict() for tpos in await get_tposs(wallet_ids)]


@tpos_api_router.post("/api/v1/tposs", status_code=HTTPStatus.CREATED)
async def api_tpos_create(
    data: CreateTposData, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    tpos = await create_tpos(wallet_id=wallet.wallet.id, data=data)
    return tpos.dict()


@tpos_api_router.put("/api/v1/tposs/{tpos_id}")
async def api_tpos_update(
    data: CreateTposData,
    tpos_id: str,
    wallet: WalletTypeInfo = Depends(require_admin_key),
):
    if not tpos_id:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    tpos = await get_tpos(tpos_id)
    assert tpos, "TPoS couldn't be retrieved"

    if wallet.wallet.id != tpos.wallet:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your TPoS.")
    tpos = await update_tpos(tpos_id=tpos_id, **data.dict(exclude_unset=True))
    return tpos.dict()


@tpos_api_router.delete("/api/v1/tposs/{tpos_id}")
async def api_tpos_delete(
    tpos_id: str, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    tpos = await get_tpos(tpos_id)

    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    if tpos.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your TPoS.")

    await delete_tpos(tpos_id)
    return "", HTTPStatus.NO_CONTENT


@tpos_api_router.post(
    "/api/v1/tposs/{tpos_id}/invoices", status_code=HTTPStatus.CREATED
)
async def api_tpos_create_invoice(
    tpos_id: str,
    amount: int = Query(..., ge=1),
    memo: str = "",
    tip_amount: int = 0,
    details: str = Query(None),
) -> dict:
    tpos = await get_tpos(tpos_id)

    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    if tip_amount > 0:
        amount += tip_amount

    try:
        payment_hash, payment_request = await create_invoice(
            wallet_id=tpos.wallet,
            amount=amount,
            memo=f"{memo} to {tpos.name}" if memo else f"{tpos.name}",
            extra={
                "tag": "tpos",
                "tipAmount": tip_amount,
                "tposId": tpos_id,
                "amount": amount - tip_amount if tip_amount else False,
                "details": details if details else None,
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    return {"payment_hash": payment_hash, "payment_request": payment_request}


@tpos_api_router.get("/api/v1/tposs/{tpos_id}/invoices")
async def api_tpos_get_latest_invoices(tpos_id: str):
    try:
        payments = [
            Payment.from_row(row)
            for row in await get_latest_payments_by_extension(
                ext_name="tpos", ext_id=tpos_id
            )
        ]

    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    return [
        {
            "checking_id": payment.checking_id,
            "amount": payment.amount,
            "time": payment.time,
            "pending": payment.pending,
        }
        for payment in payments
    ]


@tpos_api_router.post(
    "/api/v1/tposs/{tpos_id}/invoices/{payment_request}/pay", status_code=HTTPStatus.OK
)
async def api_tpos_pay_invoice(
    lnurl_data: PayLnurlWData, payment_request: str, tpos_id: str
):
    tpos = await get_tpos(tpos_id)

    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    lnurl = (
        lnurl_data.lnurl.replace("lnurlw://", "")
        .replace("lightning://", "")
        .replace("LIGHTNING://", "")
        .replace("lightning:", "")
        .replace("LIGHTNING:", "")
    )

    if lnurl.lower().startswith("lnurl"):
        lnurl = decode_lnurl(lnurl)
    else:
        lnurl = "https://" + lnurl

    async with httpx.AsyncClient() as client:
        try:
            headers = {"user-agent": "lnbits/tpos"}
            r = await client.get(lnurl, follow_redirects=True, headers=headers)
            if r.is_error:
                lnurl_response = {"success": False, "detail": "Error loading"}
            else:
                resp = r.json()
                if resp["tag"] != "withdrawRequest":
                    lnurl_response = {"success": False, "detail": "Wrong tag type"}

                elif resp["pinLimit"]:
                    invoice = bolt11.decode(payment_request)
                    if invoice.amount_msat >= resp["pinLimit"]:
                        return {
                            "success": True,
                            "detail": "PIN required for this amount",
                            "callback": resp["callback"],
                            "k1": resp["k1"],
                        }
                else:
                    r2 = await client.get(
                        resp["callback"],
                        follow_redirects=True,
                        headers=headers,
                        params={
                            "k1": resp["k1"],
                            "pr": payment_request,
                        },
                    )
                    resp2 = r2.json()
                    if r2.is_error:
                        lnurl_response = {
                            "success": False,
                            "detail": "Error loading callback",
                        }
                    elif resp2["status"] == "ERROR":
                        lnurl_response = {"success": False, "detail": resp2["reason"]}
                    else:
                        lnurl_response = {"success": True, "detail": resp2}
        except (httpx.ConnectError, httpx.RequestError):
            lnurl_response = {"success": False, "detail": "Unexpected error occurred"}

    return lnurl_response


@tpos_api_router.get(
    "/api/v1/tposs/{tpos_id}/invoices/{payment_request}/pay",
    status_code=HTTPStatus.OK,
)
async def api_tpos_pay_invoice_cb(
    payment_request: str,
    tpos_id: str,
    cb: str = Query(None),
):
    tpos = await get_tpos(tpos_id)

    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    async with httpx.AsyncClient() as client:
        try:
            headers = {"user-agent": "lnbits/tpos"}
            r = await client.get(
                cb,
                follow_redirects=True,
                headers=headers,
            )
            r_json = r.json()
            if r.is_error:
                lnurl_response = {
                    "success": False,
                    "detail": "Error loading callback",
                }
            elif r_json["status"] == "ERROR":
                lnurl_response = {"success": False, "detail": r_json["reason"]}
            else:
                lnurl_response = {"success": True, "detail": r_json}
        except (httpx.ConnectError, httpx.RequestError):
            lnurl_response = {"success": False, "detail": "Unexpected error occurred"}

    return lnurl_response


@tpos_api_router.get(
    "/api/v1/tposs/{tpos_id}/invoices/{payment_hash}", status_code=HTTPStatus.OK
)
async def api_tpos_check_invoice(tpos_id: str, payment_hash: str):
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    payment = await get_standalone_payment(payment_hash)
    if not payment:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Payment does not exist."
        )
    status = await payment.check_status()
    return {"paid": status.success}


@tpos_api_router.get("/api/v1/atm/{tpos_id}/{atmpin}", status_code=HTTPStatus.CREATED)
async def api_tpos_atm_pin_check(tpos_id: str, atmpin: int):
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    if int(tpos.withdrawpin or 0) != int(atmpin):
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Wrong PIN.")
    token = await start_lnurlcharge(tpos_id)
    return token


@tpos_api_router.get(
    "/api/v1/atm/withdraw/{k1}/{amount}/pay", status_code=HTTPStatus.OK
)
async def api_tpos_atm_pay(
    request: Request, k1: str, amount: int, pay_link: str = Query(...)
):
    try:
        # get the payment_request from the lnurl
        pay_link = pay_link.replace("lnurlp://", "https://")
        async with httpx.AsyncClient() as client:
            headers = {"user-agent": "lnbits/tpos"}
            r = await client.get(pay_link, follow_redirects=True, headers=headers)
            if r.is_error:
                return {"success": False, "detail": "Error loading"}
            resp = r.json()

            amount = amount * 1000  # convert to msats

            if resp["tag"] != "payRequest":
                return {"success": False, "detail": "Wrong tag type"}

            if amount < resp["minSendable"]:
                return {"success": False, "detail": "Amount too low"}

            if amount > resp["maxSendable"]:
                return {"success": False, "detail": "Amount too high"}

            cb_res = await client.get(
                resp["callback"],
                follow_redirects=True,
                headers=headers,
                params={"amount": amount},
            )
            cb_resp = cb_res.json()
            if cb_res.is_error:
                return {"success": False, "detail": "Error loading callback"}

            # pay the invoice
            lnurl_cb_url = str(request.url_for("tpos.tposlnurlcharge.callback"))
            pay_invoice = await client.get(
                lnurl_cb_url,
                params={"pr": cb_resp["pr"], "k1": k1},
            )
            if pay_invoice.status_code != 200:
                return {"success": False, "detail": "Error paying invoice"}
            return {"success": True, "detail": "Payment successful"}

    except AssertionError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.warning(exc)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Cannot process atm withdraw",
        ) from exc


@tpos_api_router.get(
    "/api/v1/atm/withdraw/{withdraw_token}/{amount}", status_code=HTTPStatus.CREATED
)
async def api_tpos_create_withdraw(
    request: Request, withdraw_token: str, amount: str
) -> dict:
    lnurlcharge = await get_lnurlcharge(withdraw_token)
    if not lnurlcharge:
        return {
            "status": "ERROR",
            "reason": f"lnurlcharge {withdraw_token} not found on this server",
        }
    tpos = await get_tpos(lnurlcharge.tpos_id)
    if not tpos:
        return {
            "status": "ERROR",
            "reason": f"TPoS {lnurlcharge.tpos_id} not found on this server",
        }

    wallet = await get_wallet(tpos.wallet)
    assert wallet
    balance = int(wallet.balance_msat / 1000)
    if balance < int(amount):
        return {
            "status": "ERROR",
            "reason": f"Insufficient balance. Your balance is {balance} sats",
        }

    lnurlcharge = await update_lnurlcharge(
        LNURLCharge(
            id=withdraw_token,
            tpos_id=lnurlcharge.tpos_id,
            amount=int(amount),
            claimed=False,
        )
    )
    return {**lnurlcharge.dict(), **{"lnurl": lnurlcharge.lnurl(request)}}


@tpos_api_router.get("/api/v1/rate/{currency}", status_code=HTTPStatus.OK)
async def api_check_fiat_rate(currency):
    try:
        rate = await get_fiat_rate_satoshis(currency)
    except AssertionError:
        rate = None

    return {"rate": rate}


@tpos_api_router.put("/api/v1/tposs/{tpos_id}/items", status_code=HTTPStatus.CREATED)
async def api_tpos_create_items(
    data: CreateUpdateItemData,
    tpos_id: str,
    wallet: WalletTypeInfo = Depends(require_admin_key),
):
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    if wallet.wallet.id != tpos.wallet:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your TPoS.")

    items = json.dumps(data.dict()["items"])
    tpos = await update_tpos(tpos_id=tpos_id, items=items)
    return tpos.dict()
