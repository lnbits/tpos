from http import HTTPStatus

from fastapi import HTTPException
from lnbits.core.crud.wallets import create_wallet, get_wallets
from lnbits.core.models.wallets import Wallet, WalletType
from lnbits.db import Connection
from loguru import logger


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
    name: str | None = None,
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
            wallet_name=name or f"TPoS {currency}",
            wallet_type=WalletType.FIAT,
            currency=currency,
            conn=conn,
        )
    except ValueError as exc:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, f"Unsupported fiat currency {currency}."
        ) from exc
