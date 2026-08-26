import json
from http import HTTPStatus
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from lnbits.core.crud import get_account, get_standalone_payment, get_wallet
from lnbits.core.crud.payments import update_payment, update_payment_checking_id
from lnbits.core.crud.users import update_account
from lnbits.core.models import CreateInvoice, Payment, PaymentState
from lnbits.core.models.users import UserLabel
from lnbits.core.services import create_payment_request, websocket_updater
from lnbits.tasks import internal_invoice_queue_put
from lnurl import decode as decode_lnurl

from .crud import (
    create_tpos_payment,
    get_latest_tpos_payments,
    get_tpos,
    get_tpos_payment_by_hash,
)
from .helpers import (
    INTERNAL_FIAT_LABEL_COLORS,
    INTERNAL_FIAT_METHODS,
    inventory_tags_to_list,
)
from .models import (
    CreateTposInvoice,
    InventorySale,
    PayLnurlWData,
    PrintReceiptRequest,
    ReceiptData,
    ReceiptDetailsData,
    ReceiptExtraData,
    ReceiptItemData,
    ReceiptPrint,
    TapToPay,
    Tpos,
    TposInvoiceResponse,
    TposPayment,
)
from .services import ensure_tpos_tabs_access
from .services_onchain import fetch_onchain_address
from .services_tabs import get_tab_for_tpos, tab_settlement_tolerance
from .views_onchain import _validate_watchonly_settings

tpos_payments_router = APIRouter()


