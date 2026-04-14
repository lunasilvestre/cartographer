from __future__ import annotations

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from unittest.mock import AsyncMock, MagicMock


def _resp(status: int, body: dict | None = None, text: str = "") -> MagicMock:
    """Build a fake httpx response."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = text
    r.url = f"https://fake.example/raster/test?status={status}"
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
    mock_httpx_request.return_value = _resp(404, text="not found")

    result = await mcp_fx.call_tool("get_raster_info", {"collection_id": "c", "item_id": "bad", "asset": "data"})

    sc = result.structured_content
    assert "error" in sc
    assert sc["status"] == 404


async def test_get_raster_info_timeout(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """httpx.TimeoutException should surface as a FastMCP ToolError."""
    mock_httpx_request.side_effect = httpx.TimeoutException("timed out")

    with pytest.raises(ToolError, match="(?i)timeout|timed out|retry"):
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

    params = dict(mock_httpx_request.call_args.kwargs.get("params", mock_httpx_request.call_args[1].get("params", [])))
    assert params.get("colormap_name") == "viridis"
    assert params.get("rescale") == "0,100"
    # bidx is multi-value; call_args params is a list of tuples
    raw_params = mock_httpx_request.call_args.kwargs.get("params", [])
    bidx_values = [v for k, v in raw_params if k == "bidx"]
    assert 1 in bidx_values


async def test_get_tile_url_404(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(404, text="collection not found")

    result = await mcp_fx.call_tool("get_tile_url", {"collection_id": "bad", "item_id": "bad", "asset": "data"})

    sc = result.structured_content
    assert "error" in sc
    assert sc["status"] == 404


# ---------------------------------------------------------------------------
# get_zonal_statistics
# ---------------------------------------------------------------------------

_STATS_FEATURE = {
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    "properties": {"statistics": {"data_b1": {"min": 0.0, "max": 1.0, "mean": 0.5, "count": 10.0}}},
}


async def test_get_zonal_stats_bare_polygon(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Bare Polygon input must be wrapped into a Feature before the POST body."""
    mock_httpx_request.return_value = _resp(200, _STATS_FEATURE)
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}

    result = await mcp_fx.call_tool("get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": polygon})

    body = mock_httpx_request.call_args.kwargs.get("json", {})
    assert body["type"] == "Feature"
    assert body.get("geometry") == polygon
    sc = result.structured_content
    assert "properties" in sc
    assert "statistics" in sc["properties"]


async def test_get_zonal_stats_feature_input(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Feature input must be passed as-is (no double-wrap)."""
    mock_httpx_request.return_value = _resp(200, _STATS_FEATURE)
    feature = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        "properties": {},
    }

    await mcp_fx.call_tool("get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": feature})

    body = mock_httpx_request.call_args.kwargs.get("json", {})
    assert body["type"] == "Feature"
    assert body.get("properties") == {}
    assert body.get("geometry") == feature["geometry"]


async def test_get_zonal_stats_invalid_geojson_type(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Point geometry is unsupported; tool returns a 400 error dict without calling the API."""
    result = await mcp_fx.call_tool(
        "get_zonal_statistics",
        {"collection_id": "c", "item_id": "i", "geojson": {"type": "Point", "coordinates": [0, 0]}},
    )

    sc = result.structured_content
    assert sc["status"] == 400
    assert "error" in sc
    assert not mock_httpx_request.called


async def test_get_zonal_stats_feature_collection(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """FeatureCollection: first feature is extracted and used as the POST body."""
    mock_httpx_request.return_value = _resp(200, _STATS_FEATURE)
    first = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}, "properties": {}}
    fc = {"type": "FeatureCollection", "features": [first]}

    await mcp_fx.call_tool("get_zonal_statistics", {"collection_id": "c", "item_id": "i", "geojson": fc})

    body = mock_httpx_request.call_args.kwargs.get("json", {})
    assert body["type"] == "Feature"


async def test_get_zonal_stats_empty_feature_collection(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Empty FeatureCollection returns a 400 error without hitting the API."""
    result = await mcp_fx.call_tool(
        "get_zonal_statistics",
        {"collection_id": "c", "item_id": "i", "geojson": {"type": "FeatureCollection", "features": []}},
    )

    sc = result.structured_content
    assert sc["status"] == 400
    assert not mock_httpx_request.called


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


async def test_get_zonal_stats_categorical_and_bins(mcp_fx: FastMCP, mock_httpx_request: AsyncMock):
    """categorical=True and histogram_bins are appended as query params."""
    mock_httpx_request.return_value = _resp(200, _STATS_FEATURE)
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}

    await mcp_fx.call_tool(
        "get_zonal_statistics",
        {"collection_id": "c", "item_id": "i", "geojson": polygon, "categorical": True, "histogram_bins": 10},
    )

    raw_params = mock_httpx_request.call_args.kwargs.get("params", [])
    param_dict = dict(raw_params)
    assert param_dict.get("categorical") == "true"
    assert param_dict.get("histogram_bins") == "10"


async def test_get_raster_info_with_auth_token(settings_fx, mock_httpx_request: AsyncMock):
    """When auth_token is set the Authorization header branch is exercised."""
    from eo_mcp.config import Settings
    from eo_mcp.tools import raster as r_mod

    authed_settings = Settings(raster_url=settings_fx.raster_url, stac_url=settings_fx.stac_url, auth_token="tok123")
    from fastmcp import FastMCP as _FastMCP
    authed_mcp = _FastMCP("authed")
    r_mod.register(authed_mcp, authed_settings)

    body = {"data": {"bounds": [-9.6, 36.9, -6.1, 42.2], "dtype": "float32", "count": 1}}
    mock_httpx_request.return_value = _resp(200, body)

    result = await authed_mcp.call_tool("get_raster_info", {"collection_id": "c", "item_id": "i", "asset": "data"})
    assert result.structured_content.get("data") is not None
