import json
from http import HTTPStatus
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from lnbits.core.crud import get_standalone_payment
from lnbits.core.models import Payment
from lnbits.core.services import websocket_updater
from lnbits.tasks import internal_invoice_queue_put
from lnurl import decode as decode_lnurl

from .crud import (
    get_latest_tpos_payments,
    get_tpos,
    get_tpos_payment_by_hash,
)
from .models import (
    PayLnurlWData,
    PrintReceiptRequest,
    ReceiptData,
    ReceiptDetailsData,
    ReceiptExtraData,
    ReceiptItemData,
    ReceiptPrint,
    Tpos,
    TposInvoiceResponse,
    TposPayment,
)

tpos_payments_router = APIRouter()


@tpos_payments_router.get("/api/v1/tposs/{tpos_id}/invoices")
async def api_tpos_get_latest_invoices(tpos_id: str):
    tpos_payments = await get_latest_tpos_payments(tpos_id)
    result = []
    for tpos_payment in tpos_payments:
        payment = await get_standalone_payment(tpos_payment.payment_hash, incoming=True)
        if not payment:
            continue
        details = payment.extra.get("details", {})
        currency = details.get("currency", None)
        exchange_rate = details.get("exchangeRate") or payment.extra.get("exchangeRate")
        result.append(
            {
                "checking_id": payment.checking_id,
                "amount": payment.amount,
                "time": payment.time,
                "pending": not tpos_payment.paid,
                "currency": currency,
                "exchange_rate": exchange_rate,
                "payment_method": tpos_payment.payment_method,
            }
        )
    return result


@tpos_payments_router.post(
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
                if resp.get("status") == "ERROR":
                    lnurl_response = {
                        "success": False,
                        "detail": resp.get("reason", ""),
                    }
                    return lnurl_response

                if resp.get("tag") != "withdrawRequest":
                    lnurl_response = {"success": False, "detail": "Wrong tag type"}
                else:
                    r2 = await client.get(
                        resp.get("callback", ""),
                        follow_redirects=True,
                        headers=headers,
                        params={
                            "k1": resp.get("k1", ""),
                            "pr": payment_request,
                        },
                    )
                    resp2 = r2.json()
                    if r2.is_error:
                        lnurl_response = {
                            "success": False,
                            "detail": "Error loading callback",
                        }
                    elif resp2.get("status") == "ERROR":
                        lnurl_response = {"success": False, "detail": resp2["reason"]}
                    else:
                        lnurl_response = {"success": True, "detail": resp2}
        except (httpx.ConnectError, httpx.RequestError):
            lnurl_response = {"success": False, "detail": "Unexpected error occurred"}

    return lnurl_response


@tpos_payments_router.get(
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
    tpos_payment = await get_tpos_payment_by_hash(payment_hash)

    if extra:
        return _build_receipt_data(tpos, payment, tpos_payment).to_api_dict()
    return {"paid": payment.success or bool(tpos_payment and tpos_payment.paid)}


@tpos_payments_router.post(
    "/api/v1/tposs/{tpos_id}/invoices/{payment_hash}/print",
    status_code=HTTPStatus.OK,
)
async def api_tpos_print_invoice(
    data: PrintReceiptRequest, tpos_id: str, payment_hash: str
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
    if payment.extra.get("tag") != "tpos" or payment.extra.get("tpos_id") != tpos_id:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS payment does not exist."
        )

    receipt_type: Literal["receipt", "order_receipt"] = (
        "order_receipt" if data.receipt_type == "order_receipt" else "receipt"
    )
    tpos_payment = await get_tpos_payment_by_hash(payment_hash)
    receipt = _build_receipt_data(tpos, payment, tpos_payment)
    payload = ReceiptPrint(
        tpos_id=tpos_id,
        payment_hash=payment_hash,
        receipt_type=receipt_type,
        print_text=receipt.render_text(receipt_type),
        receipt=receipt.to_api_dict(),
    )
    await websocket_updater(tpos_id, json.dumps(payload.dict()))
    return {"success": True}


@tpos_payments_router.post(
    "/api/v1/tposs/{tpos_id}/invoices/{payment_hash}/cash/validate",
    status_code=HTTPStatus.OK,
)
async def api_tpos_validate_cash_invoice(tpos_id: str, payment_hash: str):
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    if not tpos.allow_cash_settlement:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="Cash settlement is not enabled for this TPoS.",
        )
    payment = await get_standalone_payment(payment_hash, incoming=True)
    if not payment:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Payment does not exist."
        )
    if payment.extra.get("tag") != "tpos" or payment.extra.get("tpos_id") != tpos_id:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS payment does not exist."
        )
    if payment.extra.get("fiat_method") != "cash":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Payment is not cash."
        )
    if not payment.is_internal:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Payment is not an internal cash invoice.",
        )
    if payment.success:
        return {"success": True}
    await internal_invoice_queue_put(payment.checking_id)
    return {"success": True}