@tpos_payments_router.post(
    "/api/v1/tposs/{tpos_id}/invoices", status_code=HTTPStatus.CREATED
)
async def api_tpos_create_invoice(
    tpos_id: str, data: CreateTposInvoice, request: Request
) -> dict[str, Any]:
    tpos = await get_tpos(tpos_id)

    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    inventory_payload: InventorySale | None = data.inventory
    if inventory_payload:
        if not tpos.use_inventory or not tpos.inventory_id:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Inventory is not enabled for this TPoS.",
            )
        inventory_payload.tags = inventory_tags_to_list(inventory_payload.tags)
        if tpos.inventory_id and inventory_payload.inventory_id != tpos.inventory_id:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Mismatched inventory selection.",
            )
        allowed_tags = set(inventory_tags_to_list(tpos.inventory_tags))
        if allowed_tags and any(
            tag not in allowed_tags for tag in inventory_payload.tags
        ):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Provided tags are not allowed for this TPoS.",
            )

    if not data.details:
        tax_value = 0.0
        if tpos.tax_default and data.exchange_rate:
            gross_amount = data.amount / data.exchange_rate
            tax_rate = tpos.tax_default * 0.01
            tax_value = (gross_amount * tax_rate) / (1 + tax_rate)
        data.details = {
            "currency": tpos.currency,
            "exchangeRate": data.exchange_rate,
            "items": None,
            "taxIncluded": True,
            "taxValue": tax_value,
        }

    internal_fiat_method = (
        data.fiat_method
        if data.pay_in_fiat and data.fiat_method in INTERNAL_FIAT_METHODS
        else None
    )
    cash_method = internal_fiat_method is not None
    onchain_method = data.payment_method == "btc_onchain"
    if cash_method and not tpos.allow_cash_settlement:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="Cash settlement is not enabled for this TPoS.",
        )
    if onchain_method and not tpos.onchain_enabled:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="Onchain payments are not enabled for this TPoS.",
        )
    tab_settlement = data.tab_settlement
    if tab_settlement:
        user_id = await ensure_tpos_tabs_access(tpos)
        tab = await get_tab_for_tpos(user_id, tpos, tab_settlement.tab_id)
        if tab.get("status") == "closed":
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Closed tabs cannot be settled.",
            )
        tab_balance = float(tab.get("balance") or 0)
        if tab_balance <= 0:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="This tab has no outstanding balance to settle.",
            )
        amount_over_balance = tab_settlement.amount - tab_balance
        if amount_over_balance > tab_settlement_tolerance(tab.get("currency")):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Settlement amount cannot exceed the outstanding balance.",
            )
        if amount_over_balance > 0:
            tab_settlement.amount = tab_balance
    currency = tpos.currency if data.pay_in_fiat else "sat"
    amount = data.amount + (data.tip_amount or 0.0)
    if data.pay_in_fiat:
        amount = (data.amount_fiat or 0.0) + (data.tip_amount_fiat or 0.0)

    try:
        extra = {
            "tag": "tpos",
            "tip_amount": data.tip_amount,
            "tpos_id": tpos_id,
            "amount": data.amount,
            "exchangeRate": data.exchange_rate if data.exchange_rate else None,
            "details": data.details if data.details else None,
            "notes": data.notes if data.notes else None,
            "lnaddress": data.user_lnaddress if data.user_lnaddress else None,
            "internal_memo": data.internal_memo if data.internal_memo else None,
            "paid_in_fiat": data.pay_in_fiat,
            "base_url": str(request.base_url),
        }
        if tab_settlement:
            extra["tab_settlement"] = tab_settlement.dict()
        if cash_method or onchain_method:
            wallet = await get_wallet(tpos.wallet)
            if wallet:
                account = await get_account(wallet.user)
                if account:
                    if not account.is_super_user:
                        raise HTTPException(
                            status_code=HTTPStatus.BAD_REQUEST,
                            detail="This tpos cannot create cash or onchain invoices.",
                        )
                    existing = {label.name for label in account.extra.labels or []}
                    label_name = internal_fiat_method if cash_method else "onchain"
                    label_description = (
                        f"{label_name.capitalize()} payment"
                        if cash_method
                        else "Onchain payment"
                    )
                    label_color = (
                        INTERNAL_FIAT_LABEL_COLORS.get(label_name, "#FFC107")
                        if cash_method
                        else "#ED8403"
                    )
                    if label_name not in existing:
                        account.extra.labels.append(
                            UserLabel(
                                name=label_name,
                                description=label_description,
                                color=label_color,
                            )
                        )
                        await update_account(account)
        if inventory_payload:
            extra["inventory"] = inventory_payload.dict()
        if data.pay_in_fiat:
            extra["fiat_method"] = data.fiat_method if data.fiat_method else "checkout"
            if data.fiat_method == "terminal" and tpos.stripe_reader_id:
                extra["terminal"] = {"reader_id": tpos.stripe_reader_id}
        if onchain_method:
            extra["payment_method"] = "onchain"
        invoice_data = CreateInvoice(
            unit=currency,
            out=False,
            amount=amount,
            memo=f"{data.memo} to {tpos.name}" if data.memo else f"{tpos.name}",
            extra=extra,
            fiat_provider=(
                tpos.fiat_provider if data.pay_in_fiat and not cash_method else None
            ),
            internal=bool(cash_method or onchain_method),
            labels=(
                [internal_fiat_method]
                if internal_fiat_method
                else (["onchain"] if onchain_method else [])
            ),
        )
        payment = await create_payment_request(tpos.wallet, invoice_data)
        if cash_method:
            new_checking_id = f"internal_{internal_fiat_method}_{payment.payment_hash}"
            await update_payment_checking_id(payment.checking_id, new_checking_id)
            payment.checking_id = new_checking_id
        elif onchain_method:
            new_checking_id = f"internal_onchain_{payment.payment_hash}"
            await update_payment_checking_id(payment.checking_id, new_checking_id)
            payment.checking_id = new_checking_id

        onchain_address = None
        mempool_endpoint = None
        if onchain_method:
            wallet_record = await get_wallet(tpos.wallet)
            if not wallet_record:
                raise HTTPException(
                    status_code=HTTPStatus.BAD_REQUEST,
                    detail="Wallet not found for this TPoS.",
                )
            validation = await _validate_watchonly_settings(
                wallet=wallet_record,
                onchain_enabled=tpos.onchain_enabled,
                onchain_wallet_id=tpos.onchain_wallet_id,
            )
            assert validation
            address_data = await fetch_onchain_address(
                wallet_record.inkey, tpos.onchain_wallet_id or ""
            )
            onchain_address = address_data.get("address")
            mempool_endpoint = validation.get("mempool_endpoint")

        tpos_payment = await create_tpos_payment(
            TposPayment(
                id=uuid4().hex,
                tpos_id=tpos_id,
                payment_hash=payment.payment_hash,
                amount=int(data.amount + (data.tip_amount or 0)),
                onchain_address=onchain_address,
                onchain_wallet_id=tpos.onchain_wallet_id,
                onchain_zero_conf=tpos.onchain_zero_conf,
                mempool_endpoint=mempool_endpoint,
            )
        )
        response_payload = _serialize_tpos_invoice_response(payment, tpos_payment)

        if tpos.enable_remote:
            payload = {
                "type": "invoice_created",
                "tpos_id": tpos_id,
                "payment_hash": payment.payment_hash,
                "payment_request": response_payload.payment_request,
                "paid_in_fiat": data.pay_in_fiat,
                "amount_fiat": data.amount_fiat,
                "tip_amount": data.tip_amount,
                "tip_amount_fiat": data.tip_amount_fiat,
                "exchange_rate": data.exchange_rate if data.exchange_rate else None,
                "tpos_payment_id": response_payload.tpos_payment_id,
                "payment_options": response_payload.payment_options,
                "onchain_address": response_payload.onchain_address,
                "onchain_amount_sat": response_payload.onchain_amount_sat,
                "payment_method": response_payload.payment_method,
            }
            await websocket_updater(tpos_id, json.dumps(payload))

        if (invoice_data.extra or {}).get("fiat_method") == "terminal":
            pi_id = payment.extra.get("fiat_checking_id")
            client_secret = payment.extra.get("fiat_payment_request")
            if pi_id and client_secret:
                amount_minor = round(amount * 100)
                tap_to_pay_payload = TapToPay(
                    payment_intent_id=pi_id,
                    client_secret=client_secret,
                    currency=invoice_data.unit.lower(),
                    amount=amount_minor,
                    tpos_id=tpos_id,
                    payment_hash=payment.payment_hash,
                )
                await websocket_updater(tpos_id, json.dumps(tap_to_pay_payload.dict()))
        return response_payload.dict()

    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


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


