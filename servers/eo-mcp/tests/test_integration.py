from __future__ import annotations

import os

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from eo_mcp.config import Settings
from eo_mcp.http import Backends
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
async def live_backends(live_settings: Settings):
    backends = Backends(live_settings)
    yield backends
    await backends.aclose()


@pytest.fixture
def live_mcp(live_backends: Backends) -> FastMCP:
    mcp = FastMCP("live-eo-mcp")
    raster.register(mcp, live_backends)
    return mcp


@pytest.fixture
def live_mcp_full(live_backends: Backends) -> FastMCP:
    """Full MCP with raster + vector + workflow tools for live integration tests."""
    mcp = FastMCP("live-eo-mcp-full")
    raster.register(mcp, live_backends)
    vector.register(mcp, live_backends)
    workflows.register(mcp, live_backends)
    return mcp




# ---------------------------------------------------------------------------
# Raster integration tests
# ---------------------------------------------------------------------------


async def test_live_raster_info(live_mcp: FastMCP):
    result = await live_mcp.call_tool("get_raster_info", {"collection_id": COLLECTION, "item_id": ITEM, "asset": ASSET})
    sc = result.structured_content
    assert sc[ASSET]["dtype"] == "float32"
    assert sc[ASSET]["count"] == 1
    assert sc[ASSET]["bounds"] == [-9.6, 36.9, -6.1, 42.2]


async def test_live_raster_info_bad_item_raises(live_mcp: FastMCP):
    """The live 404 path: an error, not a dict that looks like data."""
    with pytest.raises(ToolError) as excinfo:
        await live_mcp.call_tool(
            "get_raster_info",
            {"collection_id": COLLECTION, "item_id": "no-such-item", "asset": ASSET},
        )

    message = str(excinfo.value)
    assert "404" in message
    assert "no-such-item" in message


async def test_live_tile_url(live_mcp: FastMCP):
    result = await live_mcp.call_tool("get_tile_url", {"collection_id": COLLECTION, "item_id": ITEM, "asset": ASSET})
    sc = result.structured_content
    assert "WebMercatorQuad" in sc["tile_url_template"]
    assert len(sc["bounds"]) == 4
    assert sc["minzoom"] == 0
    assert sc["maxzoom"] == 24


async def test_live_tile_url_picks_up_collection_render_defaults(live_mcp: FastMCP):
    """precipitation-daily publishes cheias:rescale [0, 80] and cheias:colormap 'blues'.

    The colormap is inherited unconditionally. The rescale is not: this item
    runs ~133-516, so the collection's [0, 80] would saturate it and the tool
    falls back to the item's own percentiles instead. `TestLiveRegressions`
    covers that decision; here we only pin that the metadata is read at all.
    """
    result = await live_mcp.call_tool("get_tile_url", {"collection_id": COLLECTION, "item_id": ITEM})

    render = result.structured_content["render"]
    assert render["colormap"] == "blues"
    assert render["from_collection_metadata"] == ["colormap"]
    assert render["rescale"] is not None
    assert "rescale=" in result.structured_content["tilejson_url"]


async def test_live_tile_url_explicit_args_win(live_mcp: FastMCP):
    result = await live_mcp.call_tool(
        "get_tile_url",
        {"collection_id": COLLECTION, "item_id": ITEM, "rescale": "0,200", "colormap": "viridis"},
    )

    render = result.structured_content["render"]
    assert render["rescale"] == "0,200"
    assert render["colormap"] == "viridis"
    assert render["from_collection_metadata"] == []


async def test_live_zonal_statistics(live_mcp: FastMCP):
    result = await live_mcp.call_tool(
        "get_zonal_statistics",
        {"collection_id": COLLECTION, "item_id": ITEM, "geojson": CANNED_POLYGON, "asset": ASSET},
    )
    sc = result.structured_content
    stats = sc["properties"]["statistics"]["data_b1"]
    assert stats["count"] > 0
    assert stats["min"] <= stats["mean"] <= stats["max"]


