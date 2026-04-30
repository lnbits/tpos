import asyncio
import os

import pytest

os.environ.setdefault("DEBUG", "false")

from lnbits.core.db import db as core_db
from lnbits.core.helpers import run_migration

import tpos.migrations as ext_migrations  # type: ignore[import]
from tpos.crud import db  # type: ignore[import]


@pytest.fixture(scope="session", autouse=True)
def init_ext():
    async def _init():
        async with core_db.connect() as core_conn:
            await core_conn.execute("""
                CREATE TABLE IF NOT EXISTS dbversions (
                    db TEXT PRIMARY KEY,
                    version INTEGER NOT NULL
                );
                """)
            await core_conn.execute("DELETE FROM dbversions WHERE db = 'tpos'")
        async with db.connect() as conn:
            for table in ("payments", "withdraws", "pos", "tposs"):
                await conn.execute(f"DROP TABLE IF EXISTS tpos.{table}")
            await run_migration(conn, ext_migrations, "tpos")

    asyncio.run(_init())
