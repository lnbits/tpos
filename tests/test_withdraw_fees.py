from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from lnbits.core.models import Payment, PaymentState
from lnurl import LnurlErrorResponse, LnurlSuccessResponse

from .. import views_lnurl as views


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["success", "pending", "failed", "exception"])
async def test_tribute_only_charged_for_confirmed_withdrawal(monkeypatch, outcome):
    charge = SimpleNamespace(id="charge", tpos_id="tpos", amount=100, claimed=False)
    tpos = SimpleNamespace(
        wallet="wallet",
        can_withdraw=True,
        withdraw_maximum=100,
        withdrawn_amount=0,
        withdraw_time=0,
    )
    monkeypatch.setattr(views, "get_lnurlcharge", AsyncMock(return_value=charge))
    monkeypatch.setattr(views, "get_tpos", AsyncMock(return_value=tpos))
    monkeypatch.setattr(views, "update_lnurlcharge", AsyncMock())
    monkeypatch.setattr(views, "update_tpos", AsyncMock())
    payment = Payment(
        checking_id="withdrawal",
        payment_hash="a" * 64,
        wallet_id="wallet",
        amount=-100000,
        fee=-1000,
        bolt11="test-invoice",
        status=PaymentState.PENDING if outcome == "exception" else outcome,
    )
    pay = AsyncMock(return_value=payment)
    if outcome == "exception":
        pay.side_effect = RuntimeError("sensitive backend details")
    monkeypatch.setattr(views, "pay_invoice", pay)
    tribute = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(views, "pay_tribute", tribute)
    monkeypatch.setattr(views, "websocket_updater", notify)

    response = await views.lnurl_callback(pr="test-invoice", k1="charge")
    if outcome == "success":
        assert isinstance(response, LnurlSuccessResponse)
        tribute.assert_awaited_once_with(withdraw_amount=100, wallet_id="wallet")
        notify.assert_awaited_once_with("charge", "paid")
    else:
        tribute.assert_not_awaited()
        notify.assert_not_awaited()
        if outcome == "pending":
            assert isinstance(response, LnurlSuccessResponse)
            payment.status = PaymentState.FAILED
        else:
            assert isinstance(response, LnurlErrorResponse)
            assert "sensitive backend" not in response.reason

    # Retrying the callback cannot charge tribute again or spend a second time,
    # including after a previously pending withdrawal fails.
    retry = await views.lnurl_callback(pr="test-invoice", k1="charge")
    assert isinstance(retry, LnurlErrorResponse)
    assert pay.await_count == 1
    assert tribute.await_count == (1 if outcome == "success" else 0)
