from http import HTTPStatus

from fastapi import HTTPException
from lnbits.core.crud.wallets import get_standalone_wallet, get_wallet
from lnbits.core.models.wallets import Wallet, WalletType
from lnbits.settings import settings

from .models import Tpos


async def resolve_tpos_fiat_wallet(
    *,
    user_id: str,
    currency: str,
    cash_settlement: bool,
    fiat_provider: str | None,
    requested_id: str | None,
    provider_changed: bool = True,
) -> str | None:
    if currency.lower() == "sats" or not (cash_settlement or fiat_provider):
        return None
    if (
        fiat_provider
        and provider_changed
        and fiat_provider not in settings.get_fiat_providers_for_user(user_id)
    ):
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, "Card payments are not enabled for you."
        )
    if not requested_id:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, f"Select a fiat wallet in {currency}."
        )
    wallet = await get_wallet(requested_id)
    if wallet and wallet.user != user_id:
        raise HTTPException(HTTPStatus.FORBIDDEN, "Fiat wallet does not belong to you.")
    if not _matches_currency(wallet, currency):
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, f"Select an active fiat wallet in {currency}."
        )
    return requested_id


async def get_valid_tpos_fiat_wallet(
    tpos: Tpos, wallet_id: str | None, currency: str
) -> Wallet | None:
    if not wallet_id:
        return None
    wallet = await get_wallet(wallet_id)
    if not wallet or not _matches_currency(wallet, currency):
        return None
    owner_wallet = await get_standalone_wallet(tpos.wallet)
    if not owner_wallet or owner_wallet.user != wallet.user:
        return None
    return wallet


def _matches_currency(wallet: Wallet | None, currency: str) -> bool:
    return bool(
        wallet
        and wallet.wallet_type == WalletType.FIAT.value
        and (wallet.currency or "").upper() == currency.upper()
    )
