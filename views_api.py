import json
from http import HTTPStatus
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from lnbits.core.crud import (
    get_account,
    get_user,
    get_wallet,
)
from lnbits.core.crud.payments import update_payment_checking_id
from lnbits.core.crud.users import update_account
from lnbits.core.models import CreateInvoice, WalletTypeInfo
from lnbits.core.models.users import UserLabel
from lnbits.core.services import create_payment_request, websocket_updater
from lnbits.decorators import (
    require_admin_key,
    require_invoice_key,
)
from lnurl import LnurlPayResponse
from lnurl import handle as lnurl_handle

from .crud import (
    create_tpos,
    create_tpos_payment,
    delete_tpos,
    get_tpos,
    get_tposs,
    update_tpos,
)
from .helpers import (
    inventory_tags_to_list,
    inventory_tags_to_string,
)
from .models import (
    CreateTposData,
    CreateTposInvoice,
    CreateUpdateItemData,
    InventorySale,
    TapToPay,
    Tpos,
    TposPayment,
)
from .services import (
    ensure_tpos_tabs_access,
    fetch_onchain_address,
    fetch_single_tab_for_tpos,
    get_default_inventory,
    inventory_available_for_user,
)
from .views_inventory import tpos_inventory_router
from .views_onchain import _validate_watchonly_settings, tpos_onchain_router
from .views_payments import _serialize_tpos_invoice_response, tpos_payments_router
from .views_tabs import (
    _ensure_tab_matches_tpos_currency,
    _tab_settlement_tolerance,
    tpos_tabs_router,
)
from .views_wrapper import tpos_wrapper_router

tpos_api_router = APIRouter()
tpos_api_router.include_router(tpos_inventory_router)
tpos_api_router.include_router(tpos_onchain_router)
tpos_api_router.include_router(tpos_tabs_router)
tpos_api_router.include_router(tpos_wrapper_router)
tpos_api_router.include_router(tpos_payments_router)


async def _get_tpos_or_404(tpos_id: str) -> Tpos:
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    return tpos


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
    data: CreateTposData, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    data.wallet = wallet.wallet.id
    await _validate_watchonly_settings(
        wallet=wallet.wallet,
        onchain_enabled=data.onchain_enabled,
        onchain_wallet_id=data.onchain_wallet_id,
    )
    if not data.tabs_enabled:
        data.tabs_allow_create = False
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
    update_payload.pop("wallet", None)
    desired_onchain_enabled = update_payload.get(
        "onchain_enabled", tpos.onchain_enabled
    )
    desired_onchain_wallet_id = update_payload.get(
        "onchain_wallet_id", tpos.onchain_wallet_id
    )
    await _validate_watchonly_settings(
        wallet=wallet.wallet,
        onchain_enabled=desired_onchain_enabled,
        onchain_wallet_id=desired_onchain_wallet_id,
    )
    desired_tabs_enabled = update_payload.get("tabs_enabled", tpos.tabs_enabled)
    if not desired_tabs_enabled:
        update_payload["tabs_allow_create"] = False
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
        tab = await fetch_single_tab_for_tpos(
            user_id=user_id, tab_id=tab_settlement.tab_id
        )
        _ensure_tab_matches_tpos_currency(tab, tpos)
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
        if amount_over_balance > _tab_settlement_tolerance(tab.get("currency")):
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
                    label_name = "cash" if cash_method else "onchain"
                    label_description = (
                        "Cash payment" if cash_method else "Onchain payment"
                    )
                    label_color = "#FFC107" if cash_method else "#ED8403"
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
            labels=["cash"] if cash_method else (["onchain"] if onchain_method else []),
        )
        payment = await create_payment_request(tpos.wallet, invoice_data)
        if cash_method:
            new_checking_id = f"internal_cash_{payment.payment_hash}"
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