async def _validate_internal_fiat_invoice(
    tpos_id: str, payment_hash: str, fiat_method: str
):
    """Mark an internally settled fiat invoice as received.

    Shared by the cash and custom validation routes. The cashier confirms
    manually that the amount was received through that channel.
    """
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
    if payment.extra.get("fiat_method") != fiat_method:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Payment is not {fiat_method}.",
        )
    if not payment.is_internal:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Payment is not an internal {fiat_method} invoice.",
        )
    if not payment.success:
        payment.status = PaymentState.SUCCESS
        await update_payment(payment)
    await internal_invoice_queue_put(payment.checking_id)
    return {"success": True}


@tpos_payments_router.post(
    "/api/v1/tposs/{tpos_id}/invoices/{payment_hash}/cash/validate",
    status_code=HTTPStatus.OK,
)
async def api_tpos_validate_cash_invoice(tpos_id: str, payment_hash: str):
    return await _validate_internal_fiat_invoice(tpos_id, payment_hash, "cash")


@tpos_payments_router.post(
    "/api/v1/tposs/{tpos_id}/invoices/{payment_hash}/custom/validate",
    status_code=HTTPStatus.OK,
)
async def api_tpos_validate_custom_invoice(tpos_id: str, payment_hash: str):
    return await _validate_internal_fiat_invoice(tpos_id, payment_hash, "custom")


def _payment_method_from_payment(payment: Payment) -> str:
    if payment.extra.get("payment_method"):
        return str(payment.extra["payment_method"])
    if payment.extra.get("fiat_method") in INTERNAL_FIAT_METHODS:
        return str(payment.extra["fiat_method"])
    if payment.extra.get("fiat_payment_request", "").startswith("pi_"):
        return "fiat"
    return "lightning"


def _serialize_tpos_invoice_response(
    payment: Payment, tpos_payment: TposPayment
) -> TposInvoiceResponse:
    payment_method = _payment_method_from_payment(payment)
    payment_request = "lightning:" + payment.bolt11.upper()
    if payment_method in INTERNAL_FIAT_METHODS:
        payment_request = payment_method
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
