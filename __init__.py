import asyncio

from fastapi import APIRouter, Request, Response
from fastapi.routing import APIRoute

from lnbits.db import Database
from lnbits.helpers import template_renderer
from lnbits.tasks import create_permanent_task
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


tpos_ext: APIRouter = APIRouter(
    prefix="/tpos", tags=["TPoS"], route_class=LNURLErrorResponseHandler
)

tpos_static_files = [
    {
        "path": "/tpos/static",
        "name": "tpos_static",
    }
]


def tpos_renderer():
    return template_renderer(["tpos/templates"])


from .lnurl import *  # noqa: F401,F403
from .tasks import wait_for_paid_invoices
from .views import *  # noqa
from .views_api import *  # noqa


scheduled_tasks: list[asyncio.Task] = []

def tpos_stop():
    for task in scheduled_tasks:
        try:
            task.cancel()
        except Exception as ex:
            logger.warning(ex)

def tpos_start():
    task = create_permanent_task(wait_for_paid_invoices)
    scheduled_tasks.append(task)
