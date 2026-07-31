"""Unit tests for the raster tools.

Error-path tests assert ``pytest.raises(ToolError)`` rather than inspecting a
returned error dict. That is the contract: a failure is signalled through MCP's
error channel, never returned as data a model could mistake for a result.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from eo_mcp.config import Settings
from eo_mcp.http import Backends
from eo_mcp.tools import raster


def _resp(status: int, body: dict | None = None, text: str = "") -> MagicMock:
    """Build a fake httpx response."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = text
    r.url = f"https://fake.example/raster/test?status={status}"
    r.headers = {}
    return r


# ---------------------------------------------------------------------------
# get_raster_info
# ---------------------------------------------------------------------------


async def test_get_raster_info_happy(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    body = {"data": {"bounds": [-9.6, 36.9, -6.1, 42.2], "dtype": "float32", "count": 1}}
    mock_httpx_request.return_value = _resp(200, body)

    result = await mcp_fx.call_tool("get_raster_info", {"collection_id": "c", "item_id": "i", "asset": "data"})

    sc = result.structured_content
    assert "data" in sc
    assert sc["data"]["dtype"] == "float32"
    assert sc["data"]["count"] == 1
    assert len(sc["data"]["bounds"]) == 4


async def test_get_raster_info_404(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """A 404 raises, and the message carries status, URL, and the backend's text."""
    mock_httpx_request.return_value = _resp(404, text='{"detail":"No item found"}')

    with pytest.raises(ToolError) as excinfo:
        await mcp_fx.call_tool("get_raster_info", {"collection_id": "c", "item_id": "bad", "asset": "data"})

    message = str(excinfo.value)
    assert "404" in message
    assert "https://fake.example/raster/collections/c/items/bad/info" in message
    assert "No item found" in message


async def test_get_raster_info_500(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(500, text="internal server error")

    with pytest.raises(ToolError, match="500"):
        await mcp_fx.call_tool("get_raster_info", {"collection_id": "c", "item_id": "i", "asset": "data"})


async def test_get_raster_info_timeout(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """httpx.TimeoutException should surface as a FastMCP ToolError."""
    mock_httpx_request.side_effect = httpx.TimeoutException("timed out")

    with pytest.raises(ToolError, match="(?i)unreachable|timeout"):
        await mcp_fx.call_tool("get_raster_info", {"collection_id": "c", "item_id": "i", "asset": "data"})


async def test_get_raster_info_unreachable_host(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """A connect error names the failure type and the URL that failed."""
    mock_httpx_request.side_effect = httpx.ConnectError("connection refused")

    with pytest.raises(ToolError) as excinfo:
        await mcp_fx.call_tool("get_raster_info", {"collection_id": "c", "item_id": "i", "asset": "data"})

    message = str(excinfo.value)
    assert "unreachable" in message
    assert "ConnectError" in message
    assert "https://fake.example/raster/collections/c/items/i/info" in message


async def test_non_json_body_raises(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """A 200 whose body is not JSON is a failure, not a silent empty result."""
    bad = _resp(200)
    bad.json.side_effect = ValueError("no json")
    bad.text = "<html>a proxy error page</html>"
    mock_httpx_request.return_value = bad

    with pytest.raises(ToolError, match="not JSON"):
        await mcp_fx.call_tool("get_raster_info", {"collection_id": "c", "item_id": "i", "asset": "data"})


# ---------------------------------------------------------------------------
# get_tile_url
# ---------------------------------------------------------------------------

_TILEJSON_BODY = {
    "tilejson": "3.0.0",
    "tiles": [
        "https://api.cheias.pt/raster/collections/X/items/Y/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?assets=data"
    ],
    "bounds": [-9.6, 36.9, -6.1, 42.2],
    "center": [-7.85, 39.55, 0],
    "minzoom": 0,
    "maxzoom": 24,
}


async def test_get_tile_url_happy(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(200, _TILEJSON_BODY)

    result = await mcp_fx.call_tool("get_tile_url", {"collection_id": "X", "item_id": "Y", "asset": "data"})

    sc = result.structured_content
    assert "tile_url_template" in sc
    assert "WebMercatorQuad" in sc["tile_url_template"]
    assert len(sc["bounds"]) == 4
    assert "minzoom" in sc
    assert "maxzoom" in sc


async def test_get_tile_url_with_options(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(200, _TILEJSON_BODY)

    await mcp_fx.call_tool(
        "get_tile_url",
        {"collection_id": "X", "item_id": "Y", "asset": "data", "colormap": "viridis", "rescale": "0,100", "bidx": [1]},
    )

    raw_params = mock_httpx_request.call_args.kwargs.get("params", [])
    params = dict(raw_params)
    assert params.get("colormap_name") == "viridis"
    assert params.get("rescale") == "0,100"
    # bidx is multi-value; call_args params is a list of tuples
    bidx_values = [v for k, v in raw_params if k == "bidx"]
    assert 1 in bidx_values


async def test_get_tile_url_404(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(404, text="collection not found")

    with pytest.raises(ToolError, match="404"):
        await mcp_fx.call_tool("get_tile_url", {"collection_id": "bad", "item_id": "bad", "asset": "data"})


async def test_get_tile_url_color_formula(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """color_formula param is forwarded correctly."""
    mock_httpx_request.return_value = _resp(200, _TILEJSON_BODY)

    await mcp_fx.call_tool(
        "get_tile_url",
        {"collection_id": "X", "item_id": "Y", "asset": "data", "color_formula": "Gamma RGB 3.5"},
    )

    raw_params = mock_httpx_request.call_args.kwargs.get("params", [])
    cf_values = [v for k, v in raw_params if k == "color_formula"]
    assert cf_values == ["Gamma RGB 3.5"]


async def test_get_tile_url_encodes_spaces_in_tilejson_url(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """A color_formula with spaces must not produce a broken tilejson_url."""
    mock_httpx_request.return_value = _resp(200, _TILEJSON_BODY)

    result = await mcp_fx.call_tool(
        "get_tile_url",
        {"collection_id": "X", "item_id": "Y", "color_formula": "Gamma RGB 3.5 Saturation 1.7"},
    )

    url = result.structured_content["tilejson_url"]
    assert " " not in url
    assert "Gamma+RGB+3.5+Saturation+1.7" in url


async def test_get_tile_url_missing_tiles_key_raises(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """An unexpected payload becomes a message naming the key, not a KeyError."""
    mock_httpx_request.return_value = _resp(200, {"bounds": [0, 0, 1, 1], "minzoom": 0, "maxzoom": 20})

    with pytest.raises(ToolError, match="missing the expected 'tiles' field"):
        await mcp_fx.call_tool("get_tile_url", {"collection_id": "X", "item_id": "Y"})


async def test_get_tile_url_empty_tiles_list_raises(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(200, {**_TILEJSON_BODY, "tiles": []})

    with pytest.raises(ToolError, match="empty 'tiles' list"):
        await mcp_fx.call_tool("get_tile_url", {"collection_id": "X", "item_id": "Y"})


async def test_get_tile_url_payload_containing_error_key_is_not_a_failure(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """A 200 payload that merely contains an 'error' key is still a success.

    The old implementation treated any dict with an 'error' key as a backend
    failure, conflating a real error with data that happens to use that name.
    """
    mock_httpx_request.return_value = _resp(200, {**_TILEJSON_BODY, "error": 0.25})

    result = await mcp_fx.call_tool("get_tile_url", {"collection_id": "X", "item_id": "Y"})

    assert "tile_url_template" in result.structured_content


# ---------------------------------------------------------------------------
# get_tile_url: styling defaults from STAC collection metadata
# ---------------------------------------------------------------------------

# Shape verified live against https://api.cheias.pt/collections/precipitation-daily
# on 2026-07-31: both properties sit at collection top level, rescale is a
# two-element list of numbers and colormap is a string.
_COLLECTION_WITH_RENDER_HINTS = {
    "id": "precipitation-daily",
    "type": "Collection",
    "cheias:rescale": [0, 80],
    "cheias:colormap": "blues",
}


def _render_hint_router(collection_body: dict | None):
    """Route the STAC collection lookup separately from the TiTiler tilejson call."""

    def _router(method, path, **kw):
        if path.startswith("/collections/") and path.count("/") == 2:
            if collection_body is None:
                return _resp(404, text="collection not found")
            return _resp(200, collection_body)
        return _resp(200, _TILEJSON_BODY)

    return _router


async def test_get_tile_url_uses_collection_render_defaults(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.side_effect = _render_hint_router(_COLLECTION_WITH_RENDER_HINTS)

    result = await mcp_fx.call_tool(
        "get_tile_url", {"collection_id": "precipitation-daily", "item_id": "Y"}
    )

    render = result.structured_content["render"]
    assert render["rescale"] == "0,80"
    assert render["colormap"] == "blues"
    assert sorted(render["from_collection_metadata"]) == ["colormap", "rescale"]

    # And they reach TiTiler in the form it expects.
    params = dict(mock_httpx_request.call_args.kwargs.get("params", []))
    assert params["rescale"] == "0,80"
    assert params["colormap_name"] == "blues"


async def test_explicit_args_override_collection_render_defaults(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.side_effect = _render_hint_router(_COLLECTION_WITH_RENDER_HINTS)

    result = await mcp_fx.call_tool(
        "get_tile_url",
        {"collection_id": "precipitation-daily", "item_id": "Y", "rescale": "5,10", "colormap": "magma"},
    )

    render = result.structured_content["render"]
    assert render["rescale"] == "5,10"
    assert render["colormap"] == "magma"
    assert render["from_collection_metadata"] == []


async def test_collection_without_render_hints_degrades_silently(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.side_effect = _render_hint_router({"id": "plain", "type": "Collection"})

    result = await mcp_fx.call_tool("get_tile_url", {"collection_id": "plain", "item_id": "Y"})

    render = result.structured_content["render"]
    assert render["rescale"] is None
    assert render["colormap"] is None
    params = dict(mock_httpx_request.call_args.kwargs.get("params", []))
    assert "rescale" not in params
    assert "colormap_name" not in params


async def test_unreachable_stac_does_not_break_tile_url(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """A failed metadata lookup is a lost convenience, never a failed tile request."""
    mock_httpx_request.side_effect = _render_hint_router(None)

    result = await mcp_fx.call_tool("get_tile_url", {"collection_id": "gone", "item_id": "Y"})

    assert "tile_url_template" in result.structured_content


async def test_render_defaults_are_cached_across_calls(backends_fx: Backends, mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """The STAC lookup happens once per collection, not once per tile request."""
    mock_httpx_request.side_effect = _render_hint_router(_COLLECTION_WITH_RENDER_HINTS)

    for _ in range(3):
        await mcp_fx.call_tool("get_tile_url", {"collection_id": "precipitation-daily", "item_id": "Y"})

    collection_lookups = [
        call for call in mock_httpx_request.call_args_list
        if call.args[1] == "/collections/precipitation-daily"
    ]
    assert len(collection_lookups) == 1


@pytest.mark.parametrize(
    "raw,expected",
    [
        ([0, 80], "0,80"),
        ([-3, 3], "-3,3"),
        ([0.05, 0.5], "0.05,0.5"),
        ("0,80", "0,80"),
        (None, None),
        ([1, 2, 3], None),
        (["a", "b"], None),
        ([True, False], None),
    ],
)
def test_format_rescale(raw, expected):
    from eo_mcp.http import _format_rescale

    assert _format_rescale(raw) == expected


# ---------------------------------------------------------------------------
# get_zonal_statistics
# ---------------------------------------------------------------------------

_POLYGON = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}

_STATS_FEATURE = {
    "type": "Feature",
    "geometry": _POLYGON,
    "properties": {"statistics": {"data_b1": {"min": 0.0, "max": 1.0, "mean": 0.5, "count": 10.0}}},
}


async def test_get_zonal_stats_bare_polygon(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Bare Polygon input must be wrapped into a Feature before the POST body."""
    mock_httpx_request.return_value = _resp(200, _STATS_FEATURE)

    result = await mcp_fx.call_tool("get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": _POLYGON})

    body = mock_httpx_request.call_args.kwargs.get("json", {})
    assert body["type"] == "Feature"
    assert body.get("geometry") == _POLYGON
    sc = result.structured_content
    assert "properties" in sc
    assert "statistics" in sc["properties"]


async def test_get_zonal_stats_feature_input(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Feature input must be passed as-is (no double-wrap)."""
    mock_httpx_request.return_value = _resp(200, _STATS_FEATURE)
    feature = {"type": "Feature", "geometry": _POLYGON, "properties": {}}

    await mcp_fx.call_tool("get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": feature})

    body = mock_httpx_request.call_args.kwargs.get("json", {})
    assert body["type"] == "Feature"
    assert body.get("properties") == {}
    assert body.get("geometry") == feature["geometry"]


async def test_get_zonal_stats_invalid_geojson_type(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Point geometry is unsupported; the tool raises without calling the API."""
    with pytest.raises(ToolError, match="Polygon"):
        await mcp_fx.call_tool(
            "get_zonal_statistics",
            {"collection_id": "c", "item_id": "i", "geojson": {"type": "Point", "coordinates": [0, 0]}},
        )

    assert not mock_httpx_request.called


async def test_get_zonal_stats_feature_with_point_geometry_raises(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}

    with pytest.raises(ToolError, match="Polygon or MultiPolygon"):
        await mcp_fx.call_tool("get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": feature})

    assert not mock_httpx_request.called


async def test_get_zonal_stats_feature_collection_forwards_every_feature(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """The whole FeatureCollection is sent, not just features[0].

    TiTiler computes statistics per feature and returns a FeatureCollection —
    verified live against api.cheias.pt on 2026-07-31. The previous behaviour
    analysed only the first feature and reported it as covering the collection.
    """
    second_polygon = {"type": "Polygon", "coordinates": [[[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]]}
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": _POLYGON, "properties": {"name": "a"}},
            {"type": "Feature", "geometry": second_polygon, "properties": {"name": "b"}},
        ],
    }
    mock_httpx_request.return_value = _resp(200, {"type": "FeatureCollection", "features": [_STATS_FEATURE, _STATS_FEATURE]})

    result = await mcp_fx.call_tool("get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": fc})

    body = mock_httpx_request.call_args.kwargs.get("json", {})
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 2
    assert result.structured_content["type"] == "FeatureCollection"
    assert len(result.structured_content["features"]) == 2


async def test_get_zonal_stats_empty_feature_collection(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Empty FeatureCollection raises without hitting the API."""
    with pytest.raises(ToolError, match="no features"):
        await mcp_fx.call_tool(
            "get_zonal_statistics",
            {"collection_id": "c", "item_id": "i", "geojson": {"type": "FeatureCollection", "features": []}},
        )

    assert not mock_httpx_request.called


async def test_get_zonal_stats_feature_collection_with_bad_member(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """A non-area member is named by index rather than failing opaquely."""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": _POLYGON, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}},
        ],
    }

    with pytest.raises(ToolError, match=r"features\[1\]"):
        await mcp_fx.call_tool("get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": fc})

    assert not mock_httpx_request.called


async def test_get_zonal_stats_feature_collection_with_non_feature_member(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    fc = {"type": "FeatureCollection", "features": [_POLYGON]}

    with pytest.raises(ToolError, match=r"features\[0\].*not"):
        await mcp_fx.call_tool("get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": fc})


async def test_get_zonal_stats_categorical_and_bins(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """categorical=True and histogram_bins are appended as query params."""
    mock_httpx_request.return_value = _resp(200, _STATS_FEATURE)

    await mcp_fx.call_tool(
        "get_zonal_statistics",
        {"collection_id": "c", "item_id": "i", "geojson": _POLYGON, "categorical": True, "histogram_bins": 10},
    )

    param_dict = dict(mock_httpx_request.call_args.kwargs.get("params", []))
    assert param_dict.get("categorical") == "true"
    assert param_dict.get("histogram_bins") == "10"


async def test_get_raster_info_with_auth_token(settings_fx: Settings, mock_httpx_request: AsyncMock):
    """When auth_token is set the Authorization header branch is exercised."""
    authed = Settings(
        raster_url=settings_fx.raster_url,
        stac_url=settings_fx.stac_url,
        vector_url=settings_fx.vector_url,
        auth_token="tok123",
        max_retries=0,
    )
    backends = Backends(authed)
    authed_mcp = FastMCP("authed")
    raster.register(authed_mcp, backends)

    body = {"data": {"bounds": [-9.6, 36.9, -6.1, 42.2], "dtype": "float32", "count": 1}}
    mock_httpx_request.return_value = _resp(200, body)

    try:
        result = await authed_mcp.call_tool("get_raster_info", {"collection_id": "c", "item_id": "i", "asset": "data"})
        assert result.structured_content.get("data") is not None
        client = await backends.raster.client()
        assert client.headers["Authorization"] == "Bearer tok123"
    finally:
        await backends.aclose()


def test_statistics_body_rejects_a_non_object():
    """FastMCP's schema normally catches this; the guard is the last line."""
    from eo_mcp.tools.raster import statistics_body

    with pytest.raises(ToolError, match="must be a GeoJSON object, got list"):
        statistics_body(["not", "geojson"])


# ---------------------------------------------------------------------------
# tipg feature bloat: the reason the two-tool chain used to fail
# ---------------------------------------------------------------------------

_TIPG_FEATURE = {
    "type": "Feature",
    # On a real flood polygon this hex and the matching `links` entry are 95% of
    # the payload — 5.96 MB against a 1.82 MB geometry, measured live 2026-07-31.
    "id": "0103000020E6100000" + "AB" * 500,
    "links": [{"rel": "self", "href": "https://x/items/" + "AB" * 500}],
    "geometry": _POLYGON,
    "properties": {"ogc_fid": 42, "area_ha": 150.0},
}


async def test_zonal_stats_drops_tipg_id_and_links_from_the_body(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """Chaining get_vector_features into get_zonal_statistics must not 413.

    The Feature that tipg returns carries an `id` that is the WKB hex of its own
    geometry plus a `links` array embedding the same hex. Neither is readable by
    TiTiler and neither round-trips, but together they made the natural chain
    exceed the backend's request-size limit for a geometry an order of magnitude
    under it.
    """
    mock_httpx_request.return_value = _resp(200, _STATS_FEATURE)

    await mcp_fx.call_tool(
        "get_zonal_statistics",
        {"collection_id": "c", "item_id": "i", "geojson": _TIPG_FEATURE},
    )

    body = mock_httpx_request.call_args.kwargs["json"]
    assert set(body) == {"type", "geometry", "properties"}
    assert body["geometry"] == _POLYGON
    # properties survive: they are small and they identify the source feature.
    assert body["properties"]["ogc_fid"] == 42
    assert len(json.dumps(body)) < len(json.dumps(_TIPG_FEATURE)) / 4


async def test_zonal_stats_drops_id_and_links_from_every_collection_member(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    fc = {"type": "FeatureCollection", "features": [_TIPG_FEATURE, _TIPG_FEATURE]}
    mock_httpx_request.return_value = _resp(
        200, {"type": "FeatureCollection", "features": [_STATS_FEATURE, _STATS_FEATURE]}
    )

    await mcp_fx.call_tool(
        "get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": fc}
    )

    body = mock_httpx_request.call_args.kwargs["json"]
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 2
    for member in body["features"]:
        assert set(member) == {"type", "geometry", "properties"}


# ---------------------------------------------------------------------------
# rescale defaults are checked against the item, not just inherited
# ---------------------------------------------------------------------------


def _render_router(collection_body, item_stats):
    """Route the STAC collection lookup, the item statistics, and the tilejson."""

    def _router(method, path, **kw):
        if path.startswith("/collections/") and path.count("/") == 2:
            return _resp(200, collection_body)
        if path.endswith("/statistics"):
            return _resp(200, item_stats) if item_stats else _resp(404, text="no stats")
        return _resp(200, _TILEJSON_BODY)

    return _router


async def test_collection_rescale_applied_when_it_fits_the_item(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    mock_httpx_request.side_effect = _render_router(
        _COLLECTION_WITH_RENDER_HINTS,
        {"data_b1": {"percentile_2": 0.5, "percentile_98": 20.75}},
    )

    result = await mcp_fx.call_tool(
        "get_tile_url", {"collection_id": "precipitation-daily", "item_id": "daily-2026-02-08"}
    )

    render = result.structured_content["render"]
    assert render["rescale"] == "0,80"
    assert render["rescale_source"] == "collection-metadata"


async def test_collection_rescale_rejected_when_it_would_saturate_the_item(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """precipitation-daily publishes [0, 80]; storm-total runs 100-582.

    Applying the collection value there clamps every pixel to the top of the
    ramp: a 200, a valid tile URL, and a uniformly saturated image. Verified
    live on 2026-07-31.
    """
    mock_httpx_request.side_effect = _render_router(
        _COLLECTION_WITH_RENDER_HINTS,
        {"data_b1": {"percentile_2": 133.4, "percentile_98": 516.5}},
    )

    result = await mcp_fx.call_tool(
        "get_tile_url",
        {"collection_id": "precipitation-daily", "item_id": "precipitation-storm-total"},
    )

    render = result.structured_content["render"]
    assert render["rescale"] == "133.4,516.5"
    assert render["rescale_source"] == "item-percentiles"
    assert render["item_data_range"] == [133.4, 516.5]
    assert "rescale" not in render["from_collection_metadata"]


async def test_caller_rescale_skips_the_item_probe_entirely(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """An explicit argument wins, and costs no extra round trip."""
    mock_httpx_request.side_effect = _render_router(
        _COLLECTION_WITH_RENDER_HINTS,
        {"data_b1": {"percentile_2": 133.4, "percentile_98": 516.5}},
    )

    result = await mcp_fx.call_tool(
        "get_tile_url",
        {"collection_id": "precipitation-daily", "item_id": "x", "rescale": "0,999"},
    )

    render = result.structured_content["render"]
    assert render["rescale"] == "0,999"
    assert render["rescale_source"] == "caller"
    assert not any(
        c.args[1].endswith("/statistics") for c in mock_httpx_request.call_args_list
    )


async def test_unavailable_item_stats_fall_back_to_the_collection_value(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """If the range cannot be checked, the curated value is still the best guess."""
    mock_httpx_request.side_effect = _render_router(_COLLECTION_WITH_RENDER_HINTS, None)

    result = await mcp_fx.call_tool(
        "get_tile_url", {"collection_id": "precipitation-daily", "item_id": "x"}
    )

    render = result.structured_content["render"]
    assert render["rescale"] == "0,80"
    assert render["rescale_source"] == "collection-metadata"
    assert render["item_data_range"] is None


async def test_narrow_collection_rescale_is_kept_but_flagged(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """precondition publishes [0, 100]; its item occupies [0, 20.2] — 20% of the ramp.

    The curated value is kept, because a wide ramp is what makes items in a
    collection comparable. The caller is told contrast is low and given the
    value to pass if they want a single-item view instead.
    """
    mock_httpx_request.side_effect = _render_router(
        {"id": "precondition", "cheias:rescale": [0, 100], "cheias:colormap": "hot_r"},
        {"data_b1": {"percentile_2": 0.0, "percentile_98": 20.21}},
    )

    result = await mcp_fx.call_tool(
        "get_tile_url", {"collection_id": "precondition", "item_id": "x"}
    )

    render = result.structured_content["render"]
    assert render["rescale"] == "0,100"
    assert render["rescale_source"] == "collection-metadata"
    assert "few distinct tones" in render["rescale_note"]
    assert "rescale='0,20.21'" in render["rescale_note"]
    assert "comparable" in render["rescale_note"]
    assert render["colormap"] == "hot_r"


async def test_out_of_range_rescale_note_says_it_does_not_span(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    mock_httpx_request.side_effect = _render_router(
        _COLLECTION_WITH_RENDER_HINTS,
        {"data_b1": {"percentile_2": 133.4, "percentile_98": 516.5}},
    )

    result = await mcp_fx.call_tool(
        "get_tile_url", {"collection_id": "precipitation-daily", "item_id": "storm-total"}
    )

    note = result.structured_content["render"]["rescale_note"]
    assert "does not span" in note
    assert "0,80" in note
    assert "NOT on the same scale" in note


async def test_accepted_collection_rescale_has_no_note(
    mcp_fx: FastMCP, mock_httpx_request: AsyncMock
):
    mock_httpx_request.side_effect = _render_router(
        {"id": "wind-u", "cheias:rescale": [-20, 20], "cheias:colormap": "coolwarm"},
        {"data_b1": {"percentile_2": -9.59, "percentile_98": 17.62}},
    )

    render = (
        await mcp_fx.call_tool("get_tile_url", {"collection_id": "wind-u", "item_id": "x"})
    ).structured_content["render"]

    assert render["rescale"] == "-20,20"
    assert render["rescale_source"] == "collection-metadata"
    assert render["rescale_note"] is None
