import asyncio

from fastapi import APIRouter

from lnbits.db import Database
from lnbits.helpers import template_renderer
from lnbits.tasks import catch_everything_and_restart
from typing import Callable
from fastapi.responses import JSONResponse

db = Database("ext_tpos")

tpos_ext: APIRouter = APIRouter(prefix="/tpos", tags=["TPoS"])

tpos_static_files = [
    {
        "path": "/tpos/static",
        "name": "tpos_static",
    }
]


def tpos_renderer():
    return template_renderer(["tpos/templates"])


from .tasks import wait_for_paid_invoices
from .views import *  # noqa
from .views_api import *  # noqa


def tpos_start():
    loop = asyncio.get_event_loop()
    loop.create_task(catch_everything_and_restart(wait_for_paid_invoices))
