import os
from typing import Any, cast

import pytest_asyncio
import tabs.migrations as tabs_migrations  # type: ignore[import]
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from lnbits.core import migrations as core_migrations  # type: ignore[import]
from lnbits.core.crud.extensions import create_installed_extension
from lnbits.core.db import db as core_db
from lnbits.core.helpers import run_migration
from lnbits.core.models.extensions import InstallableExtension
from lnbits.settings import settings
from tabs import tabs_ext  # type: ignore[import]
from tabs.crud import db as tabs_db  # type: ignore[import]

import tpos.migrations as ext_migrations  # type: ignore[import]
import tpos.services as tpos_services  # type: ignore[import]
from tpos import tpos_ext  # type: ignore[import]
from tpos.crud import db  # type: ignore[import]


@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_ext():
    if os.path.isfile(core_db.path):
        os.remove(core_db.path)
    async with core_db.connect() as conn:
        await run_migration(conn, core_migrations, "core")
        await create_installed_extension(
            InstallableExtension(
                id="tabs",
                name="Tabs",
                version="0.0.0",
                active=True,
            ),
            conn=conn,
        )
    settings.lnbits_installed_extensions_ids.add("tabs")

    if os.path.isfile(db.path):
        os.remove(db.path)
    async with db.connect() as conn:
        await run_migration(conn, ext_migrations, "tpos")

    if os.path.isfile(tabs_db.path):
        os.remove(tabs_db.path)
    async with tabs_db.connect() as conn:
        await run_migration(conn, tabs_migrations, "tabs")


@pytest_asyncio.fixture
async def client(monkeypatch):
    app = FastAPI()
    app.include_router(tpos_ext)
    app.include_router(tabs_ext)
    transport = ASGITransport(app=cast(Any, app))

    def app_client(*args, **kwargs):
        kwargs["transport"] = transport
        kwargs.setdefault("base_url", "http://testserver")
        return AsyncClient(*args, **kwargs)

    monkeypatch.setattr(tpos_services.httpx, "AsyncClient", app_client)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
