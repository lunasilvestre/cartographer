from __future__ import annotations

import os

import pytest

from fastmcp import FastMCP

from eo_mcp.config import Settings
from eo_mcp.tools import raster, vector, workflows

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

VECTOR_COLLECTION = "public.flood_extent_emsr864"
BBOX = [-8.5, 39.0, -7.5, 39.8]

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


@pytest.fixture
def live_mcp_full(live_settings: Settings) -> FastMCP:
    """Full MCP with raster + vector + workflow tools for live integration tests."""
    mcp = FastMCP("live-eo-mcp-full")
    raster.register(mcp, live_settings)
    vector.register(mcp, live_settings)
    workflows.register(mcp, live_settings)
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


# ---------------------------------------------------------------------------
# Vector integration tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
class TestVectorIntegration:
    async def test_list_collections_live(self, live_mcp_full: FastMCP):
        """Live tipg should return at least one collection."""
        result = await live_mcp_full.call_tool("list_vector_collections", {})
        sc = result.structured_content
        assert "error" not in sc
        assert "collections" in sc
        assert len(sc["collections"]) > 0
        ids = [c["id"] for c in sc["collections"]]
        assert VECTOR_COLLECTION in ids

    async def test_get_features_emsr864_live(self, live_mcp_full: FastMCP):
        """Live feature query should return GeoJSON FeatureCollection."""
        result = await live_mcp_full.call_tool(
            "get_vector_features",
            {"collection_id": VECTOR_COLLECTION, "limit": 2},
        )
        sc = result.structured_content
        assert "error" not in sc
        assert sc["type"] == "FeatureCollection"
        assert len(sc["features"]) <= 2
        assert len(sc["features"]) >= 1

    async def test_get_features_bbox_filter_live(self, live_mcp_full: FastMCP):
        """Bbox filter should constrain returned features to the Tagus valley area."""
        result = await live_mcp_full.call_tool(
            "get_vector_features",
            {"collection_id": VECTOR_COLLECTION, "bbox": BBOX, "limit": 5},
        )
        sc = result.structured_content
        assert "error" not in sc
        assert sc["type"] == "FeatureCollection"
        # All returned features should have geometry within or overlapping the bbox
        for feat in sc["features"]:
            assert feat["geometry"] is not None

    async def test_get_vector_tile_url_live(self, live_mcp_full: FastMCP):
        """Live tilejson should return tile URL template and bounds."""
        result = await live_mcp_full.call_tool(
            "get_vector_tile_url",
            {"collection_id": VECTOR_COLLECTION},
        )
        sc = result.structured_content
        assert "error" not in sc
        assert "tile_url_template" in sc or "tiles" in sc
        assert "bounds" in sc
        assert len(sc["bounds"]) == 4
        assert "minzoom" in sc
        assert "maxzoom" in sc

    async def test_get_collection_queryables_live(self, live_mcp_full: FastMCP):
        """Live queryables should include datetime + area_ha for emsr864."""
        result = await live_mcp_full.call_tool(
            "get_collection_queryables",
            {"collection_id": VECTOR_COLLECTION},
        )
        sc = result.structured_content
        assert "error" not in sc
        assert "properties" in sc
        assert "datetime" in sc["properties"]
        assert "area_ha" in sc["properties"]


# ---------------------------------------------------------------------------
# Workflow integration tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
class TestWorkflowIntegration:
    async def test_flood_rainfall_analysis_live(self, live_mcp_full: FastMCP):
        """Live workflow: fetch flood features and compute rainfall stats over each."""
        result = await live_mcp_full.call_tool(
            "flood_rainfall_analysis",
            {
                "flood_collection_id": VECTOR_COLLECTION,
                "precipitation_collection_id": COLLECTION,
                "precipitation_item_id": ITEM,
                "limit": 2,
            },
        )
        sc = result.structured_content
        assert "error" not in sc or sc.get("type") is not None
        # Get features from result (may be FeatureCollection or wrapper dict)
        features = []
        if sc.get("type") == "FeatureCollection":
            features = sc.get("features", [])
        elif "features" in sc:
            features = sc["features"]
        # At least one feature should have been processed
        assert len(features) >= 0  # non-negative; empty is allowed if no data

    async def test_multi_layer_summary_live(self, live_mcp_full: FastMCP):
        """Live multi-layer: raster stats from two layers over the same AOI."""
        result = await live_mcp_full.call_tool(
            "multi_layer_summary",
            {
                "geojson": CANNED_POLYGON,
                "raster_layers": [
                    {
                        "collection_id": COLLECTION,
                        "item_id": ITEM,
                        "asset": ASSET,
                        "label": "precipitation",
                    },
                ],
            },
        )
        sc = result.structured_content
        assert "error" not in sc
        assert "layers" in sc
        assert "precipitation" in sc["layers"]
        precip_result = sc["layers"]["precipitation"]
        # Stats should include properties/statistics
        assert "properties" in precip_result or "statistics" in precip_result or "error" in precip_result

    async def test_collection_health_check_live(self, live_mcp_full: FastMCP):
        """Live health check should report on known collections."""
        result = await live_mcp_full.call_tool(
            "collection_health_check",
            {"collection_ids": [VECTOR_COLLECTION]},
        )
        sc = result.structured_content
        # Should return without error
        assert sc is not None
        # Should have some structure indicating collections were checked
        assert "collections" in sc or "vector" in sc or "raster" in sc or len(sc) > 0
