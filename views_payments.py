import json
from http import HTTPStatus
from time import time
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from httpx import HTTPError
from lnbits.core.crud import get_account, get_standalone_payment, get_wallet
from lnbits.core.crud.payments import update_payment, update_payment_checking_id
from lnbits.core.crud.users import update_account
from lnbits.core.models import CreateInvoice, Payment, PaymentState
from lnbits.core.models.users import UserLabel
from lnbits.core.services import (
    create_payment_request,
    perform_withdraw,
    websocket_updater,
)
from lnbits.settings import settings
from lnbits.tasks import internal_invoice_queue_put
from loguru import logger

from .crud import (
    create_onchain_payment,
    create_tpos_payment,
    get_latest_tpos_payments,
    get_tpos,
    get_tpos_payment_by_hash,
)
from .helpers import inventory_tags_to_list
from .models import (
    TERMINAL_PAYMENT_STATUSES,
    CreateTposInvoice,
    InventorySale,
    OnchainSale,
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
from .services import (
    ensure_tpos_tabs_access,
    is_tpos_payment,
    validate_tpos_sale_amount,
)
from .services_fiat import get_valid_tpos_fiat_wallet
from .services_inventory import validate_inventory_sale
from .services_onchain import fetch_onchain_address, validate_onchain_address
from .services_tabs import get_tab_for_tpos, tab_settlement_tolerance
from .tasks import tpos_invoice_queue
from .views_onchain import validate_onchain_settings

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

    if (
        tpos.currency.lower() != "sats"
        and data.amount_fiat is not None
        and not (data.tab_settlement or inventory_payload or data.pay_in_fiat)
    ):
        # Keypad sales: price the sats on the server, so a client that fell back
        # to a wrong exchange rate can't undercharge. Runs before the
        # default details, so their tax uses the repriced amount.
        await validate_tpos_sale_amount(tpos, data, data.amount_fiat)

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

    cash_method = data.pay_in_fiat and data.fiat_method == "cash"
    onchain_method = data.payment_method == "btc_onchain"
    if onchain_method and data.pay_in_fiat:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Choose either an onchain or a fiat payment.",
        )
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
    settlement_wallet = None
    if cash_method or (data.pay_in_fiat and tpos.fiat_provider):
        settlement_wallet = await get_valid_tpos_fiat_wallet(
            tpos, tpos.fiat_wallet_id, tpos.currency
        )
        if not settlement_wallet:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=f"Select an active fiat wallet in {tpos.currency}.",
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
        await validate_tpos_sale_amount(tpos, data, tab_settlement.amount)
    if inventory_payload:
        await validate_inventory_sale(tpos, data)
    currency = tpos.currency if data.pay_in_fiat else "sat"
    amount = data.amount + (data.tip_amount or 0.0)
    if data.pay_in_fiat:
        amount = (data.amount_fiat or 0.0) + (data.tip_amount_fiat or 0.0)

    try:
        extra: dict[str, Any] = {
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
            extra["tab_settlement"] = tab_settlement.model_dump()
        if (inventory_payload or tab_settlement) and data.amount_fiat is not None:
            extra["amount_fiat"] = data.amount_fiat
        if inventory_payload:
            extra["inventory"] = inventory_payload.model_dump()
        if onchain_method:
            response = await _create_onchain_checkout(tpos, data, extra)
            if tpos.enable_remote:
                try:
                    await _notify_checkout_created(tpos_id, data, response)
                except Exception:
                    logger.exception(
                        "tpos: failed to notify on-chain checkout for {}", tpos_id
                    )
            return response.model_dump()
        if data.pay_in_fiat:
            extra["fiat_method"] = data.fiat_method if data.fiat_method else "checkout"
            if data.fiat_method == "terminal" and tpos.stripe_reader_id:
                extra["terminal"] = {"reader_id": tpos.stripe_reader_id}
        if cash_method:
            wallet = await get_wallet(tpos.wallet)
            account = await get_account(wallet.user) if wallet else None
            if account and "cash" not in {
                label.name for label in account.extra.labels or []
            }:
                account.extra.labels.append(
                    UserLabel(name="cash", description="Cash payment", color="#FFC107")
                )
                await update_account(account)
        invoice_data = CreateInvoice(
            unit=currency,
            out=False,
            amount=amount,
            memo=f"{data.memo} to {tpos.name}" if data.memo else f"{tpos.name}",
            extra=extra,
            fiat_provider=(
                tpos.fiat_provider if data.pay_in_fiat and not cash_method else None
            ),
            internal=cash_method,
            labels=["cash"] if cash_method else [],
        )
        payment = await create_payment_request(
            settlement_wallet.id if settlement_wallet else tpos.wallet, invoice_data
        )
        if tab_settlement:
            payment.extra["tab_settlement"]["amount_msat"] = payment.amount
            # A provider may already have completed the payment; save only metadata.
            await update_payment(
                payment.copy(include={"checking_id", "wallet_id", "extra"})
            )
        if cash_method:
            new_checking_id = f"internal_cash_{payment.payment_hash}"
            await update_payment_checking_id(payment.checking_id, new_checking_id)
            payment.checking_id = new_checking_id

        tpos_payment = await create_tpos_payment(
            TposPayment(
                id=uuid4().hex,
                tpos_id=tpos_id,
                fiat_wallet_id=settlement_wallet.id if settlement_wallet else None,
                payment_hash=payment.payment_hash,
                amount=int(data.amount + (data.tip_amount or 0)),
            )
        )
        # Replay payments whose original event arrived before the tracking row.
        stored_payment = await get_standalone_payment(
            payment.payment_hash, incoming=True
        )
        if stored_payment and stored_payment.success:
            await tpos_invoice_queue.put(stored_payment)
        response_payload = _serialize_tpos_invoice_response(payment, tpos_payment)

        if tpos.enable_remote:
            await _notify_checkout_created(tpos_id, data, response_payload)

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
                await websocket_updater(
                    tpos_id, json.dumps(tap_to_pay_payload.model_dump())
                )
        return response_payload.model_dump()

    except HTTPException:
        raise
    except Exception as exc:
        if onchain_method:
            logger.exception("tpos: failed to create on-chain checkout for {}", tpos_id)
            raise HTTPException(
                HTTPStatus.INTERNAL_SERVER_ERROR, "Could not create on-chain checkout."
            ) from exc
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


@tpos_payments_router.get("/api/v1/tposs/{tpos_id}/invoices")
async def api_tpos_get_latest_invoices(tpos_id: str):
    tpos_payments = await get_latest_tpos_payments(tpos_id)
    result = []
    for tpos_payment in tpos_payments:
        sale = tpos_payment.onchain_sale
        payment = (
            None
            if sale
            else await get_standalone_payment(tpos_payment.payment_hash, incoming=True)
        )
        if not sale and not payment:
            continue
        extra = sale.extra if sale else payment.extra if payment else {}
        details = extra.get("details") or {}
        result.append(
            {
                "checking_id": (
                    payment.checking_id if payment else tpos_payment.payment_hash
                ),
                "amount": payment.amount if payment else tpos_payment.amount * 1000,
                "time": payment.time if payment else tpos_payment.created_at,
                "pending": not tpos_payment.paid,
                "currency": details.get("currency"),
                "exchange_rate": details.get("exchangeRate")
                or extra.get("exchangeRate"),
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

    lnurl = lnurl_data.lnurl
    if lnurl.lower().startswith("lightning:"):
        lnurl = lnurl.split(":", 1)[1].removeprefix("//")
    if "://" not in lnurl and not lnurl.lower().startswith("lnurl"):
        lnurl = "https://" + lnurl
    try:
        await perform_withdraw(lnurl, payment_request)
    except Exception:
        return {"success": False, "detail": "Cannot process LNURL withdrawal."}
    return {"success": True}


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
    tpos_payment = await get_tpos_payment_by_hash(payment_hash)
    if tpos_payment and tpos_payment.onchain_sale:
        if tpos_payment.tpos_id != tpos_id:
            raise HTTPException(HTTPStatus.NOT_FOUND, "TPoS payment does not exist.")
        if extra:
            return _build_receipt_data(tpos, None, tpos_payment).to_api_dict()
        return {"paid": tpos_payment.paid, "status": tpos_payment.status.value}
    payment = await get_standalone_payment(payment_hash, incoming=True)
    if not payment:
        if (
            tpos_payment
            and tpos_payment.tpos_id == tpos_id
            and tpos_payment.status in TERMINAL_PAYMENT_STATUSES
        ):
            return {"paid": False, "status": tpos_payment.status.value}
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Payment does not exist."
        )
    if not is_tpos_payment(payment, tpos, tpos_payment):
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS payment does not exist."
        )
    if extra:
        return _build_receipt_data(tpos, payment, tpos_payment).to_api_dict()
    response: dict[str, bool | str] = {
        "paid": payment.success or bool(tpos_payment and tpos_payment.paid),
    }
    if (
        tpos_payment
        and tpos_payment.onchain_address
        and tpos_payment.status in TERMINAL_PAYMENT_STATUSES
    ):
        response["status"] = tpos_payment.status.value
    return response


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

    tpos_payment = await get_tpos_payment_by_hash(payment_hash)
    payment = None
    if tpos_payment and tpos_payment.onchain_sale:
        if tpos_payment.tpos_id != tpos_id:
            raise HTTPException(HTTPStatus.NOT_FOUND, "TPoS payment does not exist.")
    else:
        payment = await get_standalone_payment(payment_hash, incoming=True)
        if not payment:
            raise HTTPException(HTTPStatus.NOT_FOUND, "Payment does not exist.")
        if not is_tpos_payment(payment, tpos, tpos_payment):
            raise HTTPException(HTTPStatus.NOT_FOUND, "TPoS payment does not exist.")

    receipt_type: Literal["receipt", "order_receipt"] = (
        "order_receipt" if data.receipt_type == "order_receipt" else "receipt"
    )
    receipt = _build_receipt_data(tpos, payment, tpos_payment)
    payload = ReceiptPrint(
        tpos_id=tpos_id,
        payment_hash=payment_hash,
        receipt_type=receipt_type,
        print_text=receipt.render_text(receipt_type),
        receipt=receipt.to_api_dict(),
    )
    await websocket_updater(tpos_id, json.dumps(payload.model_dump()))
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
    checkout = await get_tpos_payment_by_hash(payment_hash)
    if not checkout or not is_tpos_payment(payment, tpos, checkout):
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS payment does not exist."
        )
    if (
        payment.extra.get("fiat_method") != "cash"
        or payment.fiat_provider
        or payment.checking_id != f"internal_cash_{payment_hash}"
    ):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="Payment is not cash."
        )
    wallet = await get_valid_tpos_fiat_wallet(
        tpos,
        checkout.fiat_wallet_id,
        payment.extra.get("wallet_fiat_currency") or tpos.currency,
    )
    if not wallet:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail="Cash checkout is no longer assigned to an active fiat wallet.",
        )
    if not payment.success:
        if payment.is_expired:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT, detail="Cash checkout has expired."
            )
        if not payment.pending:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail="Cash checkout cannot be settled.",
            )
        payment.status = PaymentState.SUCCESS
        await update_payment(
            payment.copy(include={"wallet_id", "checking_id", "status"})
        )
    if not checkout.paid:
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

    return TposInvoiceResponse(
        payment_hash=payment.payment_hash,
        bolt11="" if tpos_payment.fiat_wallet_id else payment.bolt11,
        payment_request=payment_request,
        tpos_payment_id=tpos_payment.id,
        payment_options=[payment_method],
        payment_method=payment_method,
        extra=payment.extra or {},
    )