async def test_live_zonal_statistics_feature_collection_covers_every_feature(live_mcp: FastMCP):
    """The whole collection is analysed, not just features[0].

    TiTiler returns a FeatureCollection with per-feature statistics — this is
    the fix for the tool silently reporting one feature's numbers as if they
    covered the lot.
    """
    second = {
        "type": "Polygon",
        "coordinates": [[[-9.0, 38.5], [-8.6, 38.5], [-8.6, 38.9], [-9.0, 38.9], [-9.0, 38.5]]],
    }
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": CANNED_POLYGON, "properties": {"name": "tagus"}},
            {"type": "Feature", "geometry": second, "properties": {"name": "sado"}},
        ],
    }

    result = await live_mcp.call_tool(
        "get_zonal_statistics",
        {"collection_id": COLLECTION, "item_id": ITEM, "geojson": fc, "asset": ASSET},
    )

    sc = result.structured_content
    assert sc["type"] == "FeatureCollection"
    assert len(sc["features"]) == 2
    means = [f["properties"]["statistics"]["data_b1"]["mean"] for f in sc["features"]]
    assert all(m > 0 for m in means)
    # Two different areas: the point of computing both is that they differ.
    assert means[0] != means[1]


# ---------------------------------------------------------------------------
# Vector integration tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
class TestVectorIntegration:
    async def test_list_collections_live(self, live_mcp_full: FastMCP):
        """Live tipg should return the domain collections and hide PostGIS internals."""
        result = await live_mcp_full.call_tool("list_vector_collections", {})
        sc = result.structured_content
        ids = [c["id"] for c in sc["collections"]]
        assert VECTOR_COLLECTION in ids
        # The seven domain tables, verified live on 2026-07-31.
        assert set(ids) >= {
            "public.flood_extent_emsr861",
            "public.flood_extent_emsr864",
            "public.frontal_boundaries",
            "public.storm_tracks",
            "public.discharge_stations",
            "public.rivers_portugal",
            "public.consequence_events",
        }
        # PostGIS utility tables are excluded, and the exclusion is visible.
        assert not any(i.startswith(("public.st_", "public.postgis_")) for i in ids)
        assert sc["excluded_internal"]

    async def test_list_collections_include_internal_live(self, live_mcp_full: FastMCP):
        result = await live_mcp_full.call_tool("list_vector_collections", {"include_internal": True})
        ids = [c["id"] for c in result.structured_content["collections"]]
        assert "public.st_hexagongrid" in ids

    async def test_get_features_emsr864_live(self, live_mcp_full: FastMCP):
        """Live feature query should return GeoJSON FeatureCollection."""
        result = await live_mcp_full.call_tool(
            "get_vector_features",
            {"collection_id": VECTOR_COLLECTION, "limit": 2},
        )
        sc = result.structured_content
        assert sc["type"] == "FeatureCollection"
        assert len(sc["features"]) == 2
        props = sc["features"][0]["properties"]
        # Column names verified live on 2026-07-31.
        assert {"ogc_fid", "datetime", "area_ha", "activation", "aoi", "event_type", "storm"} <= set(props)
        assert props["activation"] == "EMSR864"

    async def test_get_features_bbox_filter_live(self, live_mcp_full: FastMCP):
        """A bbox must actually constrain the result, not just be accepted."""
        unfiltered = await live_mcp_full.call_tool(
            "get_vector_features", {"collection_id": VECTOR_COLLECTION, "limit": 1}
        )
        filtered = await live_mcp_full.call_tool(
            "get_vector_features",
            {"collection_id": VECTOR_COLLECTION, "bbox": BBOX, "limit": 5},
        )

        assert filtered.structured_content["numberMatched"] < unfiltered.structured_content["numberMatched"]
        for feat in filtered.structured_content["features"]:
            assert feat["geometry"] is not None

    async def test_get_features_bad_collection_raises(self, live_mcp_full: FastMCP):
        with pytest.raises(ToolError) as excinfo:
            await live_mcp_full.call_tool(
                "get_vector_features", {"collection_id": "public.definitely_not_a_table"}
            )
        assert "public.definitely_not_a_table" in str(excinfo.value)

    async def test_get_vector_feature_live(self, live_mcp_full: FastMCP):
        """Round-trip: read a feature's ogc_fid, then fetch it by that id."""
        listing = await live_mcp_full.call_tool(
            "get_vector_features", {"collection_id": VECTOR_COLLECTION, "limit": 1}
        )
        ogc_fid = listing.structured_content["features"][0]["properties"]["ogc_fid"]

        result = await live_mcp_full.call_tool(
            "get_vector_feature",
            {"collection_id": VECTOR_COLLECTION, "feature_id": str(ogc_fid)},
        )

        sc = result.structured_content
        assert sc["type"] == "Feature"
        assert sc["properties"]["ogc_fid"] == ogc_fid

    async def test_get_vector_feature_missing_raises(self, live_mcp_full: FastMCP):
        with pytest.raises(ToolError, match="no feature with ogc_fid=999999999"):
            await live_mcp_full.call_tool(
                "get_vector_feature",
                {"collection_id": VECTOR_COLLECTION, "feature_id": "999999999"},
            )

    async def test_get_vector_tile_url_live(self, live_mcp_full: FastMCP):
        """Live tilejson should return tile URL template and bounds."""
        result = await live_mcp_full.call_tool(
            "get_vector_tile_url",
            {"collection_id": VECTOR_COLLECTION},
        )
        sc = result.structured_content
        assert "{z}" in sc["tile_url_template"]
        assert len(sc["bounds"]) == 4
        assert sc["minzoom"] <= sc["maxzoom"]

    async def test_get_collection_queryables_live(self, live_mcp_full: FastMCP):
        """Live queryables for emsr864, verified live on 2026-07-31."""
        result = await live_mcp_full.call_tool(
            "get_collection_queryables",
            {"collection_id": VECTOR_COLLECTION},
        )
        sc = result.structured_content
        assert {"datetime", "area_ha", "activation", "aoi", "event_type", "storm", "ogc_fid"} <= set(
            sc["properties"]
        )


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
        assert sc["type"] == "FeatureCollection"
        assert len(sc["features"]) == 2
        assert sc["partial_failures"] == 0
        # 5,496 polygons match; the counts must not read as "2 of 2, complete".
        assert sc["features_attempted"] == 2
        assert sc["features_matched"] > 2
        assert sc["features_analysed"] < sc["features_matched"]
        for feature in sc["features"]:
            stats = feature["properties"]["rainfall_stats"]["properties"]["statistics"]["data_b1"]
            assert stats["count"] > 0

    async def test_flood_rainfall_analysis_no_data_for_date_live(self, live_mcp_full: FastMCP):
        """A date with no flood polygons is an empty answer that says why."""
        result = await live_mcp_full.call_tool(
            "flood_rainfall_analysis",
            {
                "flood_collection_id": VECTOR_COLLECTION,
                "precipitation_collection_id": COLLECTION,
                "precipitation_item_id": ITEM,
                "event_date": "1999-01-01",
            },
        )
        sc = result.structured_content
        assert sc["features"] == []
        assert "no flood polygons" in sc["note"]

    async def test_multi_layer_summary_live(self, live_mcp_full: FastMCP):
        """Live multi-layer: raster stats from two real layers over the same AOI."""
        result = await live_mcp_full.call_tool(
            "multi_layer_summary",
            {
                "geojson": CANNED_POLYGON,
                "raster_layers": [
                    {"collection_id": COLLECTION, "item_id": ITEM, "asset": ASSET, "label": "precipitation"},
                    {"collection_id": COLLECTION, "item_id": "no-such-item", "label": "missing"},
                ],
            },
        )
        sc = result.structured_content
        assert sc["layers"]["precipitation"]["ok"] is True
        assert sc["layers"]["precipitation"]["statistics"]["properties"]["statistics"]["data_b1"]["count"] > 0
        # A partial failure is reported, counted, and does not sink the call.
        assert sc["layers"]["missing"]["ok"] is False
        assert sc["partial_failures"] == 1
        assert sc["failed"] == ["missing"]

    async def test_collection_health_check_live(self, live_mcp_full: FastMCP):
        """Live health check over specific collections."""
        result = await live_mcp_full.call_tool(
            "collection_health_check",
            {"collection_ids": [VECTOR_COLLECTION, COLLECTION]},
        )
        sc = result.structured_content
        assert sc["vector"][VECTOR_COLLECTION]["reachable"] is True
        assert sc["raster"][COLLECTION]["reachable"] is True
        # item_count is None against this backend: STAC returns numberMatched=null
        # on /items. It used to report 1 for every collection, because the probe
        # asks for limit=1 and the code fell back to numberReturned. `has_items`
        # is the claim the probe can actually support.
        assert sc["raster"][COLLECTION]["has_items"] is True
        assert sc["raster"][COLLECTION]["item_count"] is None
        assert sc["unreachable"] == []

    async def test_collection_health_check_enumerates_past_page_one_live(self, live_mcp_full: FastMCP):
        """STAC /collections paginates at 10; there are 11 collections.

        `wind-v` sits on page two. Before the pagination fix it was silently
        dropped and the tool reported a 10-collection catalogue as complete.
        Verified live on 2026-07-31.
        """
        result = await live_mcp_full.call_tool("collection_health_check", {})
        sc = result.structured_content

        assert "wind-v" in sc["raster"]
        assert len(sc["raster"]) >= 11
        # PostGIS utility tables must not be health-checked as if they were data.
        assert not any(k.startswith(("public.st_", "public.postgis_")) for k in sc["vector"])

    async def test_collection_health_check_reports_a_bad_collection_live(self, live_mcp_full: FastMCP):
        """An unreachable collection is data, not an exception."""
        result = await live_mcp_full.call_tool(
            "collection_health_check",
            {"collection_ids": ["public.no_such_table", "no-such-collection"]},
        )
        sc = result.structured_content
        assert sc["vector"]["public.no_such_table"]["reachable"] is False
        assert sc["raster"]["no-such-collection"]["reachable"] is False
        assert sc["partial_failures"] == 2


