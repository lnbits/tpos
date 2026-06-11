import pytest
from fastapi import APIRouter

from .. import tpos_ext, tpos_redirect_paths


# just import router and add it to a test router
@pytest.mark.asyncio
async def test_router():
    router = APIRouter()
    router.include_router(tpos_ext)


def test_assetlinks_redirect_path():
    assert {
        "from_path": "/.well-known/assetlinks.json",
        "redirect_to_path": "/api/v1/well-known/assetlinks.json",
    } in tpos_redirect_paths
