import json
from datetime import datetime, timezone
from http import HTTPStatus
from time import time
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from lnbits.core.crud import (
    get_account,
    get_standalone_payment,
    get_user,
    get_wallet,
)
from lnbits.core.crud.payments import update_payment_checking_id
from lnbits.core.crud.users import (
    get_user_access_control_lists,
    update_account,
    update_user_access_control_list,
)
from lnbits.core.models import CreateInvoice, Payment, WalletTypeInfo
from lnbits.core.models.misc import SimpleItem
from lnbits.core.models.users import (
    AccessControlList,
    AccessTokenPayload,
    EndpointAccess,
    UserLabel,
)
from lnbits.core.services import create_payment_request, websocket_updater
from lnbits.decorators import (
    require_admin_key,
    require_invoice_key,
)
from lnbits.helpers import create_access_token, get_api_routes
from lnbits.tasks import internal_invoice_queue_put
from lnurl import LnurlPayResponse
from lnurl import decode as decode_lnurl
from lnurl import handle as lnurl_handle

from .crud import (
    create_tpos,
    create_tpos_payment,
    delete_tpos,
    get_latest_tpos_payments,
    get_tpos,
    get_tpos_payment_by_hash,
    get_tposs,
    update_tpos,
)
from .helpers import (
    first_image,
    inventory_tags_to_list,
    inventory_tags_to_string,
)
from .models import (
    CreateTposData,
    CreateTposInvoice,
    CreateUpdateItemData,
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
from .services import (
    fetch_onchain_address,
    fetch_watchonly_config,
    fetch_watchonly_wallet,
    fetch_watchonly_wallets,
    get_default_inventory,
    get_inventory_items_for_tpos,
    inventory_available_for_user,
    watchonly_available_for_user,
)

tpos_api_router = APIRouter()


def _two_year_token_expiry_minutes() -> int:
    now = datetime.now(timezone.utc)
    try:
        expires_at = now.replace(year=now.year + 2)
    except ValueError:
        # Handle February 29 by falling back to February 28 two years later.
        expires_at = now.replace(year=now.year + 2, month=2, day=28)
    return max(1, int((expires_at - now).total_seconds() // 60))


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


async def _get_watchonly_status(wallet) -> dict[str, Any]:
    if not await watchonly_available_for_user(wallet.user):
        return {
            "available": False,
            "message": "Watchonly extension must be enabled for this user.",
            "network": None,
            "wallets": [],
        }

    try:
        config = await fetch_watchonly_config(wallet.inkey)
        network = config.get("network")
        wallets = await fetch_watchonly_wallets(wallet.inkey, network)
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Watchonly extension is not reachable: {exc!s}",
        ) from exc

    return {
        "available": True,
        "message": None,
        "network": network,
        "wallets": wallets,
        "mempool_endpoint": config.get("mempool_endpoint"),
    }


async def _validate_watchonly_settings(
    *,
    wallet,
    onchain_enabled: bool,
    onchain_wallet_id: str | None,
) -> dict[str, Any] | None:
    if not onchain_enabled:
        return None
    if not onchain_wallet_id:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Watchonly wallet is required when onchain payments are enabled.",
        )

    status = await _get_watchonly_status(wallet)
    if not status["available"]:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=status["message"] or "Watchonly extension is not available.",
        )

    try:
        watch_wallet = await fetch_watchonly_wallet(wallet.inkey, onchain_wallet_id)
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Cannot access watchonly wallet: {exc!s}",
        ) from exc

    if watch_wallet.get("network") != status["network"]:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Watchonly wallet network does not match the user watchonly config.",
        )

    return {
        "watch_wallet": watch_wallet,
        "network": status["network"],
        "mempool_endpoint": status["mempool_endpoint"],
    }


def _build_bip21(onchain_address: str, amount_sat: int, bolt11: str | None = None) -> str:
    amount_btc = amount_sat / 100_000_000
    bip21 = f"bitcoin:{onchain_address.upper()}?amount={amount_btc:.8f}"
    if bolt11:
        bip21 += f"&lightning={bolt11.upper()}"
    return bip21


def _payment_method_from_payment(payment: Payment) -> str:
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

    options = [payment_method]
    unified_qr = None
    if tpos_payment.onchain_address:
        options = ["uqr", "lightning", "onchain"]
        unified_qr = _build_bip21(
            tpos_payment.onchain_address,
            tpos_payment.amount,
            payment.bolt11 if payment_method == "lightning" else payment.bolt11,
        )

    return TposInvoiceResponse(
        payment_hash=payment.payment_hash,
        bolt11=payment.bolt11,
        payment_request=payment_request,
        tpos_payment_id=tpos_payment.id,
        payment_options=options,
        onchain_address=tpos_payment.onchain_address,
        unified_qr=unified_qr,
        payment_method=payment_method,
        extra=payment.extra or {},
    )


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