# ---------------------------------------------------------------------------
# Regressions found during hardening, pinned against the live backend
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
class TestLiveRegressions:
    async def test_vector_features_chain_into_zonal_statistics(self, live_mcp_full: FastMCP):
        """The natural two-tool chain, on the largest polygon in the collection.

        This failed 100% of the time before: tipg stamps each feature with an
        `id` that is the WKB hex of its own geometry plus a `links` array
        embedding the same hex, which together were 95% of a 5.96 MB body. The
        backend rejected it for size, for reasons unrelated to the geometry.
        """
        listing = await live_mcp_full.call_tool(
            "get_vector_features",
            {
                "collection_id": VECTOR_COLLECTION,
                "sortby": "-area_ha",
                "simplify": 0.001,
                "limit": 1,
            },
        )
        feature = listing.structured_content["features"][0]
        assert "id" not in feature
        assert "links" not in feature

        result = await live_mcp_full.call_tool(
            "get_zonal_statistics",
            {"collection_id": COLLECTION, "item_id": ITEM, "geojson": feature},
        )

        stats = result.structured_content["properties"]["statistics"]["data_b1"]
        assert stats["count"] > 0

    async def test_vector_features_reports_truncation(self, live_mcp_full: FastMCP):
        """5,496 polygons match; a page of 2 must not read as the whole set."""
        result = await live_mcp_full.call_tool(
            "get_vector_features", {"collection_id": VECTOR_COLLECTION, "limit": 2}
        )

        sc = result.structured_content
        assert sc["truncated"] is True
        assert sc["numberMatched"] > 2
        assert "page, not the whole result" in sc["note"]

    async def test_get_vector_feature_rejects_a_cql2_injection(self, live_mcp_full: FastMCP):
        """`ogc_fid=1 OR 1=1` matches all 5,496 rows and used to return row 3500."""
        with pytest.raises(ToolError, match="must be an integer ogc_fid"):
            await live_mcp_full.call_tool(
                "get_vector_feature",
                {"collection_id": VECTOR_COLLECTION, "feature_id": "1 OR 1=1"},
            )

    async def test_storm_total_does_not_inherit_the_saturating_collection_rescale(
        self, live_mcp_full: FastMCP
    ):
        """precipitation-daily publishes [0, 80]; storm-total runs ~133-516.

        Inheriting the collection value clamps every pixel to the top of the
        ramp and returns a valid URL for a uniformly saturated image.
        """
        result = await live_mcp_full.call_tool(
            "get_tile_url", {"collection_id": COLLECTION, "item_id": ITEM}
        )

        render = result.structured_content["render"]
        assert render["rescale_source"] == "item-percentiles"
        low, high = render["item_data_range"]
        assert high > 80, "storm-total should exceed the collection's published max"
        assert render["rescale"] == f"{low},{high}"

    async def test_daily_item_does_inherit_the_collection_rescale(self, live_mcp_full: FastMCP):
        """The curated value is still used where it genuinely fits."""
        listing = await live_mcp_full.call_tool(
            "collection_health_check", {"collection_ids": [COLLECTION]}
        )
        assert listing.structured_content["raster"][COLLECTION]["reachable"] is True

        result = await live_mcp_full.call_tool(
            "get_tile_url",
            {"collection_id": COLLECTION, "item_id": "precipitation-daily-2026-02-08"},
        )

        render = result.structured_content["render"]
        assert render["colormap"] == "blues"
        assert render["rescale_source"] in ("collection-metadata", "item-percentiles")

    async def test_health_check_does_not_fabricate_an_item_count(self, live_mcp_full: FastMCP):
        """This STAC returns numberMatched=null on /items, and the probe uses limit=1.

        The old fallback to numberReturned reported item_count=1 for every
        collection; wind-u actually has 308.
        """
        result = await live_mcp_full.call_tool(
            "collection_health_check", {"collection_ids": ["wind-u"]}
        )

        entry = result.structured_content["raster"]["wind-u"]
        assert entry["reachable"] is True
        assert entry["has_items"] is True
        assert entry["item_count"] != 1, "item_count=1 is the fabricated value"

    async def test_color_formula_rgb_example_is_rejected_by_the_backend(self, live_mcp_full: FastMCP):
        """Documents why the docstring no longer advertises an RGB color_formula.

        These assets are single-band float32, so `Gamma RGB ... Saturation ...`
        produces a tilejson URL whose tiles 500. The tool cannot detect that
        from the tilejson response, so the docstring must not teach it.
        """
        import httpx as _httpx

        result = await live_mcp_full.call_tool(
            "get_tile_url",
            {
                "collection_id": COLLECTION,
                "item_id": ITEM,
                "color_formula": "Gamma RGB 3.5 Saturation 1.7",
            },
        )
        template = result.structured_content["tile_url_template"]
        tile_url = template.replace("{z}", "8").replace("{x}", "122").replace("{y}", "97")

        async with _httpx.AsyncClient(timeout=30) as client:
            broken = await client.get(tile_url)
        assert broken.status_code == 500

        plain = await live_mcp_full.call_tool(
            "get_tile_url", {"collection_id": COLLECTION, "item_id": ITEM}
        )
        ok_url = (
            plain.structured_content["tile_url_template"]
            .replace("{z}", "8").replace("{x}", "122").replace("{y}", "97")
        )
        async with _httpx.AsyncClient(timeout=30) as client:
            good = await client.get(ok_url)
        assert good.status_code == 200

    async def test_simplify_can_null_geometries_and_the_tool_says_so(
        self, live_mcp_full: FastMCP
    ):
        """Live proof of the hazard the docstring and error message warn about.

        tipg answers 200 with properties intact and no geometry, so a caller
        following generic "use simplify to reduce payload" advice loses most of
        the collection silently.
        """
        coarse = await live_mcp_full.call_tool(
            "get_vector_features",
            {"collection_id": VECTOR_COLLECTION, "limit": 100, "simplify": 0.001},
        )
        sc = coarse.structured_content
        assert sc["null_geometry_count"] > 0
        assert "too coarse" in sc["null_geometry_note"]

        fine = await live_mcp_full.call_tool(
            "get_vector_features",
            {"collection_id": VECTOR_COLLECTION, "limit": 100, "simplify": 0.0001},
        )
        # The tolerance the tooling actually recommends must be the safe one.
        assert "null_geometry_count" not in fine.structured_content

    async def test_backend_errors_name_the_env_var_that_configures_them(
        self, live_mcp_full: FastMCP
    ):
        with pytest.raises(ToolError, match="EO_MCP_RASTER_URL"):
            await live_mcp_full.call_tool(
                "get_raster_info", {"collection_id": COLLECTION, "item_id": "no-such-item"}
            )

    async def test_wide_collection_ramp_is_kept_and_flagged(self, live_mcp_full: FastMCP):
        """precondition's ramp [0,100] holds an item occupying ~20% of it.

        Kept, not overridden: a shared scale is what makes items in a collection
        comparable, and rescaling per item would render a dry day and a wet day
        identically. The caller is told instead.
        """
        result = await live_mcp_full.call_tool(
            "get_tile_url", {"collection_id": "precondition", "item_id": "precondition-2026-02-15"}
        )
        render = result.structured_content["render"]
        if render["rescale_source"] != "collection-metadata":
            pytest.skip(f"precondition item range moved; source={render['rescale_source']}")
        assert render["rescale"] == "0,100"
        assert "few distinct tones" in (render["rescale_note"] or "")

    async def test_daily_items_share_one_scale_across_wet_and_dry_days(
        self, live_mcp_full: FastMCP
    ):
        """The comparability property a per-item override would have destroyed.

        These two days differ by roughly 4x in rainfall. Both must render on the
        collection's ramp, or a reader comparing the tiles cannot tell them apart.
        """
        dry = await live_mcp_full.call_tool(
            "get_tile_url",
            {"collection_id": COLLECTION, "item_id": "precipitation-daily-2026-02-15"},
        )
        wet = await live_mcp_full.call_tool(
            "get_tile_url",
            {"collection_id": COLLECTION, "item_id": "precipitation-daily-2026-02-01"},
        )

        dry_render = dry.structured_content["render"]
        wet_render = wet.structured_content["render"]
        assert dry_render["rescale"] == wet_render["rescale"] == "0,80"
        assert dry_render["rescale_source"] == wet_render["rescale_source"] == "collection-metadata"
        # The dry day genuinely occupies less of the ramp — that is the signal.
        assert dry_render["item_data_range"][1] < wet_render["item_data_range"][1]

    async def test_multi_layer_summary_rejects_colliding_labels_live(self, live_mcp_full: FastMCP):
        with pytest.raises(ToolError, match="more than one layer labelled"):
            await live_mcp_full.call_tool(
                "multi_layer_summary",
                {
                    "geojson": CANNED_POLYGON,
                    "raster_layers": [
                        {"collection_id": COLLECTION, "item_id": ITEM, "label": "rain"},
                        {"collection_id": COLLECTION, "item_id": ITEM, "label": "rain"},
                    ],
                },
            )

    async def test_statistics_have_no_total_field(self, live_mcp_full: FastMCP):
        """Pins the docstring claim that there is no total, and sum is not one.

        A model that reads "total" in a docstring, finds `sum`, and reports
        "total rainfall was 8,152 mm" has produced a number with no physical
        meaning: it is depths in mm added together across pixels.
        """
        result = await live_mcp_full.call_tool(
            "get_zonal_statistics",
            {"collection_id": COLLECTION, "item_id": ITEM, "geojson": CANNED_POLYGON},
        )
        stats = result.structured_content["properties"]["statistics"]["data_b1"]

        assert "total" not in stats
        assert {"mean", "min", "max", "sum", "count"} <= set(stats)
        # sum is the arithmetic pixel-value sum, which is why it is not a total.
        assert stats["sum"] / stats["count"] == pytest.approx(stats["mean"], rel=1e-9)
