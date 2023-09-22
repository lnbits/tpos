import asyncio

from fastapi import APIRouter, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.routing import APIRoute

from lnbits.db import Database
from lnbits.helpers import template_renderer
from lnbits.tasks import catch_everything_and_restart
from typing import Callable
from fastapi.responses import JSONResponse

db = Database("ext_tpos")


class LNURLErrorResponseHandler(APIRoute):
    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                response = await original_route_handler(request)
            except HTTPException as exc:
                logger.debug(f"HTTPException: {exc}")
                response = JSONResponse(
                    status_code=exc.status_code,
                    content={"status": "ERROR", "reason": f"{exc.detail}"},
                )
            except Exception as exc:
                raise exc

            return response

        return custom_route_handler


tpos_ext: APIRouter = APIRouter(prefix="/tpos", tags=["TPoS"])
tpos_ext.route_class = LNURLErrorResponseHandler

tpos_static_files = [
    {
        "path": "/tpos/static",
        "app": StaticFiles(directory="lnbits/extensions/tpos/static"),
        "name": "tpos_static",
    }
]


def tpos_renderer():
    return template_renderer(["lnbits/extensions/tpos/templates"])


from .lnurl import *  # noqa: F401,F403
from .tasks import wait_for_paid_invoices
from .views import *  # noqa
from .views_api import *  # noqa


def tpos_start():
    loop = asyncio.get_event_loop()
    loop.create_task(catch_everything_and_restart(wait_for_paid_invoices))