@tpos_api_router.get("/api/v1/inventory/status", status_code=HTTPStatus.OK)
async def api_inventory_status(
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> dict:
    user = await get_user(wallet.wallet.user)
    if not inventory_available_for_user(user):
        return {"enabled": False, "inventory_id": None, "tags": [], "omit_tags": []}
    inventory = await get_default_inventory(wallet.wallet.user)
    tags = inventory_tags_to_list(inventory.get("tags")) if inventory else []
    omit_tags = inventory_tags_to_list(inventory.get("omit_tags")) if inventory else []
    return {
        "enabled": True,
        "inventory_id": inventory.get("id") if inventory else None,
        "tags": tags,
        "omit_tags": omit_tags,
    }


@tpos_api_router.get("/api/v1/onchain/status", status_code=HTTPStatus.OK)
async def api_onchain_status(
    key_info: WalletTypeInfo = Depends(require_admin_key),
) -> dict[str, Any]:
    return await _get_watchonly_status(key_info.wallet)


@tpos_api_router.post("/api/v1/tposs", status_code=HTTPStatus.CREATED)
async def api_tpos_create(
    data: CreateTposData, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    data.wallet = wallet.wallet.id
    await _validate_watchonly_settings(
        wallet=wallet.wallet,
        onchain_enabled=data.onchain_enabled,
        onchain_wallet_id=data.onchain_wallet_id,
    )
    user = await get_user(wallet.wallet.user)
    if not (user and user.super_user):
        data.allow_cash_settlement = False
    if data.currency == "sats":
        data.allow_cash_settlement = False
    if data.use_inventory and not inventory_available_for_user(user):
        data.use_inventory = False
    if data.use_inventory and not data.inventory_id:
        inventory = await get_default_inventory(wallet.wallet.user)
        if not inventory:
            data.use_inventory = False
        else:
            data.inventory_id = inventory.get("id")
            data.inventory_tags = inventory.get("tags")
            data.inventory_omit_tags = inventory.get("omit_tags")
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
    user = await get_user(wallet.wallet.user)
    update_payload = data.dict(exclude_unset=True)
    desired_onchain_enabled = update_payload.get("onchain_enabled", tpos.onchain_enabled)
    desired_onchain_wallet_id = update_payload.get(
        "onchain_wallet_id", tpos.onchain_wallet_id
    )
    await _validate_watchonly_settings(
        wallet=wallet.wallet,
        onchain_enabled=desired_onchain_enabled,
        onchain_wallet_id=desired_onchain_wallet_id,
    )
    desired_currency = update_payload.get("currency", tpos.currency)
    if desired_currency == "sats":
        update_payload["allow_cash_settlement"] = False
    if "allow_cash_settlement" in update_payload:
        if update_payload["allow_cash_settlement"] and not (user and user.super_user):
            raise HTTPException(
                status_code=HTTPStatus.FORBIDDEN,
                detail="Cash settlement can only be enabled by super users.",
            )
    if update_payload.get("use_inventory") and not update_payload.get("inventory_id"):
        inventory = await get_default_inventory(wallet.wallet.user)
        if inventory:
            update_payload["inventory_id"] = inventory.get("id")
        else:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="No inventory found for this user.",
            )
    if update_payload.get("use_inventory") and not inventory_available_for_user(user):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Inventory extension must be enabled to use it.",
        )
    if "inventory_tags" in update_payload:
        update_payload["inventory_tags"] = inventory_tags_to_string(
            inventory_tags_to_list(update_payload["inventory_tags"])
        )
    if "inventory_omit_tags" in update_payload:
        update_payload["inventory_omit_tags"] = inventory_tags_to_string(
            inventory_tags_to_list(update_payload["inventory_omit_tags"])
        )
    for field, value in update_payload.items():
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


@tpos_api_router.post("/api/v1/tposs/{tpos_id}/wrapper-token")
async def api_tpos_create_wrapper_token(
    tpos_id: str,
    request: Request,
    wallet: WalletTypeInfo = Depends(require_admin_key),
):
    tpos = await get_tpos(tpos_id)

    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    if tpos.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your TPoS.")

    account = await get_account(wallet.wallet.user)
    if not account or not account.username:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="A username is required to create a wrapper ACL token.",
        )

    user_acls = await get_user_access_control_lists(account.id)
    acl_name = "TPoS Wrapper Fiat"
    acl = next(
        (
            existing_acl
            for existing_acl in user_acls.access_control_list
            if existing_acl.name == acl_name
        ),
        None,
    )

    api_routes = get_api_routes(request.app.router.routes)
    fiat_endpoints = []
    for path, name in api_routes.items():
        is_fiat_endpoint = path.startswith("/api/v1/fiat")
        fiat_endpoints.append(
            EndpointAccess(
                path=path,
                name=name,
                read=is_fiat_endpoint,
                write=is_fiat_endpoint,
            )
        )
    fiat_endpoints.sort(key=lambda e: e.name.lower())

    if acl:
        acl.endpoints = fiat_endpoints
    else:
        acl = AccessControlList(
            id=uuid4().hex,
            name=acl_name,
            endpoints=fiat_endpoints,
            token_id_list=[],
        )
        user_acls.access_control_list.append(acl)
        user_acls.access_control_list.sort(
            key=lambda existing_acl: existing_acl.name.lower()
        )

    token_expire_minutes = _two_year_token_expiry_minutes()
    api_token_id = uuid4().hex
    payload = AccessTokenPayload(
        sub=account.username, api_token_id=api_token_id, auth_time=int(time())
    )
    api_token = create_access_token(
        data=payload.dict(), token_expire_minutes=token_expire_minutes
    )

    acl.token_id_list.append(
        SimpleItem(id=api_token_id, name=f"TPoS Wrapper {tpos_id}")
    )
    await update_user_access_control_list(user_acls)

    return {"auth": api_token, "expiration_time_minutes": token_expire_minutes}


