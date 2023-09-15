import asyncio

from fastapi import APIRouter

from lnbits.db import Database
from lnbits.helpers import template_renderer
from lnbits.tasks import catch_everything_and_restart

db = Database("ext_tpos")

tpos_ext: APIRouter = APIRouter(prefix="/tpos", tags=["TPoS"])
scheduled_tasks: list[asyncio.Task] = []

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
    task = loop.create_task(catch_everything_and_restart(wait_for_paid_invoices))
    scheduled_tasks.append(task)
