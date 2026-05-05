import os
from typing import Any, cast

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from lnbits.core import migrations as core_migrations  # type: ignore[import]
from lnbits.core.db import db as core_db
from lnbits.core.helpers import run_migration

import tpos.migrations as ext_migrations  # type: ignore[import]
from tpos import tpos_ext  # type: ignore[import]
from tpos.crud import db  # type: ignore[import]


@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_ext():
    if os.path.isfile(core_db.path):
        os.remove(core_db.path)
    async with core_db.connect() as conn:
        await run_migration(conn, core_migrations, "core")

    if os.path.isfile(db.path):
        os.remove(db.path)
    async with db.connect() as conn:
        await run_migration(conn, ext_migrations, "tpos")


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    app.include_router(tpos_ext)
    transport = ASGITransport(app=cast(Any, app))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