@tpos_api_router.post(
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

    cash_method = data.pay_in_fiat and data.fiat_method == "cash"
    if cash_method and not tpos.allow_cash_settlement:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="Cash settlement is not enabled for this TPoS.",
        )
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
        if cash_method:
            wallet = await get_wallet(tpos.wallet)
            if wallet:
                account = await get_account(wallet.user)
                if account:
                    existing = {label.name for label in account.extra.labels or []}
                    if "cash" not in existing:
                        account.extra.labels.append(
                            UserLabel(
                                name="cash",
                                description="Cash payment",
                                color="#FFC107",
                            )
                        )
                        await update_account(account)
        if inventory_payload:
            extra["inventory"] = inventory_payload.dict()
        if data.pay_in_fiat:
            extra["fiat_method"] = data.fiat_method if data.fiat_method else "checkout"
            if data.fiat_method == "terminal" and tpos.stripe_reader_id:
                extra["terminal"] = {"reader_id": tpos.stripe_reader_id}
        invoice_data = CreateInvoice(
            unit=currency,
            out=False,
            amount=amount,
            memo=f"{data.memo} to {tpos.name}" if data.memo else f"{tpos.name}",
            extra=extra,
            fiat_provider=(
                tpos.fiat_provider if data.pay_in_fiat and not cash_method else None
            ),
            internal=bool(cash_method),
            labels=["cash"] if cash_method else [],
        )
        payment = await create_payment_request(tpos.wallet, invoice_data)
        if cash_method:
            new_checking_id = f"internal_cash_{payment.payment_hash}"
            await update_payment_checking_id(payment.checking_id, new_checking_id)
            payment.checking_id = new_checking_id

        onchain_address = None
        mempool_endpoint = None
        if tpos.onchain_enabled and not data.pay_in_fiat:
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
                "exchange_rate": data.exchange_rate if data.exchange_rate else None,
                "tpos_payment_id": response_payload.tpos_payment_id,
                "payment_options": response_payload.payment_options,
                "onchain_address": response_payload.onchain_address,
                "unified_qr": response_payload.unified_qr,
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


@tpos_api_router.get("/api/v1/tposs/{tpos_id}/invoices")
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
    tpos_payment = await get_tpos_payment_by_hash(payment_hash)

    if extra:
        return _build_receipt_data(tpos, payment, tpos_payment).to_api_dict()
    return {"paid": payment.success or bool(tpos_payment and tpos_payment.paid)}


@tpos_api_router.post(
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


@tpos_api_router.post(
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


@tpos_api_router.put("/api/v1/tposs/{tpos_id}/items", status_code=HTTPStatus.CREATED)
async def api_tpos_create_items(
    data: CreateUpdateItemData,
    tpos_id: str,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> Tpos:
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
    try:
        res = await lnurl_handle(lnaddress)
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Error checking lnaddress: {exc!s}",
        ) from exc

    if not isinstance(res, LnurlPayResponse):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="The provided lnaddress returned an unexpected response type.",
        )

    return True


@tpos_api_router.get(
    "/api/v1/tposs/{tpos_id}/inventory-items", status_code=HTTPStatus.OK
)
async def api_tpos_inventory_items(tpos_id: str):
    tpos = await get_tpos(tpos_id)
    if not tpos or not tpos.use_inventory:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Inventory not enabled for this TPoS.",
        )

    wallet = await get_wallet(tpos.wallet)
    if not wallet:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Wallet not found for this TPoS.",
        )

    inventory_id = tpos.inventory_id
    inventory_data: dict[str, Any] | None = None
    if not inventory_id:
        inventory_data = await get_default_inventory(wallet.user)
        inventory_id = inventory_data.get("id") if inventory_data else None
    else:
        inventory_data = await get_default_inventory(wallet.user)
    if not inventory_id:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="No inventory found for this TPoS.",
        )

    items = await get_inventory_items_for_tpos(
        wallet.user,
        inventory_id,
        tpos.inventory_tags,
        tpos.inventory_omit_tags,
    )
    return [
        {
            "id": item.get("id"),
            "title": item.get("name"),
            "description": item.get("description"),
            "price": item.get("price"),
            "tax": item.get("tax_rate"),
            "image": first_image(item.get("images")),
            "categories": inventory_tags_to_list(item.get("tags")),
            "quantity_in_stock": item.get("quantity_in_stock"),
            "disabled": (not item.get("is_active"))
            or (
                item.get("quantity_in_stock") is not None
                and item.get("quantity_in_stock") <= 0
            ),
        }
        for item in items
    ]
