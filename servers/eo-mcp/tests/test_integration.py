from __future__ import annotations

import os

import pytest

from fastmcp import FastMCP

from eo_mcp.config import Settings
from eo_mcp.tools import raster

pytestmark = [
    pytest.mark.slow,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("EO_MCP_SKIP_INTEGRATION") == "1",
        reason="integration skipped",
    ),
]

COLLECTION = "precipitation-daily"
ITEM = "precipitation-storm-total"
ASSET = "data"

CANNED_POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [-8.2, 39.3],
        [-7.8, 39.3],
        [-7.8, 39.7],
        [-8.2, 39.7],
        [-8.2, 39.3],
    ]],
}


@pytest.fixture
def live_settings() -> Settings:
    return Settings()


@pytest.fixture
def live_mcp(live_settings: Settings) -> FastMCP:
    mcp = FastMCP("live-eo-mcp")
    raster.register(mcp, live_settings)
    return mcp


async def test_live_raster_info(live_mcp: FastMCP):
    result = await live_mcp.call_tool("get_raster_info", {"collection_id": COLLECTION, "item_id": ITEM, "asset": ASSET})
    sc = result.structured_content
    assert sc[ASSET]["dtype"] == "float32"
    assert sc[ASSET]["count"] == 1


async def test_live_tile_url(live_mcp: FastMCP):
    result = await live_mcp.call_tool("get_tile_url", {"collection_id": COLLECTION, "item_id": ITEM, "asset": ASSET})
    sc = result.structured_content
    assert "WebMercatorQuad" in sc["tile_url_template"]
    assert len(sc["bounds"]) == 4


async def test_live_zonal_statistics(live_mcp: FastMCP):
    result = await live_mcp.call_tool(
        "get_zonal_statistics",
        {"collection_id": COLLECTION, "item_id": ITEM, "geojson": CANNED_POLYGON, "asset": ASSET},
    )
    sc = result.structured_content
    stats = sc["properties"]["statistics"]["data_b1"]
    assert stats["count"] > 0
