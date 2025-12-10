import json
from http import HTTPStatus

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from lnbits.core.crud import (
    get_latest_payments_by_extension,
    get_standalone_payment,
    get_user,
)
from lnbits.core.models import CreateInvoice, Payment, User, WalletTypeInfo
from lnbits.core.services import create_payment_request, websocket_updater
from lnbits.decorators import (
    require_admin_key,
    require_invoice_key,
)
from lnurl import LnurlPayResponse
from lnurl import decode as decode_lnurl
from lnurl import handle as lnurl_handle
from loguru import logger

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

try:  # inventory extension is optional
    from ..inventory.crud import (
        create_inventory_update_log,
        db as inventory_db,
        get_inventories as get_user_inventories,
        get_items_by_ids as inventory_get_items_by_ids,
        update_item as inventory_update_item,
    )
    from ..inventory.models import (
        CreateInventoryUpdateLog,
        Item as InventoryItem,
        UpdateSource,
    )
except Exception:  # pragma: no cover - guard when inventory extension is absent
    get_user_inventories = None
    inventory_db = None
    inventory_get_items_by_ids = None
    inventory_update_item = None
    CreateInventoryUpdateLog = None
    InventoryItem = None
    UpdateSource = None

tpos_api_router = APIRouter()


def _inventory_tags_to_list(raw_tags: str | list[str] | None) -> list[str]:
    if raw_tags is None:
        return []
    if isinstance(raw_tags, list):
        return [tag for tag in raw_tags if tag]
    return [tag for tag in raw_tags.split(",") if tag]


def _inventory_tags_to_string(raw_tags: str | list[str] | None) -> str | None:
    if raw_tags is None:
        return None
    if isinstance(raw_tags, str):
        return raw_tags
    return ",".join([tag for tag in raw_tags if tag])


async def _get_default_inventory_id(user_id: str) -> str | None:
    if not get_user_inventories:
        return None
    inventory = await get_user_inventories(user_id)
    return inventory.id if inventory else None


async def _get_inventory_tags(inventory_id: str) -> list[str]:
    if not inventory_db:
        return []
    rows = await inventory_db.fetchall(
        """
        SELECT tags FROM inventory.items
        WHERE inventory_id = :inventory_id
        AND tags IS NOT NULL AND tags != ''
        AND is_active = true AND is_approved = true
        """,
        {"inventory_id": inventory_id},
    )
    tags: set[str] = set()
    for row in rows:
        raw_tags = row["tags"] if isinstance(row, dict) else row.tags  # type: ignore[attr-defined]
        tags.update(_inventory_tags_to_list(raw_tags))
    return sorted(tags)


async def _get_inventory_items_for_tpos(
    inventory_id: str, tags: str | list[str] | None
) -> list[InventoryItem]:
    if not inventory_db or not InventoryItem:
        return []

    tag_list = _inventory_tags_to_list(tags)
    where = [
        "inventory_id = :inventory_id",
        "is_active = true",
        "is_approved = true",
    ]
    params: dict[str, str] = {"inventory_id": inventory_id}
    if tag_list:
        tag_filters = []
        for idx, tag in enumerate(tag_list):
            key = f"tag_{idx}"
            tag_filters.append(f"tags LIKE :{key}")
            params[key] = f"%{tag}%"
        where.append("(" + " OR ".join(tag_filters) + ")")

    query = f"""
        SELECT * FROM inventory.items
        WHERE {' AND '.join(where)}
    """
    items = await inventory_db.fetchall(query, params, model=InventoryItem)
    # hide items with no stock when stock tracking is enabled
    return [
        item
        for item in items
        if item.quantity_in_stock is None or item.quantity_in_stock > 0
    ]


def _inventory_available_for_user(user: User) -> bool:
    return bool(
        get_user_inventories
        and user
        and getattr(user, "extensions", None)
        and "inventory" in user.extensions
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
    key_info: WalletTypeInfo = Depends(require_admin_key),
) -> dict:
    user = await get_user(key_info.wallet.user)
    if not user or not _inventory_available_for_user(user):
        return {"enabled": False, "inventory_id": None, "tags": []}

    inventory_id = await _get_default_inventory_id(user.id)
    tags = await _get_inventory_tags(inventory_id) if inventory_id else []
    return {"enabled": True, "inventory_id": inventory_id, "tags": tags}


@tpos_api_router.post("/api/v1/tposs", status_code=HTTPStatus.CREATED)
async def api_tpos_create(
    data: CreateTposData, key_type: WalletTypeInfo = Depends(require_admin_key)
):
    data.wallet = key_type.wallet.id
    user = await get_user(key_type.wallet.user)
    if data.use_inventory and not _inventory_available_for_user(user):
        data.use_inventory = False
    if data.use_inventory and not data.inventory_id:
        data.inventory_id = await _get_default_inventory_id(key_type.wallet.user)
        if not data.inventory_id:
            data.use_inventory = False
    if data.inventory_tags:
        data.inventory_tags = _inventory_tags_to_list(data.inventory_tags)
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
        default_inventory_id = await _get_default_inventory_id(wallet.wallet.user)
        if default_inventory_id:
            update_payload["inventory_id"] = default_inventory_id
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
        if (
            tpos.inventory_id
            and inventory_payload.inventory_id != tpos.inventory_id
        ):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Mismatched inventory selection.",
            )
        allowed_tags = set(_inventory_tags_to_list(tpos.inventory_tags))
        if allowed_tags and any(tag not in allowed_tags for tag in inventory_payload.tags):
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
    if not tpos or not tpos.use_inventory or not tpos.inventory_id:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="Inventory not enabled for this TPoS.",
        )
    items = await _get_inventory_items_for_tpos(tpos.inventory_id, tpos.inventory_tags)
    return [
        {
            "id": item.id,
            "title": item.name,
            "description": item.description,
            "price": item.price,
            "tax": item.tax_rate,
            "image": item.images.split(",")[0] if item.images else None,
            "categories": _inventory_tags_to_list(item.tags),
            "quantity_in_stock": item.quantity_in_stock,
            "disabled": (not item.is_active)
            or (item.quantity_in_stock is not None and item.quantity_in_stock <= 0),
        }
        for item in items
    ]
