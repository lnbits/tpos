from datetime import datetime, timezone
from http import HTTPStatus
from time import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from lnbits.core.crud import get_account
from lnbits.core.crud.users import (
    get_user_access_control_lists,
    update_user_access_control_list,
)
from lnbits.core.models import WalletTypeInfo
from lnbits.core.models.misc import SimpleItem
from lnbits.core.models.users import (
    AccessControlList,
    AccessTokenPayload,
    EndpointAccess,
)
from lnbits.decorators import require_admin_key
from lnbits.helpers import create_access_token, get_api_routes

from .crud import get_tpos
from .services import fetch_wrapper_assetlinks

tpos_wrapper_router = APIRouter()


def _two_year_token_expiry_minutes() -> int:
    now = datetime.now(timezone.utc)
    try:
        expires_at = now.replace(year=now.year + 2)
    except ValueError:
        expires_at = now.replace(year=now.year + 2, month=2, day=28)
    return max(1, int((expires_at - now).total_seconds() // 60))


@tpos_wrapper_router.get("/api/v1/well-known/assetlinks.json")
async def api_tpos_assetlinks() -> JSONResponse:
    try:
        assetlinks = await fetch_wrapper_assetlinks()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return JSONResponse(content=assetlinks, media_type="application/json")


@tpos_wrapper_router.post("/api/v1/tposs/{tpos_id}/wrapper-token")
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
