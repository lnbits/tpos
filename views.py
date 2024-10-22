from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Request
from lnbits.core.models import User
from lnbits.decorators import check_user_exists
from lnbits.helpers import template_renderer
from lnbits.settings import settings
from starlette.responses import HTMLResponse

from .crud import get_clean_tpos, get_tpos
from .models import TposClean

tpos_generic_router = APIRouter()


def tpos_renderer():
    return template_renderer(["tpos/templates"])


@tpos_generic_router.get("/", response_class=HTMLResponse)
async def index(request: Request, user: User = Depends(check_user_exists)):
    return tpos_renderer().TemplateResponse(
        "tpos/index.html", {"request": request, "user": user.json()}
    )


@tpos_generic_router.get("/{tpos_id}")
async def tpos(request: Request, tpos_id):
    tpos = await get_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )
    withdraw_pin_open = tpos.withdraw_pin or 0
    tpos_clean = TposClean(**tpos.dict())
    response = tpos_renderer().TemplateResponse(
        "tpos/tpos.html",
        {
            "request": request,
            "tpos": tpos_clean.json(),
            "withdraw_pin_open": withdraw_pin_open,
            "withdraw_maximum": tpos.withdraw_maximum,
            "web_manifest": f"/tpos/manifest/{tpos_id}.webmanifest",
        },
    )
    # This is just for hiding the user-account UI elements.
    # It is not a security measure.
    response.set_cookie("is_lnbits_user_authorized", "false", path=request.url.path)
    return response


@tpos_generic_router.get("/manifest/{tpos_id}.webmanifest")
async def manifest(tpos_id: str):
    tpos = await get_clean_tpos(tpos_id)
    if not tpos:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="TPoS does not exist."
        )

    return {
        "short_name": settings.lnbits_site_title,
        "name": tpos.name + " - " + settings.lnbits_site_title,
        "icons": [
            {
                "src": (
                    settings.lnbits_custom_logo
                    if settings.lnbits_custom_logo
                    else "https://cdn.jsdelivr.net/gh/lnbits/lnbits@0.3.0/docs/logos/lnbits.png"
                ),
                "type": "image/png",
                "sizes": "900x900",
            }
        ],
        "start_url": "/tpos/" + tpos_id,
        "background_color": "#1F2234",
        "description": "Bitcoin Lightning tPOS",
        "display": "standalone",
        "scope": "/tpos/" + tpos_id,
        "theme_color": "#1F2234",
        "shortcuts": [
            {
                "name": tpos.name + " - " + settings.lnbits_site_title,
                "short_name": tpos.name,
                "description": tpos.name + " - " + settings.lnbits_site_title,
                "url": "/tpos/" + tpos_id,
            }
        ],
    }
