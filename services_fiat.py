from http import HTTPStatus

from fastapi import HTTPException
from lnbits.core.crud.wallets import create_wallet, get_wallet, get_wallets
from lnbits.core.models.wallets import Wallet, WalletType
from lnbits.db import Connection
from lnbits.settings import settings
from loguru import logger

from .models import Tpos


async def get_user_fiat_wallets(
    user_id: str, conn: Connection | None = None
) -> list[Wallet]:
    return await get_wallets(user_id, wallet_type=WalletType.FIAT, conn=conn)


async def find_fiat_wallet(
    user_id: str, currency: str, conn: Connection | None = None
) -> Wallet | None:
    """The user's fiat wallet in `currency`: the oldest non-deleted match."""
    currency = currency.upper()
    wallets = [
        wallet
        for wallet in await get_user_fiat_wallets(user_id, conn=conn)
        if (wallet.currency or "").upper() == currency
    ]
    if not wallets:
        return None
    wallets.sort(key=lambda wallet: (wallet.created_at, wallet.id))
    if len(wallets) > 1:
        logger.debug(
            f"tpos: {len(wallets)} fiat wallets in {currency} for {user_id}, "
            f"using {wallets[0].id}"
        )
    return wallets[0]


async def create_user_fiat_wallet(
    user_id: str,
    currency: str,
    conn: Connection | None = None,
) -> Wallet:
    """Find-or-create: idempotent, never a second wallet for a user + currency."""
    currency = currency.upper()
    existing = await find_fiat_wallet(user_id, currency, conn=conn)
    if existing:
        return existing
    try:
        return await create_wallet(
            user_id=user_id,
            # the wallet is the account's fiat wallet for that currency: every
            # extension that settles in fiat reuses it, so it is named after the
            # currency and the merchant can rename it in LNbits
            wallet_name=currency,
            wallet_type=WalletType.FIAT,
            currency=currency,
            conn=conn,
        )
    except ValueError as exc:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, f"Unsupported fiat currency {currency}."
        ) from exc


async def resolve_tpos_fiat_wallet(
    *,
    user_id: str,
    currency: str | None,
    cash_settlement: bool,
    fiat_provider: str | None,
    requested_id: str | None,
    provider_changed: bool = True,
) -> str | None:
    """The fiat wallet a TPoS must settle to, or `None` when it needs none (R1-R12)."""
    currency = (currency or "").upper()
    if currency in ("", "SATS") or not (cash_settlement or fiat_provider):
        return None

    # Card payments must be enabled for the merchant by the admin, even when the
    # merchant already owns a fiat wallet (R7c).
    if (
        fiat_provider
        and provider_changed
        and fiat_provider not in settings.get_fiat_providers_for_user(user_id)
    ):
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            "Card payments are not enabled for you. "
            "Ask your admin to enable fiat payments.",
        )

    if requested_id:
        return (await _validate_requested_wallet(requested_id, user_id, currency)).id

    wallet = await find_fiat_wallet(user_id, currency)
    if wallet:
        return wallet.id

    try:
        wallet = await create_user_fiat_wallet(user_id, currency)
    except HTTPException as exc:
        # R7b: an unchanged legacy provider TPoS keeps working on the lightning wallet.
        if fiat_provider and not cash_settlement and not provider_changed:
            logger.warning(
                f"tpos: no fiat wallet in {currency} for {user_id}, "
                "provider payments stay on the lightning wallet"
            )
            return None
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            (
                f"Cash settlement needs a fiat wallet in {currency}."
                if cash_settlement
                else f"Card payments need a fiat wallet in {currency}. "
                "Ask your admin to enable fiat payments."
            ),
        ) from exc
    return wallet.id


async def _validate_requested_wallet(
    wallet_id: str, user_id: str, currency: str
) -> Wallet:
    wallet = await get_wallet(wallet_id)
    if not wallet:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Fiat wallet not found.")
    if wallet.wallet_type != WalletType.FIAT.value:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, f"{wallet.name} is not a fiat wallet."
        )
    if wallet.user != user_id:
        raise HTTPException(HTTPStatus.FORBIDDEN, "Fiat wallet does not belong to you.")
    if (wallet.currency or "").upper() != currency:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            f"Fiat wallet currency {wallet.currency} does not match "
            f"TPoS currency {currency}.",
        )
    return wallet


async def get_valid_tpos_fiat_wallet(tpos: Tpos) -> Wallet | None:
    """The TPoS' fiat wallet, or `None` when it is gone, foreign or stale."""
    if not tpos.fiat_wallet_id:
        return None
    wallet = await get_wallet(tpos.fiat_wallet_id)
    if not wallet or wallet.wallet_type != WalletType.FIAT.value:
        return None
    if (wallet.currency or "").upper() != (tpos.currency or "").upper():
        return None
    owner = await get_wallet(tpos.wallet)
    if not owner or owner.user != wallet.user:
        return None
    return wallet
