import json
from http import HTTPStatus

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from lnbits.core.crud import (
    get_latest_payments_by_extension,
    get_standalone_payment,
    get_user,
    get_wallet,
)
from lnbits.core.models import WalletTypeInfo
from lnbits.core.services import create_invoice
from lnbits.decorators import (
    require_admin_key,
    require_invoice_key,
)
from lnurl import decode as decode_lnurl

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
from .helpers import get_pr
from .models import (
    CreateTposData,
    CreateTposInvoice,
    CreateUpdateItemData,
    CreateWithdrawPay,
    LnurlCharge,
    PayLnurlWData,
    Tpos,
)

tpos_api_router = APIRouter()


@tpos_api_router.get("/api/v1/tposs", status_code=HTTPStatus.OK)
async def api_tposs(
    all_wallets: bool = Query(False),
    key_info: WalletTypeInfo = Depends(require_invoice_key),
) -> list[Tpos]:
    wallet_ids = [key_info.wallet.id]
    if all_wallets:
        user = await get_user(key_info.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return await get_tposs(wallet_ids)


@tpos_api_router.post("/api/v1/tposs", status_code=HTTPStatus.CREATED)
async def api_tpos_create(
    data: CreateTposData, key_type: WalletTypeInfo = Depends(require_admin_key)
):
    data.wallet = key_type.wallet.id
    tpos = await create_tpos(data)
    return tpos


@tpos_api_router.put("/api/v1/tposs/{tpos_id}")
async def api_tpos_update(
    data: CreateTposData,
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
    for field, value in data.dict().items():
        setattr(tpos, field, value)
    tpos = await update_tpos(tpos)
    return tpos


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
async def api_tpos_create_invoice(tpos_id: str, data: CreateTposInvoice) -> dict:
    tpos = await get_tpos(tpos_id)

    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    if not data.details:
        tax_value = 0.0
        if tpos.tax_default:
            tax_value = (
                (data.amount / data.exchange_rate) * (tpos.tax_default * 0.01)
                if data.exchange_rate
                else 0.0
            )
        data.details = {
            "currency": tpos.currency,
            "exchangeRate": data.exchange_rate,
            "items": None,
            "taxIncluded": True,
            "taxValue": tax_value,
        }

    try:
        payment = await create_invoice(
            wallet_id=tpos.wallet,
            amount=data.amount + (data.tip_amount or 0),
            memo=f"{data.memo} to {tpos.name}" if data.memo else f"{tpos.name}",
            extra={
                "tag": "tpos",
                "tip_amount": data.tip_amount,
                "tpos_id": tpos_id,
                "amount": data.amount,
                "exchangeRate": data.exchange_rate if data.exchange_rate else None,
                "details": data.details if data.details else None,
                "lnaddress": data.user_lnaddress if data.user_lnaddress else None,
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    return {"payment_hash": payment.payment_hash, "payment_request": payment.bolt11}


@tpos_api_router.get("/api/v1/tposs/{tpos_id}/invoices")
async def api_tpos_get_latest_invoices(tpos_id: str):
    payments = await get_latest_payments_by_extension(ext_name="tpos", ext_id=tpos_id)

    details = payments[0].extra.get("details", None)
    exchange_rate = None
    currency = None
    if details:
        exchange_rate = details.get("exchange_rate", None)
        currency = details.get("currency", None)
    return [
        {
            "checking_id": payment.checking_id,
            "amount": payment.amount,
            "time": payment.time,
            "pending": payment.pending,
            "currency": currency,
            "exchange_rate": exchange_rate,
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
    "/api/v1/tposs/{tpos_id}/invoices/{payment_hash}", status_code=HTTPStatus.OK
)
async def api_tpos_check_invoice(
    tpos_id: str, payment_hash: str, extra: bool = Query(False)
):
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    payment = await get_standalone_payment(payment_hash, incoming=True)
    if not payment:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Payment does not exist."
        )
    if payment.extra.get("tag") != "tpos":
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS payment does not exist."
        )

    if extra:
        return {
            "paid": payment.success,
            "extra": payment.extra,
            "created_at": payment.created_at,
            "business_name": tpos.business_name,
            "business_address": tpos.business_address,
            "business_vat_id": tpos.business_vat_id,
        }
    return {"paid": payment.success}


@tpos_api_router.get("/api/v1/atm/{tpos_id}/{atmpin}", status_code=HTTPStatus.CREATED)
async def api_tpos_atm_pin_check(tpos_id: str, atmpin: int) -> LnurlCharge:
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(HTTPStatus.NOT_FOUND, "TPoS does not exist.")
    if int(tpos.withdraw_pin or 0) != int(atmpin):
        raise HTTPException(HTTPStatus.NOT_FOUND, "Wrong PIN.")
    token = await start_lnurlcharge(tpos)
    return token


@tpos_api_router.post(
    "/api/v1/atm/withdraw/{k1}/{amount}/pay", status_code=HTTPStatus.OK
)
async def api_tpos_atm_pay(
    request: Request, k1: str, amount: int, data: CreateWithdrawPay
):
    try:
        # get the payment_request from the lnurl
        pay_link = data.pay_link.replace("lnurlp://", "https://")
        async with httpx.AsyncClient() as client:
            headers = {"user-agent": "lnbits/tpos"}
            r = await client.get(pay_link, follow_redirects=True, headers=headers)
            if r.is_error:
                return {"success": False, "detail": "Error loading"}
            resp = r.json()

            amount = amount * 1000  # convert to msats

            if resp["tag"] != "payRequest":
                return {"success": False, "detail": "Wrong tag type"}

            if amount < int(resp["minSendable"]):
                return {"success": False, "detail": "Amount too low"}

            if amount > int(resp["maxSendable"]):
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
        LnurlCharge(
            id=withdraw_token,
            tpos_id=lnurlcharge.tpos_id,
            amount=int(amount),
            claimed=False,
        )
    )
    return {**lnurlcharge.dict(), **{"lnurl": lnurlcharge.lnurl(request)}}


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

    tpos.items = json.dumps(data.dict()["items"])
    tpos = await update_tpos(tpos)
    return tpos


@tpos_api_router.get("/api/v1/tposs/lnaddresscheck", status_code=HTTPStatus.OK)
async def api_tpos_check_lnaddress(lnaddress: str):
    check = await get_pr(lnaddress, 1)
    if not check:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    return True
