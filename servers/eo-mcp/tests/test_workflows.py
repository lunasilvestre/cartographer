"""Unit tests for the workflow tools (v0.4).

Each workflow tool chains raster + vector + STAC calls. These tests patch
``httpx.AsyncClient.send`` rather than ``.request`` so the router can tell the
three backends apart by absolute URL — ``/collections`` exists on both STAC and
tipg, so the relative path is not enough.

The workflow tools have a deliberately different error contract from the other
eight: a per-item failure is reported in band alongside the items that
succeeded, with an explicit ``partial_failures`` count, and only a total failure
raises. These tests pin both halves of that.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .conftest import make_response

# Base URLs from settings_fx. The STAC backend is the bare host, so it is
# identified by *not* being one of the other two.
RASTER_PREFIX = "https://fake.example/raster"
VECTOR_PREFIX = "https://fake.example/vector"


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_FLOOD_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [[-8.0, 39.0], [-7.5, 39.0], [-7.5, 39.5], [-8.0, 39.5], [-8.0, 39.0]]
    ],
}

_FLOOD_FEATURE_A = {
    "type": "Feature",
    "id": "feat-001",
    "geometry": _FLOOD_POLYGON,
    "properties": {"datetime": "2026-02-08T00:00:00+00:00", "area_ha": 150.0},
}

_FLOOD_FEATURE_B = {
    "type": "Feature",
    "id": "feat-002",
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [[-7.8, 39.1], [-7.3, 39.1], [-7.3, 39.6], [-7.8, 39.6], [-7.8, 39.1]]
        ],
    },
    "properties": {"datetime": "2026-02-08T00:00:00+00:00", "area_ha": 80.0},
}

_FEATURES_BODY = {
    "type": "FeatureCollection",
    "features": [_FLOOD_FEATURE_A, _FLOOD_FEATURE_B],
    "numberMatched": 2,
    "numberReturned": 2,
}

_STATS_FEATURE = {
    "type": "Feature",
    "geometry": _FLOOD_POLYGON,
    "properties": {
        "statistics": {
            "data_b1": {
                "min": 10.0,
                "max": 80.0,
                "mean": 42.5,
                "count": 200.0,
                "sum": 8500.0,
            }
        }
    },
}

_STAC_COLLECTIONS = {
    "collections": [
        {"id": "precipitation-daily", "title": "Daily Precipitation"},
        {"id": "soil-moisture-daily", "title": "Daily Soil Moisture"},
    ],
    "links": [{"rel": "self", "href": "https://fake.example/collections"}],
}

_VECTOR_COLLECTIONS = {
    "collections": [
        {
            "id": "public.flood_extent_emsr864",
            "title": "EMSR864 Flood Extent",
            "extent": {
                "spatial": {"bbox": [[-9.0, 38.0, -6.0, 41.0]]},
                "temporal": {"interval": [["2026-02-01T00:00:00Z", None]]},
            },
        },
        # PostGIS utility table: must not be probed as if it were data.
        {"id": "public.st_hexagongrid", "title": "public.st_hexagongrid"},
    ],
    "links": [{"rel": "self", "href": "https://fake.example/vector/collections"}],
}

_COLLECTION_META = {
    "id": "public.flood_extent_emsr864",
    "title": "EMSR864 Flood Extent",
    "extent": {
        "spatial": {"bbox": [[-9.0, 38.0, -6.0, 41.0]]},
        "temporal": {"interval": [["2026-02-01T00:00:00Z", None]]},
    },
}

_STAC_ITEMS = {"features": [{"id": "precipitation-storm-total"}], "numberMatched": 1}


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


def backend_of(request: httpx.Request) -> str:
    url = str(request.url)
    if url.startswith(RASTER_PREFIX):
        return "raster"
    if url.startswith(VECTOR_PREFIX):
        return "vector"
    return "stac"


def flood_router(
    *,
    features=_FEATURES_BODY,
    stats_statuses: list[int] | None = None,
    vector_status: int = 200,
):
    """Route the vector feature query and the per-feature statistics POSTs.

    ``stats_statuses`` supplies a status per statistics call, in order, so a
    test can make exactly one of them fail.
    """
    remaining = list(stats_statuses or [])

    def _router(request: httpx.Request, **kw):
        if backend_of(request) == "vector":
            if vector_status >= 400:
                return make_response(request, vector_status, text="tipg unavailable")
            return make_response(request, 200, features)
        status = remaining.pop(0) if remaining else 200
        if status >= 400:
            return make_response(request, status, text="raster out of extent")
        return make_response(request, 200, _STATS_FEATURE)

    return _router


# ---------------------------------------------------------------------------
# flood_rainfall_analysis
# ---------------------------------------------------------------------------


async def test_flood_rainfall_analysis_happy(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """Happy path: vector features fetched, rainfall stats computed per feature."""
    mock_httpx_send.side_effect = flood_router()

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis",
        {
            "flood_collection_id": "public.flood_extent_emsr864",
            "precipitation_collection_id": "precipitation-daily",
            "precipitation_item_id": "precipitation-storm-total",
        },
    )

    sc = result.structured_content
    assert sc["type"] == "FeatureCollection"
    assert len(sc["features"]) == 2
    assert all("rainfall_stats" in f["properties"] for f in sc["features"])
    assert sc["partial_failures"] == 0
    assert sc["failed"] == []


async def test_flood_rainfall_analysis_partial_failure(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """One feature failing is reported in band and counted, not swallowed."""
    mock_httpx_send.side_effect = flood_router(stats_statuses=[500, 200])

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis",
        {
            "flood_collection_id": "public.flood_extent_emsr864",
            "precipitation_collection_id": "precipitation-daily",
            "precipitation_item_id": "precipitation-storm-total",
            "limit": 2,
        },
    )

    sc = result.structured_content
    props = [f["properties"] for f in sc["features"]]
    assert sum("rainfall_error" in p for p in props) == 1
    assert sum("rainfall_stats" in p for p in props) == 1
    assert sc["partial_failures"] == 1
    assert len(sc["failed"]) == 1


async def test_flood_rainfall_analysis_all_features_fail_raises(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """A result where nothing could be computed is a failure, not an empty answer."""
    mock_httpx_send.side_effect = flood_router(stats_statuses=[500, 500])

    with pytest.raises(ToolError, match="failed for all 2 flood polygons"):
        await mcp_full_fx.call_tool(
            "flood_rainfall_analysis",
            {
                "flood_collection_id": "public.flood_extent_emsr864",
                "precipitation_collection_id": "precipitation-daily",
                "precipitation_item_id": "precipitation-storm-total",
            },
        )


async def test_flood_rainfall_analysis_empty_features(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """No flood polygons is a real, empty answer — and says so."""
    empty = {"type": "FeatureCollection", "features": [], "numberMatched": 0}
    mock_httpx_send.side_effect = flood_router(features=empty)

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis",
        {
            "flood_collection_id": "public.flood_extent_emsr864",
            "precipitation_collection_id": "precipitation-daily",
            "precipitation_item_id": "precipitation-storm-total",
        },
    )

    sc = result.structured_content
    assert sc["features"] == []
    assert sc["partial_failures"] == 0
    assert "no flood polygons" in sc["note"]


async def test_flood_rainfall_analysis_with_event_date(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """event_date is converted into a whole-day datetime interval for tipg."""
    seen: list[str] = []

    def _router(request: httpx.Request, **kw):
        if backend_of(request) == "vector":
            seen.append(str(request.url))
            return make_response(request, 200, {**_FEATURES_BODY, "features": [_FLOOD_FEATURE_A]})
        return make_response(request, 200, _STATS_FEATURE)

    mock_httpx_send.side_effect = _router

    await mcp_full_fx.call_tool(
        "flood_rainfall_analysis",
        {
            "flood_collection_id": "public.flood_extent_emsr864",
            "precipitation_collection_id": "precipitation-daily",
            "precipitation_item_id": "precipitation-storm-total",
            "event_date": "2026-02-08",
        },
    )

    assert "2026-02-08T00%3A00%3A00Z%2F2026-02-08T23%3A59%3A59Z" in seen[0]


async def test_flood_rainfall_analysis_vector_error_raises(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """If the driving query fails there is no analysis, so the tool raises."""
    mock_httpx_send.side_effect = flood_router(vector_status=503)

    with pytest.raises(ToolError, match="503"):
        await mcp_full_fx.call_tool(
            "flood_rainfall_analysis",
            {
                "flood_collection_id": "public.flood_extent_emsr864",
                "precipitation_collection_id": "precipitation-daily",
                "precipitation_item_id": "precipitation-storm-total",
            },
        )


async def test_flood_rainfall_analysis_rejects_bad_limit(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    with pytest.raises(ToolError, match="limit must be at least 1"):
        await mcp_full_fx.call_tool("flood_rainfall_analysis", {"limit": 0})

    assert not mock_httpx_send.called


async def test_flood_rainfall_analysis_feature_no_geometry(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """A feature with geometry=None gets rainfall_error instead of crashing."""
    feature_no_geom = {"type": "Feature", "id": "feat-null-geom", "geometry": None, "properties": {"area_ha": 10}}
    good = {"type": "FeatureCollection", "features": [feature_no_geom, _FLOOD_FEATURE_A]}
    mock_httpx_send.side_effect = flood_router(features=good)

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis",
        {
            "flood_collection_id": "public.flood_extent_emsr864",
            "precipitation_collection_id": "precipitation-daily",
            "precipitation_item_id": "precipitation-storm-total",
        },
    )

    sc = result.structured_content
    assert "rainfall_error" in sc["features"][0]["properties"]
    assert sc["failed"] == ["feat-null-geom"]


async def test_flood_rainfall_analysis_unsupported_geometry_type(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """A GeometryCollection gets rainfall_error rather than crashing the run."""
    feature_geomcoll = {
        "type": "Feature",
        "id": "feat-geomcoll",
        "geometry": {"type": "GeometryCollection", "geometries": []},
        "properties": {"area_ha": 5},
    }
    body = {"type": "FeatureCollection", "features": [feature_geomcoll, _FLOOD_FEATURE_A]}
    mock_httpx_send.side_effect = flood_router(features=body)

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis",
        {
            "flood_collection_id": "public.flood_extent_emsr864",
            "precipitation_collection_id": "precipitation-daily",
            "precipitation_item_id": "precipitation-storm-total",
        },
    )

    props = result.structured_content["features"][0]["properties"]
    assert "GeometryCollection" in props["rainfall_error"]


async def test_flood_rainfall_analysis_feature_geometry_is_feature(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """A feature whose geometry field is itself a Feature is passed through."""
    inner_feature = {"type": "Feature", "geometry": _FLOOD_POLYGON, "properties": {}}
    body = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "id": "f", "geometry": inner_feature, "properties": {}}],
    }
    mock_httpx_send.side_effect = flood_router(features=body)

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis",
        {
            "flood_collection_id": "public.flood_extent_emsr864",
            "precipitation_collection_id": "precipitation-daily",
            "precipitation_item_id": "precipitation-storm-total",
        },
    )

    assert "rainfall_stats" in result.structured_content["features"][0]["properties"]


# ---------------------------------------------------------------------------
# multi_layer_summary
# ---------------------------------------------------------------------------

_AOI_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [[-8.2, 39.3], [-7.8, 39.3], [-7.8, 39.7], [-8.2, 39.7], [-8.2, 39.3]]
    ],
}

_RASTER_LAYERS = [
    {
        "collection_id": "precipitation-daily",
        "item_id": "precipitation-storm-total",
        "asset": "data",
        "label": "precipitation",
    },
    {
        "collection_id": "soil-moisture-daily",
        "item_id": "sm-composite",
        "asset": "data",
        "label": "soil_moisture",
    },
]


def stats_router(statuses: list[int] | None = None):
    remaining = list(statuses or [])

    def _router(request: httpx.Request, **kw):
        status = remaining.pop(0) if remaining else 200
        if status >= 400:
            return make_response(request, status, text="layer unavailable")
        return make_response(request, 200, _STATS_FEATURE)

    return _router


async def test_multi_layer_summary_happy(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """Each raster layer gets stats; result keyed by label."""
    mock_httpx_send.side_effect = stats_router()

    result = await mcp_full_fx.call_tool(
        "multi_layer_summary",
        {"geojson": _AOI_POLYGON, "raster_layers": _RASTER_LAYERS},
    )

    sc = result.structured_content
    assert set(sc["layers"]) == {"precipitation", "soil_moisture"}
    assert all(entry["ok"] for entry in sc["layers"].values())
    assert sc["partial_failures"] == 0
    assert sc["aoi"] == _AOI_POLYGON


async def test_multi_layer_summary_partial_failure(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """A failed layer is marked ok=false and counted, not omitted."""
    mock_httpx_send.side_effect = stats_router([500, 200])

    result = await mcp_full_fx.call_tool(
        "multi_layer_summary",
        {"geojson": _AOI_POLYGON, "raster_layers": _RASTER_LAYERS},
    )

    sc = result.structured_content
    assert sc["partial_failures"] == 1
    assert len(sc["failed"]) == 1
    failed_entry = sc["layers"][sc["failed"][0]]
    assert failed_entry["ok"] is False
    assert "500" in failed_entry["error"]


async def test_multi_layer_summary_all_layers_fail_raises(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    mock_httpx_send.side_effect = stats_router([500, 503])

    with pytest.raises(ToolError, match="failed for all 2 layer"):
        await mcp_full_fx.call_tool(
            "multi_layer_summary",
            {"geojson": _AOI_POLYGON, "raster_layers": _RASTER_LAYERS},
        )


async def test_multi_layer_summary_empty_layers_raises(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    with pytest.raises(ToolError, match="raster_layers is empty"):
        await mcp_full_fx.call_tool(
            "multi_layer_summary", {"geojson": _AOI_POLYGON, "raster_layers": []}
        )

    assert not mock_httpx_send.called


async def test_multi_layer_summary_layer_missing_item_id_raises(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    with pytest.raises(ToolError, match=r"raster_layers\[0\] is missing required item_id"):
        await mcp_full_fx.call_tool(
            "multi_layer_summary",
            {"geojson": _AOI_POLYGON, "raster_layers": [{"collection_id": "c"}]},
        )

    assert not mock_httpx_send.called


@pytest.mark.parametrize(
    "bad_geojson",
    [
        {"type": "Point", "coordinates": [-8.0, 39.0]},
        {"type": "LineString", "coordinates": [[-8.0, 39.0], [-7.5, 39.5]]},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-8.0, 39.0]}, "properties": {}},
    ],
)
async def test_multi_layer_summary_invalid_geojson(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock, bad_geojson: dict
):
    """Non-area AOIs are rejected before any HTTP call."""
    with pytest.raises(ToolError, match="(?i)polygon"):
        await mcp_full_fx.call_tool(
            "multi_layer_summary",
            {"geojson": bad_geojson, "raster_layers": _RASTER_LAYERS},
        )

    assert not mock_httpx_send.called


async def test_multi_layer_summary_feature_containing_polygon(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """Feature wrapping a Polygon should be accepted."""
    mock_httpx_send.side_effect = stats_router()
    feature_aoi = {"type": "Feature", "geometry": _AOI_POLYGON, "properties": {}}

    result = await mcp_full_fx.call_tool(
        "multi_layer_summary",
        {"geojson": feature_aoi, "raster_layers": _RASTER_LAYERS},
    )

    assert result.structured_content["partial_failures"] == 0


async def test_multi_layer_summary_label_fallback(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """When no label is provided, the key falls back to collection_id/item_id."""
    mock_httpx_send.side_effect = stats_router()

    result = await mcp_full_fx.call_tool(
        "multi_layer_summary",
        {
            "geojson": _AOI_POLYGON,
            "raster_layers": [
                {"collection_id": "precipitation-daily", "item_id": "precipitation-storm-total"}
            ],
        },
    )

    assert list(result.structured_content["layers"]) == [
        "precipitation-daily/precipitation-storm-total"
    ]


# ---------------------------------------------------------------------------
# collection_health_check
# ---------------------------------------------------------------------------


def health_router(
    *,
    stac_pages: list[dict] | None = None,
    vector_pages: list[dict] | None = None,
    stac_list_status: int = 200,
    vector_list_status: int = 200,
    probe_status: int = 200,
):
    """Route the two collection listings and the per-collection probes."""
    stac_pages = stac_pages if stac_pages is not None else [_STAC_COLLECTIONS]
    vector_pages = vector_pages if vector_pages is not None else [_VECTOR_COLLECTIONS]
    stac_seen = {"n": 0}
    vector_seen = {"n": 0}

    def _router(request: httpx.Request, **kw):
        backend = backend_of(request)
        path = request.url.path
        is_listing = path.rstrip("/").endswith("/collections")

        if backend == "vector":
            if is_listing:
                if vector_list_status >= 400:
                    return make_response(request, vector_list_status, text="tipg down")
                page = vector_pages[min(vector_seen["n"], len(vector_pages) - 1)]
                vector_seen["n"] += 1
                return make_response(request, 200, page)
            if probe_status >= 400:
                return make_response(request, probe_status, text="collection not found")
            return make_response(request, 200, _COLLECTION_META)

        if backend == "stac":
            if is_listing:
                if stac_list_status >= 400:
                    return make_response(request, stac_list_status, text="stac down")
                page = stac_pages[min(stac_seen["n"], len(stac_pages) - 1)]
                stac_seen["n"] += 1
                return make_response(request, 200, page)
            if probe_status >= 400:
                return make_response(request, probe_status, text="items not found")
            return make_response(request, 200, _STAC_ITEMS)

        return make_response(request, 200, {})

    return _router


async def test_collection_health_check_happy(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    mock_httpx_send.side_effect = health_router()

    result = await mcp_full_fx.call_tool("collection_health_check", {})

    sc = result.structured_content
    assert set(sc["raster"]) == {"precipitation-daily", "soil-moisture-daily"}
    assert set(sc["vector"]) == {"public.flood_extent_emsr864"}
    assert all(e["reachable"] for e in sc["raster"].values())
    assert sc["partial_failures"] == 0
    assert sc["unreachable"] == []


async def test_collection_health_check_skips_postgis_utility_tables(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """st_hexagongrid is in the tipg listing but is not data to health-check."""
    mock_httpx_send.side_effect = health_router()

    result = await mcp_full_fx.call_tool("collection_health_check", {})

    assert "public.st_hexagongrid" not in result.structured_content["vector"]


async def test_collection_health_check_follows_stac_pagination(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """Both backends paginate /collections at ten by default.

    Verified live on 2026-07-31: https://api.cheias.pt/collections returns 10 of
    11 collections and hides `wind-v` behind a `next` link. Reading only the
    first page reports a subset of the catalogue as if it were all of it.
    """
    page_one = {
        "collections": [{"id": f"col-{i}"} for i in range(10)],
        "links": [{"rel": "next", "href": "https://fake.example/collections?offset=10"}],
    }
    page_two = {"collections": [{"id": "wind-v"}], "links": [{"rel": "self"}]}
    mock_httpx_send.side_effect = health_router(stac_pages=[page_one, page_two])

    result = await mcp_full_fx.call_tool("collection_health_check", {})

    assert "wind-v" in result.structured_content["raster"]
    assert len(result.structured_content["raster"]) == 11


async def test_collection_health_check_pagination_is_bounded(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """A backend whose `next` link never ends must not spin forever."""
    endless = {
        "collections": [{"id": "col"}],
        "links": [{"rel": "next", "href": "https://fake.example/collections?offset=1"}],
    }
    mock_httpx_send.side_effect = health_router(stac_pages=[endless])

    result = await mcp_full_fx.call_tool("collection_health_check", {})

    listing_calls = [
        c for c in mock_httpx_send.call_args_list
        if c.args[0].url.path == "/collections"
    ]
    assert len(listing_calls) == 20  # _MAX_COLLECTION_PAGES
    assert "col" in result.structured_content["raster"]


async def test_collection_health_check_with_specific_ids(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """Dotted ids route to tipg, bare ids to STAC; no listing call is needed."""
    mock_httpx_send.side_effect = health_router()

    result = await mcp_full_fx.call_tool(
        "collection_health_check",
        {"collection_ids": ["public.flood_extent_emsr864", "precipitation-daily"]},
    )

    sc = result.structured_content
    assert sc["vector"]["public.flood_extent_emsr864"]["reachable"] is True
    assert sc["raster"]["precipitation-daily"]["reachable"] is True
    assert sc["raster"]["precipitation-daily"]["sample_item"] == "precipitation-storm-total"


async def test_collection_health_check_unreachable_collection_is_data_not_error(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """Reporting that a collection is down is what this tool is for."""
    mock_httpx_send.side_effect = health_router(probe_status=503)

    result = await mcp_full_fx.call_tool(
        "collection_health_check",
        {"collection_ids": ["public.flood_extent_emsr864", "precipitation-daily"]},
    )

    sc = result.structured_content
    assert sc["vector"]["public.flood_extent_emsr864"]["reachable"] is False
    assert sc["raster"]["precipitation-daily"]["reachable"] is False
    assert sorted(sc["unreachable"]) == ["precipitation-daily", "public.flood_extent_emsr864"]
    assert sc["partial_failures"] == 2


async def test_collection_health_check_one_backend_down(mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock):
    """One listing failing still yields a report, with the failure recorded."""
    mock_httpx_send.side_effect = health_router(stac_list_status=503)

    result = await mcp_full_fx.call_tool("collection_health_check", {})

    sc = result.structured_content
    assert sc["raster"] == {}
    assert "public.flood_extent_emsr864" in sc["vector"]
    assert "stac" in sc["enumeration_errors"]
    assert "503" in sc["enumeration_errors"]["stac"]


async def test_collection_health_check_both_backends_down_raises(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """With nothing enumerable there is no report to give, so it raises."""
    mock_httpx_send.side_effect = health_router(stac_list_status=503, vector_list_status=503)

    with pytest.raises(ToolError, match="neither backend could be enumerated"):
        await mcp_full_fx.call_tool("collection_health_check", {})


async def test_collection_health_check_connect_error_is_reported(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """A transport failure on a probe is reported per collection, not raised."""

    def _router(request: httpx.Request, **kw):
        raise httpx.ConnectError("connection refused", request=request)

    mock_httpx_send.side_effect = _router

    result = await mcp_full_fx.call_tool(
        "collection_health_check", {"collection_ids": ["precipitation-daily"]}
    )

    entry = result.structured_content["raster"]["precipitation-daily"]
    assert entry["reachable"] is False
    assert "unreachable" in entry["error"]


# ---------------------------------------------------------------------------
# Partial failure must be structurally impossible to skim past
# ---------------------------------------------------------------------------


async def test_flood_rainfall_partial_failure_is_stated_before_the_data(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """A count at the end of a long payload is a count that gets skimmed past.

    With 1 of 2 polygons failing, a model reading only the features would
    summarise "mean rainfall over the flooded area was X" — true of the one
    polygon that worked, presented as true of both.
    """
    mock_httpx_send.side_effect = flood_router(stats_statuses=[500, 200])

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis", {"flood_collection_id": "public.flood_extent_emsr864"}
    )

    sc = result.structured_content
    keys = list(sc)
    assert keys.index("partial_failures") < keys.index("features")
    assert keys.index("note") < keys.index("features")
    assert sc["features_analysed"] == 1
    assert sc["features_attempted"] == 2
    assert sc["note"].startswith("INCOMPLETE:")
    assert "rainfall_error" in sc["note"]


async def test_flood_rainfall_complete_result_has_no_incomplete_note(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    mock_httpx_send.side_effect = flood_router()

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis", {"flood_collection_id": "public.flood_extent_emsr864"}
    )

    sc = result.structured_content
    assert sc["partial_failures"] == 0
    assert "note" not in sc
    assert sc["features_analysed"] == sc["features_attempted"] == 2


async def test_flood_rainfall_flags_a_truncated_polygon_set(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """limit=5 against 5,496 matching polygons is a sample, and must say so."""
    body = {**_FEATURES_BODY, "numberMatched": 5496}
    mock_httpx_send.side_effect = flood_router(features=body)

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis", {"flood_collection_id": "public.flood_extent_emsr864"}
    )

    sc = result.structured_content
    assert sc["truncated"] is True
    assert "5496" in sc["truncation_note"]
    assert list(sc).index("truncation_note") < list(sc).index("features")


async def test_multi_layer_partial_failure_is_stated_before_the_layers(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    mock_httpx_send.side_effect = stats_router([500, 200])

    result = await mcp_full_fx.call_tool(
        "multi_layer_summary", {"geojson": _AOI_POLYGON, "raster_layers": _RASTER_LAYERS}
    )

    sc = result.structured_content
    keys = list(sc)
    assert keys.index("partial_failures") < keys.index("layers")
    assert keys.index("note") < keys.index("layers")
    assert sc["note"].startswith("INCOMPLETE:")


async def test_health_check_unreachable_is_stated_before_the_sections(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    mock_httpx_send.side_effect = health_router(probe_status=503)

    result = await mcp_full_fx.call_tool(
        "collection_health_check",
        {"collection_ids": ["public.flood_extent_emsr864", "precipitation-daily"]},
    )

    sc = result.structured_content
    keys = list(sc)
    assert keys.index("partial_failures") < keys.index("raster")
    assert keys.index("note") < keys.index("vector")


# ---------------------------------------------------------------------------
# item_count must not be fabricated
# ---------------------------------------------------------------------------


async def test_health_check_reports_null_item_count_when_backend_gives_none(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """This STAC returns numberMatched=null on /items; the probe asks for limit=1.

    Falling back to numberReturned produced `item_count: 1` for every raster
    collection — a clean integer, in a plausibly-named field, inside a
    `reachable: true` payload. wind-u really has 308 items. A null is honest.
    """

    def _router(request: httpx.Request, **kw):
        return make_response(
            request, 200, {"features": [{"id": "some-item"}], "numberMatched": None, "numberReturned": 1}
        )

    mock_httpx_send.side_effect = _router

    result = await mcp_full_fx.call_tool(
        "collection_health_check", {"collection_ids": ["wind-u"]}
    )

    entry = result.structured_content["raster"]["wind-u"]
    assert entry["reachable"] is True
    assert entry["item_count"] is None
    assert entry["has_items"] is True
    assert entry["sample_item"] == "some-item"


async def test_health_check_reports_a_real_item_count_when_available(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    def _router(request: httpx.Request, **kw):
        return make_response(
            request, 200, {"features": [{"id": "i"}], "numberMatched": 308, "numberReturned": 1}
        )

    mock_httpx_send.side_effect = _router

    result = await mcp_full_fx.call_tool(
        "collection_health_check", {"collection_ids": ["wind-u"]}
    )

    assert result.structured_content["raster"]["wind-u"]["item_count"] == 308


async def test_health_check_deduplicates_collection_ids(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    mock_httpx_send.side_effect = health_router()

    await mcp_full_fx.call_tool(
        "collection_health_check",
        {"collection_ids": ["precipitation-daily", "precipitation-daily"]},
    )

    probes = [
        c for c in mock_httpx_send.call_args_list
        if c.args[0].url.path == "/collections/precipitation-daily/items"
    ]
    assert len(probes) == 1


# ---------------------------------------------------------------------------
# Label collisions silently dropped layers
# ---------------------------------------------------------------------------


async def test_multi_layer_summary_rejects_duplicate_labels(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """Results are keyed by label, so a collision used to overwrite a layer.

    Three layers labelled "rain" returned one entry with partial_failures=0 —
    fewer layers than asked for, and nothing in the payload saying so.
    """
    layers = [
        {"collection_id": "precipitation-daily", "item_id": "storm-total", "label": "rain"},
        {"collection_id": "soil-moisture-daily", "item_id": "sm", "label": "rain"},
    ]

    with pytest.raises(ToolError, match="more than one layer labelled 'rain'"):
        await mcp_full_fx.call_tool(
            "multi_layer_summary", {"geojson": _AOI_POLYGON, "raster_layers": layers}
        )

    assert not mock_httpx_send.called


async def test_multi_layer_summary_rejects_collision_from_default_labels(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """The realistic case: two assets of one item, no labels, no user error.

    The default label is collection_id/item_id, so both layers collide.
    """
    layers = [
        {"collection_id": "precipitation-daily", "item_id": "storm-total", "asset": "data"},
        {"collection_id": "precipitation-daily", "item_id": "storm-total", "asset": "quality"},
    ]

    with pytest.raises(ToolError, match="precipitation-daily/storm-total"):
        await mcp_full_fx.call_tool(
            "multi_layer_summary", {"geojson": _AOI_POLYGON, "raster_layers": layers}
        )

    assert not mock_httpx_send.called


async def test_multi_layer_summary_accepts_distinct_labels_for_one_item(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """Two assets of the same item are fine once they are told apart."""
    mock_httpx_send.side_effect = stats_router()
    layers = [
        {"collection_id": "precipitation-daily", "item_id": "storm-total", "asset": "data", "label": "total"},
        {"collection_id": "precipitation-daily", "item_id": "storm-total", "asset": "data", "label": "quality"},
    ]

    result = await mcp_full_fx.call_tool(
        "multi_layer_summary", {"geojson": _AOI_POLYGON, "raster_layers": layers}
    )

    assert set(result.structured_content["layers"]) == {"total", "quality"}


async def test_counts_do_not_read_as_complete_when_truncated(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """The integers must not contradict the prose beside them.

    `features_analysed: 2` next to `features_attempted: 2` reads as "2 of 2,
    complete" — the pre-fix answer — when 5,496 polygons matched and only the
    note carried that number. `features_matched` puts the real total back among
    the integers, so a reader who skims the string still cannot conclude the
    result is complete.
    """
    mock_httpx_send.side_effect = flood_router(features={**_FEATURES_BODY, "numberMatched": 5496})

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis", {"flood_collection_id": "public.flood_extent_emsr864"}
    )

    sc = result.structured_content
    assert sc["features_analysed"] == 2
    assert sc["features_attempted"] == 2
    assert sc["features_matched"] == 5496
    assert sc["truncated"] is True
    # Reading only the integers, analysed != matched, so the result is visibly partial.
    assert sc["features_analysed"] < sc["features_matched"]


async def test_no_features_matched_key_when_the_backend_omits_the_total(
    mcp_full_fx: FastMCP, mock_httpx_send: AsyncMock
):
    """Absent is honest; a fabricated total would be the item_count bug again."""
    body = {k: v for k, v in _FEATURES_BODY.items() if k != "numberMatched"}
    mock_httpx_send.side_effect = flood_router(features=body)

    result = await mcp_full_fx.call_tool(
        "flood_rainfall_analysis", {"flood_collection_id": "public.flood_extent_emsr864"}
    )

    sc = result.structured_content
    assert "features_matched" not in sc
    assert sc["truncated"] is False if "truncated" in sc else True
