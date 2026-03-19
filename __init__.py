import asyncio

from fastapi import APIRouter
from loguru import logger

from .crud import db
from .tasks import poll_onchain_payments, wait_for_paid_invoices
from .views import tpos_generic_router
from .views_api import tpos_api_router
from .views_atm import tpos_atm_router
from .views_lnurl import tpos_lnurl_router

tpos_ext = APIRouter(prefix="/tpos", tags=["TPoS"])
tpos_ext.include_router(tpos_generic_router)
tpos_ext.include_router(tpos_lnurl_router)
tpos_ext.include_router(tpos_api_router)
tpos_ext.include_router(tpos_atm_router)

tpos_static_files = [
    {
        "path": "/tpos/static",
        "name": "tpos_static",
    }
]

scheduled_tasks: list[asyncio.Task] = []


def tpos_stop():
    for task in scheduled_tasks:
        try:
            task.cancel()
        except Exception as ex:
            logger.warning(ex)


def tpos_start():
    from lnbits.tasks import create_permanent_unique_task

    invoice_task = create_permanent_unique_task("ext_tpos", wait_for_paid_invoices)
    onchain_task = create_permanent_unique_task(
        "ext_tpos_onchain", poll_onchain_payments
    )
    scheduled_tasks.extend([invoice_task, onchain_task])


__all__ = [
    "db",
    "tpos_ext",
    "tpos_start",
    "tpos_static_files",
    "tpos_stop",
]
