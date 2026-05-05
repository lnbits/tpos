import asyncio

import pytest
from httpx import AsyncClient
from lnbits.core.crud import get_standalone_payment
from lnbits.core.services import pay_invoice, update_wallet_balance
from lnbits.core.services.users import create_user_account_no_ckeck
from lnbits.tasks import internal_invoice_queue

from tpos.crud import get_tpos_payment_by_hash  # type: ignore[import]
from tpos.tasks import on_invoice_paid  # type: ignore[import]


async def _drain_internal_invoice_queue() -> None:
    while True:
        try:
            internal_invoice_queue.get_nowait()
        except asyncio.QueueEmpty:
            return


@pytest.mark.asyncio
async def test_tpos_invoice_can_be_paid_through_api_flow(client: AsyncClient):
    await _drain_internal_invoice_queue()
    user = await create_user_account_no_ckeck()
    wallet = user.wallets[0]
    headers = {"X-API-KEY": wallet.adminkey}

    create_tpos_response = await client.post(
        "/tpos/api/v1/tposs",
        json={
            "wallet": wallet.id,
            "name": "Main Bar",
            "currency": "sats",
            "business_name": None,
            "business_address": None,
            "business_vat_id": None,
        },
        headers=headers,
    )
    assert create_tpos_response.status_code == 201
    tpos = create_tpos_response.json()

    invoice_response = await client.post(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices",
        json={"amount": 21, "memo": "Table 4"},
    )
    assert invoice_response.status_code == 201
    invoice = invoice_response.json()
    assert invoice["payment_hash"]
    assert invoice["bolt11"]
    assert invoice["payment_request"].startswith("lightning:")

    check_response = await client.get(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}"
    )
    assert check_response.status_code == 200
    assert check_response.json() == {"paid": False}

    await update_wallet_balance(wallet, 100)
    await _drain_internal_invoice_queue()
    await pay_invoice(wallet_id=wallet.id, payment_request=invoice["bolt11"])
    await _drain_internal_invoice_queue()

    payment = await get_standalone_payment(invoice["payment_hash"], incoming=True)
    assert payment is not None
    await on_invoice_paid(payment)

    paid_response = await client.get(
        f"/tpos/api/v1/tposs/{tpos['id']}/invoices/{invoice['payment_hash']}"
    )
    assert paid_response.status_code == 200
    assert paid_response.json() == {"paid": True}

    tpos_payment = await get_tpos_payment_by_hash(invoice["payment_hash"])
    assert tpos_payment is not None
    assert tpos_payment.paid is True
    assert tpos_payment.payment_method == "lightning"

    latest_response = await client.get(f"/tpos/api/v1/tposs/{tpos['id']}/invoices")
    assert latest_response.status_code == 200
    latest = latest_response.json()
    assert len(latest) == 1
    assert latest[0]["pending"] is False
    assert latest[0]["payment_method"] == "lightning"
