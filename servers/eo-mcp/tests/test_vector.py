"""Unit tests for the vector tools (v0.3).

Mirrors the structure of test_raster.py — same mock pattern, same assertion style.
Each tool gets: happy path, error path, and at least one edge case.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastmcp import FastMCP


def _resp(status: int, body: dict | list | None = None, text: str = "") -> MagicMock:
    """Build a fake httpx response."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.text = text
    r.url = f"https://fake.example/vector/test?status={status}"
    return r


# ---------------------------------------------------------------------------
# list_vector_collections
# ---------------------------------------------------------------------------

_COLLECTIONS_BODY = {
    "collections": [
        {
            "id": "public.flood_extent_emsr864",
            "title": "EMSR864 Flood Extent",
            "description": "Flood extent polygons for EMSR864 event",
            "extent": {
                "spatial": {"bbox": [[-9.0, 38.0, -6.0, 41.0]]},
                "temporal": {"interval": [["2026-02-01T00:00:00Z", None]]},
            },
            "_tipg_internal": "should be stripped",
        },
        {
            "id": "public.flood_extent_emsr861",
            "title": "EMSR861 Flood Extent",
            "description": "Flood extent polygons for EMSR861 event",
            "extent": {
                "spatial": {"bbox": [[-8.5, 39.0, -7.0, 40.5]]},
                "temporal": {"interval": [["2025-12-01T00:00:00Z", None]]},
            },
        },
    ]
}


