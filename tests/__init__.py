try:
    from lnbits.tasks import internal_invoice_queue  # type: ignore[attr-defined]
except ImportError:
    from lnbits.task_manager import task_manager

    internal_invoice_queue = task_manager.internal_invoice_queue
