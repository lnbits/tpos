import json
from http import HTTPStatus
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from lnbits.core.crud import (
    get_latest_payments_by_extension,
    get_standalone_payment,
    get_user,
    get_wallet,
)
from loguru import logger
from lnbits.helpers import create_access_token
from lnbits.settings import settings
from lnbits.core.models import CreateInvoice, Payment, User, WalletTypeInfo
from lnbits.core.services import create_payment_request, websocket_updater
from lnbits.decorators import (
    require_admin_key,
    require_invoice_key,
)
from lnurl import LnurlPayResponse
from lnurl import decode as decode_lnurl
from lnurl import handle as lnurl_handle

from .crud import (
    create_tpos,
    delete_tpos,
    get_tpos,
    get_tposs,
    update_tpos,
)
from .models import (
    CreateTposData,
    CreateTposInvoice,
    CreateUpdateItemData,
    InventorySale,
    PayLnurlWData,
    TapToPay,
    Tpos,
)

tpos_api_router = APIRouter()


def _inventory_tags_to_list(raw_tags: str | list[str] | None) -> list[str]:
    if raw_tags is None:
        return []
    if isinstance(raw_tags, list):
        return [tag.strip() for tag in raw_tags if tag and tag.strip()]
    return [tag.strip() for tag in raw_tags.split(",") if tag and tag.strip()]


def _inventory_tags_to_string(raw_tags: str | list[str] | None) -> str | None:
    if raw_tags is None:
        return None
    if isinstance(raw_tags, str):
        return raw_tags
    return ",".join([tag for tag in raw_tags if tag])


def _first_image(images: str | list[str] | None) -> str | None:
    if not images:
        return None
    if isinstance(images, list):
        return _normalize_image(images[0]) if images else None
    raw = str(images).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            return _normalize_image(parsed[0])
    except Exception:
        pass
    if "|||" in raw:
        return _normalize_image(raw.split("|||")[0])
    if "," in raw:
        return _normalize_image(raw.split(",")[0])
    return _normalize_image(raw)


def _normalize_image(val: str | None) -> str | None:
    if not val:
        return None
    val = str(val).strip()
    if not val:
        return None
    if val.startswith("http") or val.startswith("/api/") or val.startswith("data:"):
        return val
    return f"/api/v1/assets/{val}/binary"


async def _get_default_inventory(user_id: str) -> dict[str, Any] | None:
    access = create_access_token({"sub": "", "usr": user_id}, token_expire_minutes=1)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/inventory/api/v1",
            headers={"Authorization": f"Bearer {access}"},
        )
        inventory = resp.json()
    if not inventory:
        return None
    if isinstance(inventory, list):
        inventory = inventory[0] if inventory else None
    if not isinstance(inventory, dict):
        return None
    inventory["tags"] = _inventory_tags_to_list(inventory.get("tags"))
    inventory["omit_tags"] = _inventory_tags_to_list(inventory.get("omit_tags"))
    return inventory


async def _get_inventory_items_for_tpos(
    user_id: str,
    inventory_id: str,
    tags: str | list[str] | None,
    omit_tags: str | list[str] | None,
) -> list[Any]:

    tag_list = _inventory_tags_to_list(tags)
    omit_list = [tag.lower() for tag in _inventory_tags_to_list(omit_tags)]
    allowed_tags = [tag.lower() for tag in tag_list]
    logger.debug(user_id)
    access = create_access_token({"sub": "", "usr": user_id}, token_expire_minutes=1)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/inventory/api/v1/items/{inventory_id}/paginated",
            headers={"Authorization": f"Bearer {access}"},
        )
        payload = resp.json()
        items = payload.get("data", []) if isinstance(payload, dict) else payload

    def has_allowed_tag(item_tags: str | list[str] | None) -> bool:
        # When no tags are configured for this TPoS, show no items
        if not tag_list:
            return False
        item_tag_list = [tag.lower() for tag in _inventory_tags_to_list(item_tags)]
        return any(tag in item_tag_list for tag in allowed_tags)

    def has_omit_tag(item_omit_tags: str | list[str] | None) -> bool:
        if not omit_list:
            return False
        item_tag_list = [tag.lower() for tag in _inventory_tags_to_list(item_omit_tags)]
        return any(tag in item_tag_list for tag in omit_list)

    filtered = [
        item
        for item in items
        if has_allowed_tag(item.get("tags")) and not has_omit_tag(item.get("omit_tags"))
    ]
    # If no items matched the provided tags, fall back to all items minus omitted ones.
    if tag_list and not filtered:
        filtered = [item for item in items if not has_omit_tag(item.get("omit_tags"))]

    # hide items with no stock when stock tracking is enabled
    return [
        item
        for item in filtered
        if item.get("quantity_in_stock") is None or item.get("quantity_in_stock") > 0
    ]