def _build_receipt_data(
    tpos: Tpos, payment: Payment | None, tpos_payment: TposPayment | None = None
) -> ReceiptData:
    sale = tpos_payment.onchain_sale if tpos_payment else None
    extra = sale.extra if sale else payment.extra if payment else {}
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
        paid=bool(
            (payment and payment.success) or (tpos_payment and tpos_payment.paid)
        ),
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
        created_at=(
            payment.created_at
            if payment
            else tpos_payment.created_at if tpos_payment else None
        ),
        business_name=tpos.business_name,
        business_address=tpos.business_address,
        business_vat_id=tpos.business_vat_id,
        only_show_sats_on_bitcoin=tpos.only_show_sats_on_bitcoin,
    )


async def _create_onchain_checkout(
    tpos: Tpos, data: CreateTposInvoice, extra: dict[str, Any]
) -> TposInvoiceResponse:
    wallet = await get_wallet(tpos.wallet)
    wallet_ref = tpos.onchain_wallet_id
    if not wallet or not wallet_ref:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Select an on-chain wallet.")
    selection = await validate_onchain_settings(
        wallet=wallet,
        onchain_enabled=tpos.onchain_enabled,
        onchain_wallet_id=wallet_ref,
    )
    if not selection:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "On-chain payments are disabled.")
    key, network, endpoint = selection
    amount = data.amount + (data.tip_amount or 0)
    if data.tab_settlement:
        extra["tab_settlement"]["amount_msat"] = amount * 1000
    extra.pop("lnaddress", None)
    checkout = TposPayment(
        id=uuid4().hex,
        tpos_id=tpos.id,
        payment_hash=uuid4().hex,
        amount=amount,
        payment_method="onchain",
        onchain_wallet_id=wallet_ref,
        onchain_zero_conf=tpos.onchain_zero_conf,
        mempool_endpoint=endpoint,
        onchain_network=network,
        onchain_sale=OnchainSale(
            wallet_id=tpos.wallet,
            expires_at=int(time()) + settings.lightning_invoice_expiry,
            memo=f"{data.memo} to {tpos.name}" if data.memo else tpos.name,
            extra=extra,
        ),
    )
    core = wallet_ref.startswith("core:")
    wallet_id = wallet_ref.removeprefix("core:")
    for _ in range(3):
        try:
            address_data = await fetch_onchain_address(key, wallet_id, core=core)
            address = address_data["address"]
            validate_onchain_address(address, network)
        except (HTTPError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                HTTPStatus.BAD_GATEWAY, "Could not get an on-chain receive address."
            ) from exc
        checkout.onchain_address = address
        if await create_onchain_payment(checkout):
            break
    else:
        raise HTTPException(
            HTTPStatus.CONFLICT, "Wallet did not supply an unused on-chain address."
        )
    return TposInvoiceResponse(
        payment_hash=checkout.payment_hash,
        bolt11="",
        payment_request=address,
        tpos_payment_id=checkout.id,
        payment_options=["btc_onchain"],
        onchain_address=address,
        onchain_amount_sat=amount,
        payment_method="onchain",
        extra={"amount_fiat": extra["amount_fiat"]} if "amount_fiat" in extra else {},
    )


async def _notify_checkout_created(
    tpos_id: str, data: CreateTposInvoice, response: TposInvoiceResponse
) -> None:
    payload = response.model_dump(exclude={"bolt11", "extra"})
    payload.update(
        type="invoice_created",
        tpos_id=tpos_id,
        paid_in_fiat=data.pay_in_fiat,
        amount_fiat=data.amount_fiat,
        tip_amount=data.tip_amount,
        tip_amount_fiat=data.tip_amount_fiat,
        exchange_rate=data.exchange_rate or None,
    )
    await websocket_updater(tpos_id, json.dumps(payload))