async def test_list_vector_collections_happy(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(200, _COLLECTIONS_BODY)

    result = await mcp_full_fx.call_tool("list_vector_collections", {})

    sc = result.structured_content
    assert "collections" in sc
    assert len(sc["collections"]) == 2
    first = sc["collections"][0]
    assert first["id"] == "public.flood_extent_emsr864"
    assert "title" in first


async def test_list_vector_collections_simplified_shape(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """The returned list should not include tipg-internal fields."""
    mock_httpx_request.return_value = _resp(200, _COLLECTIONS_BODY)

    result = await mcp_full_fx.call_tool("list_vector_collections", {})

    sc = result.structured_content
    for col in sc["collections"]:
        # Internal tipg fields must be stripped from the simplified output.
        assert "_tipg_internal" not in col
        # Standard fields must be present.
        assert "id" in col
        assert "extent" in col or "bbox" in col or "title" in col


async def test_list_vector_collections_404(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(404, text="not found")

    result = await mcp_full_fx.call_tool("list_vector_collections", {})

    sc = result.structured_content
    assert "error" in sc
    assert sc["status"] == 404


async def test_list_vector_collections_500(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(500, text="internal server error")

    result = await mcp_full_fx.call_tool("list_vector_collections", {})

    sc = result.structured_content
    assert "error" in sc
    assert sc["status"] == 500


# ---------------------------------------------------------------------------
# get_vector_features
# ---------------------------------------------------------------------------

_FEATURES_BODY = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": "feat-001",
            "geometry": {"type": "Polygon", "coordinates": [[[-8.0, 39.0], [-7.5, 39.0], [-7.5, 39.5], [-8.0, 39.5], [-8.0, 39.0]]]},
            "properties": {
                "event_date": "2026-02-08",
                "area_ha": 150.0,
                "aff_type": "Flooded area",
            },
        },
    ],
    "numberMatched": 1,
    "numberReturned": 1,
}


async def test_get_vector_features_happy(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(200, _FEATURES_BODY)

    result = await mcp_full_fx.call_tool(
        "get_vector_features",
        {"collection_id": "public.flood_extent_emsr864"},
    )

    sc = result.structured_content
    assert sc["type"] == "FeatureCollection"
    assert len(sc["features"]) == 1
    assert sc["features"][0]["properties"]["event_date"] == "2026-02-08"


async def test_get_vector_features_404(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(404, text="collection not found")

    result = await mcp_full_fx.call_tool(
        "get_vector_features",
        {"collection_id": "public.nonexistent"},
    )

    sc = result.structured_content
    assert "error" in sc
    assert sc["status"] == 404


async def test_get_vector_features_bbox_as_comma_string(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """bbox list should be joined as a comma-separated string in the query."""
    mock_httpx_request.return_value = _resp(200, _FEATURES_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_features",
        {
            "collection_id": "public.flood_extent_emsr864",
            "bbox": [-8.5, 39.0, -7.5, 39.8],
        },
    )

    # Inspect the params passed to the httpx request
    call_kwargs = mock_httpx_request.call_args
    params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
    # params may be a dict or list of tuples
    if isinstance(params, dict):
        bbox_val = params.get("bbox")
    else:
        bbox_val = next((v for k, v in params if k == "bbox"), None)
    assert bbox_val == "-8.5,39.0,-7.5,39.8"


async def test_get_vector_features_filter_lang_query_key(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """filter_lang should be passed as 'filter-lang' (hyphenated) in the query."""
    mock_httpx_request.return_value = _resp(200, _FEATURES_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_features",
        {
            "collection_id": "public.flood_extent_emsr864",
            "filter": "event_date='2026-02-08'",
            "filter_lang": "cql2-text",
        },
    )

    call_kwargs = mock_httpx_request.call_args
    params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
    if isinstance(params, dict):
        assert "filter-lang" in params
        assert params.get("filter") == "event_date='2026-02-08'"
    else:
        keys = [k for k, v in params]
        assert "filter-lang" in keys
        assert any(k == "filter" and v == "event_date='2026-02-08'" for k, v in params)


async def test_get_vector_features_schema_prefix_normalization(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """collection_id without a dot should have 'public.' prepended in the URL path."""
    mock_httpx_request.return_value = _resp(200, _FEATURES_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_features",
        {"collection_id": "flood_extent_emsr864"},
    )

    call_args = mock_httpx_request.call_args
    path = call_args.args[1] if call_args.args else call_args[0][1]
    assert "public.flood_extent_emsr864" in path


async def test_get_vector_features_with_all_params(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """All optional params should be forwarded to the HTTP request."""
    mock_httpx_request.return_value = _resp(200, _FEATURES_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_features",
        {
            "collection_id": "public.flood_extent_emsr864",
            "limit": 5,
            "offset": 10,
            "datetime": "2026-02-08T00:00:00Z/2026-02-08T23:59:59Z",
            "sortby": "-event_date",
            "simplify": 0.001,
        },
    )

    assert mock_httpx_request.called


# ---------------------------------------------------------------------------
# get_vector_feature
# ---------------------------------------------------------------------------

_SINGLE_FEATURE = {
    "type": "Feature",
    "id": "0103...",
    "geometry": {"type": "Polygon", "coordinates": [[[-8.0, 39.0], [-7.5, 39.0], [-7.5, 39.5], [-8.0, 39.5], [-8.0, 39.0]]]},
    "properties": {
        "ogc_fid": 42,
        "datetime": "2026-02-08T00:00:00+00:00",
        "area_ha": 150.0,
    },
}

_SINGLE_FEATURE_BODY = {
    "type": "FeatureCollection",
    "features": [_SINGLE_FEATURE],
}


async def test_get_vector_feature_happy(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(200, _SINGLE_FEATURE_BODY)

    result = await mcp_full_fx.call_tool(
        "get_vector_feature",
        {"collection_id": "public.flood_extent_emsr864", "feature_id": "42"},
    )

    sc = result.structured_content
    assert sc["type"] == "Feature"
    assert sc["properties"]["area_ha"] == 150.0


async def test_get_vector_feature_not_found_empty_features(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Empty FeatureCollection from tipg → synthetic 404 error dict."""
    mock_httpx_request.return_value = _resp(200, {"type": "FeatureCollection", "features": []})

    result = await mcp_full_fx.call_tool(
        "get_vector_feature",
        {"collection_id": "public.flood_extent_emsr864", "feature_id": "99999"},
    )

    sc = result.structured_content
    assert "error" in sc
    assert sc["status"] == 404


async def test_get_vector_feature_backend_error(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Upstream tipg error should be surfaced."""
    mock_httpx_request.return_value = _resp(500, text="backend down")

    result = await mcp_full_fx.call_tool(
        "get_vector_feature",
        {"collection_id": "public.flood_extent_emsr864", "feature_id": "42"},
    )

    sc = result.structured_content
    assert "error" in sc
    assert sc["status"] == 500


async def test_get_vector_feature_uses_ogc_fid_filter(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Must query /items with an ogc_fid CQL2 filter (not /items/{id} path)."""
    mock_httpx_request.return_value = _resp(200, _SINGLE_FEATURE_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_feature",
        {"collection_id": "public.flood_extent_emsr864", "feature_id": "42"},
    )

    call_args = mock_httpx_request.call_args
    path = call_args.args[1] if call_args.args else call_args[0][1]
    assert "public.flood_extent_emsr864" in path
    assert path.endswith("/items")
    assert "/items/42" not in path
    raw_params = call_args.kwargs.get("params", [])
    param_dict = dict(raw_params)
    assert param_dict.get("filter") == "ogc_fid=42"
    assert param_dict.get("filter-lang") == "cql2-text"


async def test_get_vector_feature_schema_prefix_normalization(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """collection_id without dot should be normalized to public.<id>."""
    mock_httpx_request.return_value = _resp(200, _SINGLE_FEATURE_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_feature",
        {"collection_id": "flood_extent_emsr864", "feature_id": "42"},
    )

    call_args = mock_httpx_request.call_args
    path = call_args.args[1] if call_args.args else call_args[0][1]
    assert "public.flood_extent_emsr864" in path


# ---------------------------------------------------------------------------
# get_vector_tile_url
# ---------------------------------------------------------------------------

_TILEJSON_BODY = {
    "tilejson": "3.0.0",
    "name": "flood_extent_emsr864",
    "tiles": [
        "https://api.cheias.pt/vector/collections/public.flood_extent_emsr864/tiles/WebMercatorQuad/{z}/{x}/{y}"
    ],
    "bounds": [-9.0, 38.0, -6.0, 41.0],
    "center": [-7.5, 39.5, 8],
    "minzoom": 0,
    "maxzoom": 22,
    "vector_layers": [{"id": "default", "fields": {"event_date": "String"}}],
}

_STYLE_BODY = {
    "version": 8,
    "layers": [
        {
            "id": "flood-fill",
            "type": "fill",
            "source": "default",
            "paint": {"fill-color": "#0000ff", "fill-opacity": 0.5},
        }
    ],
}


async def test_get_vector_tile_url_happy(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Both tilejson and style should be fetched; result has both keys."""
    responses = [_resp(200, _TILEJSON_BODY), _resp(200, _STYLE_BODY)]
    mock_httpx_request.side_effect = responses

    result = await mcp_full_fx.call_tool(
        "get_vector_tile_url",
        {"collection_id": "public.flood_extent_emsr864"},
    )

    sc = result.structured_content
    assert "tile_url_template" in sc or "tiles" in sc
    assert "bounds" in sc
    assert "minzoom" in sc
    assert "maxzoom" in sc
    # Style should be included
    assert "style" in sc


async def test_get_vector_tile_url_404(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(404, text="collection not found")

    result = await mcp_full_fx.call_tool(
        "get_vector_tile_url",
        {"collection_id": "public.nonexistent"},
    )

    sc = result.structured_content
    assert "error" in sc
    assert sc["status"] == 404


async def test_get_vector_tile_url_style_404_does_not_kill_call(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """A 404 on style.json should not prevent the tilejson result from being returned.
    Style should become None in the result."""

    def _router(method, path, **kw):
        if "style.json" in path:
            return _resp(404, text="style not found")
        return _resp(200, _TILEJSON_BODY)

    mock_httpx_request.side_effect = _router

    result = await mcp_full_fx.call_tool(
        "get_vector_tile_url",
        {"collection_id": "public.flood_extent_emsr864"},
    )

    sc = result.structured_content
    # Must not be an error
    assert "error" not in sc
    # tile URL info must be present
    assert "tile_url_template" in sc or "tiles" in sc or "bounds" in sc
    # Style must be None (not missing or erroring)
    assert sc.get("style") is None


async def test_get_vector_tile_url_schema_prefix_normalization(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """collection_id without dot is normalized to public.<id>."""
    responses = [_resp(200, _TILEJSON_BODY), _resp(200, _STYLE_BODY)]
    mock_httpx_request.side_effect = responses

    await mcp_full_fx.call_tool(
        "get_vector_tile_url",
        {"collection_id": "flood_extent_emsr864"},
    )

    call_args = mock_httpx_request.call_args_list[0]
    path = call_args.args[1] if call_args.args else call_args[0][1]
    assert "public.flood_extent_emsr864" in path


# ---------------------------------------------------------------------------
# get_collection_queryables
# ---------------------------------------------------------------------------

_QUERYABLES_BODY = {
    "$schema": "https://json-schema.org/draft/07/schema#",
    "$id": "https://api.cheias.pt/vector/collections/public.flood_extent_emsr864/queryables",
    "type": "object",
    "title": "public.flood_extent_emsr864",
    "properties": {
        "event_date": {
            "title": "event_date",
            "type": "string",
            "format": "date",
        },
        "area_ha": {
            "title": "area_ha",
            "type": "number",
        },
        "aff_type": {
            "title": "aff_type",
            "type": "string",
        },
    },
}


async def test_get_collection_queryables_happy(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(200, _QUERYABLES_BODY)

    result = await mcp_full_fx.call_tool(
        "get_collection_queryables",
        {"collection_id": "public.flood_extent_emsr864"},
    )

    sc = result.structured_content
    assert "properties" in sc
    assert "event_date" in sc["properties"]
    assert "area_ha" in sc["properties"]


async def test_get_collection_queryables_404(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(404, text="collection not found")

    result = await mcp_full_fx.call_tool(
        "get_collection_queryables",
        {"collection_id": "public.nonexistent"},
    )

    sc = result.structured_content
    assert "error" in sc
    assert sc["status"] == 404


async def test_get_collection_queryables_schema_prefix_normalization(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """collection_id without dot is normalized to public.<id> in URL path."""
    mock_httpx_request.return_value = _resp(200, _QUERYABLES_BODY)

    await mcp_full_fx.call_tool(
        "get_collection_queryables",
        {"collection_id": "flood_extent_emsr864"},
    )

    call_args = mock_httpx_request.call_args
    path = call_args.args[1] if call_args.args else call_args[0][1]
    assert "public.flood_extent_emsr864" in path
