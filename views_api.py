from http import HTTPStatus
import json

import httpx
from fastapi import Depends, Query, Request
from lnurl import decode as decode_lnurl
from loguru import logger
from starlette.exceptions import HTTPException

from lnbits.core.crud import get_latest_payments_by_extension, get_user
from lnbits.core.models import Payment
from lnbits.core.services import create_invoice
from lnbits.core.views.api import api_payment
from lnbits.decorators import (
    WalletTypeInfo,
    get_key_type,
    require_admin_key,
)
from lnbits.utils.exchange_rates import get_fiat_rate_satoshis

from . import tpos_ext
from .crud import (
    create_tpos,
    update_tpos,
    delete_tpos,
    get_tpos,
    get_tposs,
    start_lnurlcharge,
    get_lnurlcharge,
    update_lnurlcharge,
)
from .models import CreateTposData, PayLnurlWData, LNURLCharge, CreateUpdateItemData


@tpos_ext.get("/api/v1/tposs", status_code=HTTPStatus.OK)
async def api_tposs(
    all_wallets: bool = Query(False), wallet: WalletTypeInfo = Depends(get_key_type)
):
    wallet_ids = [wallet.wallet.id]
    if all_wallets:
        user = await get_user(wallet.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return [tpos.dict() for tpos in await get_tposs(wallet_ids)]


@tpos_ext.post("/api/v1/tposs", status_code=HTTPStatus.CREATED)
async def api_tpos_create(
    data: CreateTposData, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    tpos = await create_tpos(wallet_id=wallet.wallet.id, data=data)
    return tpos.dict()


@tpos_ext.put("/api/v1/tposs/{tpos_id}")
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


@tpos_ext.delete("/api/v1/tposs/{tpos_id}")
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


@tpos_ext.post("/api/v1/tposs/{tpos_id}/invoices", status_code=HTTPStatus.CREATED)
async def api_tpos_create_invoice(
    tpos_id: str,
    amount: int = Query(..., ge=1),
    memo: str = "",
    tipAmount: int = 0,
    details: str = Query(None),
) -> dict:
    tpos = await get_tpos(tpos_id)

    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    if tipAmount > 0:
        amount += tipAmount

    try:
        payment_hash, payment_request = await create_invoice(
            wallet_id=tpos.wallet,
            amount=amount,
            memo=f"{memo} to {tpos.name}" if memo else f"{tpos.name}",
            extra={
                "tag": "tpos",
                "tipAmount": tipAmount,
                "tposId": tpos_id,
                "amount": amount - tipAmount if tipAmount else False,
                "details": details if details else None,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e))

    return {"payment_hash": payment_hash, "payment_request": payment_request}


@tpos_ext.get("/api/v1/tposs/{tpos_id}/invoices")
async def api_tpos_get_latest_invoices(tpos_id: str):
    try:
        payments = [
            Payment.from_row(row)
            for row in await get_latest_payments_by_extension(
                ext_name="tpos", ext_id=tpos_id
            )
        ]

    except Exception as e:
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(e))

    return [
        {
            "checking_id": payment.checking_id,
            "amount": payment.amount,
            "time": payment.time,
            "pending": payment.pending,
        }
        for payment in payments
    ]


@tpos_ext.post(
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
            headers = {"user-agent": f"lnbits/tpos"}
            r = await client.get(lnurl, follow_redirects=True, headers=headers)
            if r.is_error:
                lnurl_response = {"success": False, "detail": "Error loading"}
            else:
                resp = r.json()
                if resp["tag"] != "withdrawRequest":
                    lnurl_response = {"success": False, "detail": "Wrong tag type"}
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


@tpos_ext.get(
    "/api/v1/tposs/{tpos_id}/invoices/{payment_hash}", status_code=HTTPStatus.OK
)
async def api_tpos_check_invoice(tpos_id: str, payment_hash: str):
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    try:
        status = await api_payment(payment_hash)

    except Exception as exc:
        logger.error(exc)
        return {"paid": False}
    return status


@tpos_ext.get("/api/v1/atm/{tpos_id}/{atmpin}", status_code=HTTPStatus.CREATED)
async def api_tpos_atm_pin_check(tpos_id: str, atmpin: int):
    tpos = await get_tpos(tpos_id)
    logger.debug(tpos)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    if int(tpos.withdrawpin) != int(atmpin):
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Wrong PIN.")
    token = await start_lnurlcharge(tpos_id)
    return token


@tpos_ext.get("/api/v1/atm/withdraw/{k1}/{amount}/pay", status_code=HTTPStatus.OK)
async def api_tpos_atm_pay(
    request: Request, k1: str, amount: int, payLink: str = Query(...)
):
    try:
        # get the payment_request from the lnurl
        payLink = payLink.replace("lnurlp://", "https://")
        logger.debug(payLink)
        async with httpx.AsyncClient() as client:
            headers = {"user-agent": f"lnbits/tpos"}
            r = await client.get(payLink, follow_redirects=True, headers=headers)
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

    except AssertionError as ex:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=str(ex),
        )
    except Exception as ex:
        logger.warning(ex)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Cannot process atm withdraw",
        )


@tpos_ext.get(
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
    lnurlcharge = await update_lnurlcharge(
        LNURLCharge(id=withdraw_token, tpos_id=lnurlcharge.tpos_id, amount=int(amount))
    )
    return {**lnurlcharge.dict(), **{"lnurl": lnurlcharge.lnurl(request)}}


@tpos_ext.get("/api/v1/rate/{currency}", status_code=HTTPStatus.OK)
async def api_check_fiat_rate(currency):
    try:
        rate = await get_fiat_rate_satoshis(currency)
    except AssertionError:
        rate = None

    return {"rate": rate}


## ITEMS
@tpos_ext.put("/api/v1/tposs/{tpos_id}/items", status_code=HTTPStatus.CREATED)
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