def _payment_method_from_payment(payment: Payment) -> str:
    if payment.extra.get("payment_method"):
        return str(payment.extra["payment_method"])
    if payment.extra.get("fiat_method") == "cash":
        return "cash"
    if payment.extra.get("fiat_payment_request", "").startswith("pi_"):
        return "fiat"
    return "lightning"


def _serialize_tpos_invoice_response(
    payment: Payment, tpos_payment: TposPayment
) -> TposInvoiceResponse:
    payment_method = _payment_method_from_payment(payment)
    payment_request = "lightning:" + payment.bolt11.upper()
    if payment_method == "cash":
        payment_request = "cash"
    elif payment.extra.get("fiat_payment_request") and not payment.extra.get(
        "fiat_payment_request", ""
    ).startswith("pi_"):
        payment_request = payment.extra["fiat_payment_request"]
    elif payment_method == "fiat":
        payment_request = "tap_to_pay"
    elif payment_method == "onchain" and tpos_payment.onchain_address:
        payment_request = tpos_payment.onchain_address

    options = [payment_method]
    if tpos_payment.onchain_address:
        options = ["btc", "btc_onchain"]

    return TposInvoiceResponse(
        payment_hash=payment.payment_hash,
        bolt11=payment.bolt11,
        payment_request=payment_request,
        tpos_payment_id=tpos_payment.id,
        payment_options=options,
        onchain_address=tpos_payment.onchain_address,
        onchain_amount_sat=(
            tpos_payment.amount if tpos_payment.onchain_address else None
        ),
        payment_method=payment_method,
        extra=payment.extra or {},
    )


def _build_receipt_data(
    tpos: Tpos, payment: Payment, tpos_payment: TposPayment | None = None
) -> ReceiptData:
    extra = payment.extra or {}
    details = extra.get("details") or {}
    items = details.get("items") or []

    receipt_items = [
        ReceiptItemData(
            title=str(item.get("title") or ""),
            note=(str(item.get("note")) if item.get("note") is not None else None),
            quantity=int(item.get("quantity") or 0),
            price=float(item.get("price") or 0.0),
        )
        for item in items
    ]

    return ReceiptData(
        paid=payment.success or bool(tpos_payment and tpos_payment.paid),
        extra=ReceiptExtraData(
            amount=int(extra.get("amount") or 0),
            paid_in_fiat=bool(extra.get("paid_in_fiat")),
            fiat_method=extra.get("fiat_method"),
            fiat_payment_request=extra.get("fiat_payment_request"),
            details=ReceiptDetailsData(
                currency=str(details.get("currency") or "sats"),
                exchange_rate=float(details.get("exchangeRate") or 1.0),
                tax_value=float(details.get("taxValue") or 0.0),
                tax_included=bool(details.get("taxIncluded")),
                items=receipt_items,
            ),
        ),
        created_at=payment.created_at,
        business_name=tpos.business_name,
        business_address=tpos.business_address,
        business_vat_id=tpos.business_vat_id,
        only_show_sats_on_bitcoin=tpos.only_show_sats_on_bitcoin,
    )