def _inventory_available_for_user(user: User | None) -> bool:
    return bool(user and "inventory" in (user.extensions or []))


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
    if not user or not _inventory_available_for_user(user):
        return {"enabled": False, "inventory_id": None, "tags": [], "omit_tags": []}
    logger.debug(wallet.wallet.adminkey)
    inventory = await _get_default_inventory(user.id)
    tags = _inventory_tags_to_list(inventory.get("tags")) if inventory else []
    omit_tags = _inventory_tags_to_list(inventory.get("omit_tags")) if inventory else []
    return {
        "enabled": True,
        "inventory_id": inventory.get("id") if inventory else None,
        "tags": tags,
        "omit_tags": omit_tags,
    }


@tpos_api_router.post("/api/v1/tposs", status_code=HTTPStatus.CREATED)
async def api_tpos_create(
    data: CreateTposData, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    data.wallet = wallet.wallet.id
    user = await get_user(wallet.wallet.user)
    if data.use_inventory and not _inventory_available_for_user(user):
        data.use_inventory = False
    if data.use_inventory and not data.inventory_id:
        inventory = await _get_default_inventory(user.id)
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
    if update_payload.get("use_inventory") and not update_payload.get("inventory_id"):
        inventory = await _get_default_inventory(user.id)
        if inventory:
            update_payload["inventory_id"] = inventory.get("id")
        else:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="No inventory found for this user.",
            )
    if update_payload.get("use_inventory") and not _inventory_available_for_user(user):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Inventory extension must be enabled to use it.",
        )
    if "inventory_tags" in update_payload:
        update_payload["inventory_tags"] = _inventory_tags_to_string(
            _inventory_tags_to_list(update_payload["inventory_tags"])
        )
    if "inventory_omit_tags" in update_payload:
        update_payload["inventory_omit_tags"] = _inventory_tags_to_string(
            _inventory_tags_to_list(update_payload["inventory_omit_tags"])
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
async def api_tpos_create_invoice(tpos_id: str, data: CreateTposInvoice) -> Payment:
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
        inventory_payload.tags = _inventory_tags_to_list(inventory_payload.tags)
        if tpos.inventory_id and inventory_payload.inventory_id != tpos.inventory_id:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Mismatched inventory selection.",
            )
        allowed_tags = set(_inventory_tags_to_list(tpos.inventory_tags))
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
            "lnaddress": data.user_lnaddress if data.user_lnaddress else None,
            "internal_memo": data.internal_memo if data.internal_memo else None,
        }
        if inventory_payload:
            extra["inventory"] = inventory_payload.dict()
        if data.pay_in_fiat and tpos.fiat_provider:
            extra["fiat_method"] = data.fiat_method if data.fiat_method else "checkout"
        invoice_data = CreateInvoice(
            unit=currency,
            out=False,
            amount=amount,
            memo=f"{data.memo} to {tpos.name}" if data.memo else f"{tpos.name}",
            extra=extra,
            fiat_provider=tpos.fiat_provider if data.pay_in_fiat else None,
        )
        payment = await create_payment_request(tpos.wallet, invoice_data)
        if (invoice_data.extra or {}).get("fiat_method") == "terminal":
            pi_id = payment.extra.get("fiat_checking_id")
            client_secret = payment.extra.get("fiat_payment_request")
            if pi_id and client_secret:
                amount_minor = round(amount * 100)
                payload = TapToPay(
                    payment_intent_id=pi_id,
                    client_secret=client_secret,
                    currency=invoice_data.unit.lower(),
                    amount=amount_minor,
                    tpos_id=tpos_id,
                    payment_hash=payment.payment_hash,
                )
                await websocket_updater(tpos_id, str(payload))
        return payment

    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


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
        inventory_data = await _get_default_inventory(wallet.user)
        inventory_id = inventory_data.get("id") if inventory_data else None
    else:
        inventory_data = await _get_default_inventory(wallet.user)
    if not inventory_id:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="No inventory found for this TPoS.",
        )

    items = await _get_inventory_items_for_tpos(
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
            "image": _first_image(item.get("images")),
            "categories": _inventory_tags_to_list(item.get("tags")),
            "quantity_in_stock": item.get("quantity_in_stock"),
            "disabled": (not item.get("is_active"))
            or (
                item.get("quantity_in_stock") is not None
                and item.get("quantity_in_stock") <= 0
            ),
        }
        for item in items
    ]
