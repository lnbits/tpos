from asyncio import Queue

from lnbits import tasks

try:
    internal_invoice_queue: Queue = tasks.internal_invoice_queue  # type: ignore[attr-defined]
except AttributeError:
    from lnbits.task_manager import task_manager

    internal_invoice_queue = task_manager.internal_invoice_queue
