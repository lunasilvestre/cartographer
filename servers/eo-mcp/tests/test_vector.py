"""Unit tests for the vector tools (v0.3).

Mirrors the structure of test_raster.py — same mock pattern, same assertion style.
Each tool gets: happy path, error path, and at least one edge case.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError


def _resp(status: int, body: dict | list | None = None, text: str = "") -> MagicMock:
    """Build a fake httpx response."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.text = text
    r.url = f"https://fake.example/vector/test?status={status}"
    r.headers = {}
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

# The PostGIS utility tables tipg exposes alongside real data. Names verified
# live against https://api.cheias.pt/vector/collections on 2026-07-31.
_COLLECTIONS_WITH_INTERNALS = {
    "collections": _COLLECTIONS_BODY["collections"]
    + [
        {"id": "public.postgis_srs", "title": "public.postgis_srs"},
        {"id": "public.postgis_srs_all", "title": "public.postgis_srs_all"},
        {"id": "public.st_hexagongrid", "title": "public.st_hexagongrid"},
        {"id": "public.st_squaregrid", "title": "public.st_squaregrid"},
        {"id": "public.st_subdivide", "title": "public.st_subdivide"},
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


async def test_list_vector_collections_hides_postgis_utility_tables(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """PostGIS function-backed tables are noise an agent will try to query."""
    mock_httpx_request.return_value = _resp(200, _COLLECTIONS_WITH_INTERNALS)

    result = await mcp_full_fx.call_tool("list_vector_collections", {})

    sc = result.structured_content
    ids = [c["id"] for c in sc["collections"]]
    assert ids == ["public.flood_extent_emsr864", "public.flood_extent_emsr861"]
    # Hidden, but not hidden silently.
    assert sorted(sc["excluded_internal"]) == [
        "public.postgis_srs",
        "public.postgis_srs_all",
        "public.st_hexagongrid",
        "public.st_squaregrid",
        "public.st_subdivide",
    ]


async def test_list_vector_collections_include_internal(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(200, _COLLECTIONS_WITH_INTERNALS)

    result = await mcp_full_fx.call_tool("list_vector_collections", {"include_internal": True})

    sc = result.structured_content
    assert len(sc["collections"]) == 7
    assert sc["excluded_internal"] == []


async def test_list_vector_collections_404(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(404, text="not found")

    with pytest.raises(ToolError, match="404"):
        await mcp_full_fx.call_tool("list_vector_collections", {})


async def test_list_vector_collections_500(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(500, text="internal server error")

    with pytest.raises(ToolError, match="500"):
        await mcp_full_fx.call_tool("list_vector_collections", {})


async def test_list_vector_collections_malformed_payload(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """A 200 without the 'collections' key names the missing field."""
    mock_httpx_request.return_value = _resp(200, {"unexpected": True})

    with pytest.raises(ToolError, match="missing the expected 'collections' field"):
        await mcp_full_fx.call_tool("list_vector_collections", {})


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
                "datetime": "2026-02-08T00:00:00+00:00",
                "area_ha": 150.0,
                "event_type": "Flooded area",
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
    assert sc["features"][0]["properties"]["datetime"] == "2026-02-08T00:00:00+00:00"


async def test_get_vector_features_404(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(404, text="collection not found")

    with pytest.raises(ToolError, match="404"):
        await mcp_full_fx.call_tool(
            "get_vector_features",
            {"collection_id": "public.nonexistent"},
        )


async def test_get_vector_features_rejects_bad_bbox(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    with pytest.raises(ToolError, match="bbox needs exactly 4"):
        await mcp_full_fx.call_tool(
            "get_vector_features",
            {"collection_id": "public.flood_extent_emsr864", "bbox": [-8.5, 39.0, -7.5]},
        )

    assert not mock_httpx_request.called


async def test_get_vector_features_rejects_bad_limit(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    with pytest.raises(ToolError, match="limit must be at least 1"):
        await mcp_full_fx.call_tool(
            "get_vector_features",
            {"collection_id": "public.flood_extent_emsr864", "limit": 0},
        )

    assert not mock_httpx_request.called


async def test_get_vector_features_rejects_negative_offset(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    with pytest.raises(ToolError, match="offset cannot be negative"):
        await mcp_full_fx.call_tool(
            "get_vector_features",
            {"collection_id": "public.flood_extent_emsr864", "offset": -5},
        )

    assert not mock_httpx_request.called


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

    params = mock_httpx_request.call_args.kwargs.get("params", [])
    bbox_val = next((v for k, v in params if k == "bbox"), None)
    assert bbox_val == "-8.5,39.0,-7.5,39.8"


async def test_get_vector_features_filter_lang_query_key(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """filter_lang should be passed as 'filter-lang' (hyphenated) in the query."""
    mock_httpx_request.return_value = _resp(200, _FEATURES_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_features",
        {
            "collection_id": "public.flood_extent_emsr864",
            "filter": "datetime='2026-02-08'",
            "filter_lang": "cql2-text",
        },
    )

    params = mock_httpx_request.call_args.kwargs.get("params", [])
    keys = [k for k, v in params]
    assert "filter-lang" in keys
    assert any(k == "filter" and v == "datetime='2026-02-08'" for k, v in params)


async def test_get_vector_features_schema_prefix_normalization(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """collection_id without a dot should have 'public.' prepended in the URL path."""
    mock_httpx_request.return_value = _resp(200, _FEATURES_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_features",
        {"collection_id": "flood_extent_emsr864"},
    )

    path = mock_httpx_request.call_args.args[1]
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
            "sortby": "-datetime",
            "simplify": 0.001,
            "properties": ["ogc_fid", "area_ha"],
            "geom_column": "geom",
        },
    )

    params = dict(mock_httpx_request.call_args.kwargs.get("params", []))
    assert params["limit"] == 5
    assert params["offset"] == 10
    assert params["sortby"] == "-datetime"
    assert params["simplify"] == 0.001
    assert params["properties"] == "ogc_fid,area_ha"
    assert params["geom-column"] == "geom"


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
    """Empty FeatureCollection from tipg raises, rather than returning a dict."""
    mock_httpx_request.return_value = _resp(200, {"type": "FeatureCollection", "features": []})

    with pytest.raises(ToolError, match="no feature with ogc_fid=99999"):
        await mcp_full_fx.call_tool(
            "get_vector_feature",
            {"collection_id": "public.flood_extent_emsr864", "feature_id": "99999"},
        )


async def test_get_vector_feature_backend_error(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Upstream tipg error should be surfaced."""
    mock_httpx_request.return_value = _resp(500, text="backend down")

    with pytest.raises(ToolError, match="500"):
        await mcp_full_fx.call_tool(
            "get_vector_feature",
            {"collection_id": "public.flood_extent_emsr864", "feature_id": "42"},
        )


async def test_get_vector_feature_uses_ogc_fid_filter(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Must query /items with an ogc_fid CQL2 filter (not /items/{id} path)."""
    mock_httpx_request.return_value = _resp(200, _SINGLE_FEATURE_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_feature",
        {"collection_id": "public.flood_extent_emsr864", "feature_id": "42"},
    )

    call_args = mock_httpx_request.call_args
    path = call_args.args[1]
    assert "public.flood_extent_emsr864" in path
    assert path.endswith("/items")
    assert "/items/42" not in path
    param_dict = dict(call_args.kwargs.get("params", []))
    assert param_dict.get("filter") == "ogc_fid=42"
    assert param_dict.get("filter-lang") == "cql2-text"


async def test_get_vector_feature_schema_prefix_normalization(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """collection_id without dot should be normalized to public.<id>."""
    mock_httpx_request.return_value = _resp(200, _SINGLE_FEATURE_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_feature",
        {"collection_id": "flood_extent_emsr864", "feature_id": "42"},
    )

    path = mock_httpx_request.call_args.args[1]
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
    "vector_layers": [{"id": "default", "fields": {"datetime": "String"}}],
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


def _tile_router(tilejson=_TILEJSON_BODY, style=_STYLE_BODY, tilejson_status=200, style_status=200):
    def _router(method, path, **kw):
        if "style.json" in path:
            return _resp(style_status, style, text="style not found")
        return _resp(tilejson_status, tilejson, text="collection not found")

    return _router


async def test_get_vector_tile_url_happy(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """Both tilejson and style should be fetched; result has both keys."""
    mock_httpx_request.side_effect = _tile_router()

    result = await mcp_full_fx.call_tool(
        "get_vector_tile_url",
        {"collection_id": "public.flood_extent_emsr864"},
    )

    sc = result.structured_content
    assert "tile_url_template" in sc
    assert "bounds" in sc
    assert "minzoom" in sc
    assert "maxzoom" in sc
    assert sc["style"] == _STYLE_BODY


async def test_get_vector_tile_url_404(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.side_effect = _tile_router(tilejson_status=404, style_status=404)

    with pytest.raises(ToolError, match="404"):
        await mcp_full_fx.call_tool(
            "get_vector_tile_url",
            {"collection_id": "public.nonexistent"},
        )


async def test_get_vector_tile_url_style_404_does_not_kill_call(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """A 404 on style.json should not prevent the tilejson result from being returned.
    Style should become None in the result."""
    mock_httpx_request.side_effect = _tile_router(style_status=404)

    result = await mcp_full_fx.call_tool(
        "get_vector_tile_url",
        {"collection_id": "public.flood_extent_emsr864"},
    )

    sc = result.structured_content
    assert "tile_url_template" in sc
    # Style must be None (not missing, and not a reason to fail).
    assert sc.get("style") is None


async def test_get_vector_tile_url_missing_bounds_raises(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    incomplete = {k: v for k, v in _TILEJSON_BODY.items() if k != "bounds"}
    mock_httpx_request.side_effect = _tile_router(tilejson=incomplete)

    with pytest.raises(ToolError, match="missing the expected 'bounds' field"):
        await mcp_full_fx.call_tool(
            "get_vector_tile_url",
            {"collection_id": "public.flood_extent_emsr864"},
        )


async def test_get_vector_tile_url_schema_prefix_normalization(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """collection_id without dot is normalized to public.<id>."""
    mock_httpx_request.side_effect = _tile_router()

    await mcp_full_fx.call_tool(
        "get_vector_tile_url",
        {"collection_id": "flood_extent_emsr864"},
    )

    path = mock_httpx_request.call_args_list[0].args[1]
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
        "datetime": {"title": "datetime", "type": "string", "format": "date-time"},
        "area_ha": {"title": "area_ha", "type": "number"},
        "event_type": {"title": "event_type", "type": "string"},
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
    assert "datetime" in sc["properties"]
    assert "area_ha" in sc["properties"]


async def test_get_collection_queryables_uses_schemajson(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """tipg only emits the JSON-schema form under ?f=schemajson."""
    mock_httpx_request.return_value = _resp(200, _QUERYABLES_BODY)

    await mcp_full_fx.call_tool(
        "get_collection_queryables",
        {"collection_id": "public.flood_extent_emsr864"},
    )

    assert mock_httpx_request.call_args.kwargs.get("params") == {"f": "schemajson"}


async def test_get_collection_queryables_404(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.return_value = _resp(404, text="collection not found")

    with pytest.raises(ToolError, match="404"):
        await mcp_full_fx.call_tool(
            "get_collection_queryables",
            {"collection_id": "public.nonexistent"},
        )


async def test_get_collection_queryables_schema_prefix_normalization(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    """collection_id without dot is normalized to public.<id> in URL path."""
    mock_httpx_request.return_value = _resp(200, _QUERYABLES_BODY)

    await mcp_full_fx.call_tool(
        "get_collection_queryables",
        {"collection_id": "flood_extent_emsr864"},
    )

    path = mock_httpx_request.call_args.args[1]
    assert "public.flood_extent_emsr864" in path


async def test_get_vector_tile_url_empty_tiles_raises(mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock):
    mock_httpx_request.side_effect = _tile_router(tilejson={**_TILEJSON_BODY, "tiles": []})

    with pytest.raises(ToolError, match="empty 'tiles' list"):
        await mcp_full_fx.call_tool(
            "get_vector_tile_url",
            {"collection_id": "public.flood_extent_emsr864"},
        )


async def test_get_vector_tile_url_unexpected_style_exception_escapes(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """A backend error on style.json is tolerated; a bug in our own code is not.

    Only a ToolError from the style request degrades to `style: null`. Anything
    else propagates — FastMCP wraps it, so the call fails rather than quietly
    returning a result with a missing style.
    """

    def _router(method, path, **kw):
        if "style.json" in path:
            raise RuntimeError("a real defect, not a backend failure")
        return _resp(200, _TILEJSON_BODY)

    mock_httpx_request.side_effect = _router

    with pytest.raises(ToolError, match="a real defect"):
        await mcp_full_fx.call_tool(
            "get_vector_tile_url",
            {"collection_id": "public.flood_extent_emsr864"},
        )


# ---------------------------------------------------------------------------
# feature_id must be an integer (CQL2 injection)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["1 OR 1=1", "1; DROP TABLE x", "abc", "", "1 OR area_ha>0", "'1'"],
)
async def test_get_vector_feature_rejects_non_integer_ids(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock, bad_id: str
):
    """feature_id is interpolated into CQL2, so it must be proven to be an int.

    Verified live on 2026-07-31: `filter=ogc_fid=1 OR 1=1` matches all 5,496
    rows of public.flood_extent_emsr864, and limit=1 then returns ogc_fid 3500
    — a different feature than was asked for, reported as an ordinary success.
    """
    with pytest.raises(ToolError, match="must be an integer ogc_fid"):
        await mcp_full_fx.call_tool(
            "get_vector_feature",
            {"collection_id": "public.flood_extent_emsr864", "feature_id": bad_id},
        )

    assert not mock_httpx_request.called


@pytest.mark.parametrize("good_id,expected", [("42", 42), (" 42 ", 42), ("-7", -7)])
async def test_get_vector_feature_accepts_integer_ids(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock, good_id: str, expected: int
):
    mock_httpx_request.return_value = _resp(200, _SINGLE_FEATURE_BODY)

    await mcp_full_fx.call_tool(
        "get_vector_feature",
        {"collection_id": "public.flood_extent_emsr864", "feature_id": good_id},
    )

    params = dict(mock_httpx_request.call_args.kwargs.get("params", []))
    assert params["filter"] == f"ogc_fid={expected}"


# ---------------------------------------------------------------------------
# tipg's self-referential id/links are stripped
# ---------------------------------------------------------------------------

_BLOATED_FEATURE = {
    "type": "Feature",
    # tipg sets this to the WKB hex of the geometry; on a real flood polygon it
    # and `links` are 95% of the payload. Verified live 2026-07-31.
    "id": "0103000020E6100000" + "AB" * 400,
    "links": [{"rel": "self", "href": "https://fake.example/vector/items/" + "AB" * 400}],
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    "properties": {"ogc_fid": 42, "area_ha": 150.0},
}


async def test_get_vector_features_strips_tipg_id_and_links(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    mock_httpx_request.return_value = _resp(
        200, {"type": "FeatureCollection", "features": [_BLOATED_FEATURE], "numberMatched": 1}
    )

    result = await mcp_full_fx.call_tool(
        "get_vector_features", {"collection_id": "public.flood_extent_emsr864"}
    )

    feature = result.structured_content["features"][0]
    assert "id" not in feature
    assert "links" not in feature
    # The usable identifier survives, and it is the one get_vector_feature takes.
    assert feature["properties"]["ogc_fid"] == 42
    assert feature["geometry"] == _BLOATED_FEATURE["geometry"]


async def test_get_vector_feature_strips_tipg_id_and_links(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    mock_httpx_request.return_value = _resp(
        200, {"type": "FeatureCollection", "features": [_BLOATED_FEATURE]}
    )

    result = await mcp_full_fx.call_tool(
        "get_vector_feature",
        {"collection_id": "public.flood_extent_emsr864", "feature_id": "42"},
    )

    sc = result.structured_content
    assert "id" not in sc
    assert "links" not in sc
    assert sc["properties"]["ogc_fid"] == 42


# ---------------------------------------------------------------------------
# Truncation must be visible
# ---------------------------------------------------------------------------


async def test_get_vector_features_flags_a_truncated_page(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """A page presented as the whole answer is the error-dict bug in another costume."""
    mock_httpx_request.return_value = _resp(
        200, {**_FEATURES_BODY, "numberMatched": 5496, "numberReturned": 1}
    )

    result = await mcp_full_fx.call_tool(
        "get_vector_features", {"collection_id": "public.flood_extent_emsr864", "limit": 1}
    )

    sc = result.structured_content
    assert sc["truncated"] is True
    assert "5496" in sc["note"]
    # The warning precedes the data a reader would otherwise summarise.
    keys = list(sc)
    assert keys.index("truncated") < keys.index("features")
    assert keys.index("note") < keys.index("features")


async def test_get_vector_features_complete_result_is_not_flagged(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    mock_httpx_request.return_value = _resp(200, _FEATURES_BODY)

    result = await mcp_full_fx.call_tool(
        "get_vector_features", {"collection_id": "public.flood_extent_emsr864"}
    )

    sc = result.structured_content
    assert sc["truncated"] is False
    assert "note" not in sc


async def test_list_vector_collections_sends_an_explicit_limit(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """tipg returns all 13 today, but a bigger deployment must not be truncated."""
    mock_httpx_request.return_value = _resp(200, {**_COLLECTIONS_BODY, "links": []})

    await mcp_full_fx.call_tool("list_vector_collections", {})

    params = dict(mock_httpx_request.call_args.kwargs.get("params", {}))
    assert params["limit"] == 100


def test_slim_feature_passes_through_a_non_dict():
    """Defensive: a backend that returns a non-object in `features` must not crash."""
    from eo_mcp.tools.vector import slim_feature

    assert slim_feature("not a feature") == "not a feature"


async def test_get_vector_features_rejects_a_payload_without_features(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """A response missing `features` is broken, not empty, and must say so."""
    mock_httpx_request.return_value = _resp(200, {"numberMatched": 3})

    with pytest.raises(ToolError, match="missing the expected 'features' field"):
        await mcp_full_fx.call_tool(
            "get_vector_features", {"collection_id": "public.flood_extent_emsr864"}
        )


# ---------------------------------------------------------------------------
# simplify can null a geometry entirely; that must not be silent
# ---------------------------------------------------------------------------

_NULL_GEOM_FEATURE = {
    "type": "Feature",
    "geometry": None,
    "properties": {"ogc_fid": 7, "area_ha": 2.3},
}


async def test_null_geometries_are_counted_and_explained(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """tipg drops geometry rather than coarsening it past some tolerance.

    Measured live on public.flood_extent_emsr864 (2026-07-31, limit=500):
    simplify=0.0001 nulls 0, 0.0005 nulls 143 (29%), 0.001 nulls 305 (61%),
    0.01 nulls 481 (96%). HTTP 200 throughout, properties intact — so a feature
    with no geometry still looks present. A count is the only way a reader sees
    it.
    """
    body = {
        "type": "FeatureCollection",
        "features": [_NULL_GEOM_FEATURE, _FEATURES_BODY["features"][0]],
        "numberMatched": 2,
    }
    mock_httpx_request.return_value = _resp(200, body)

    result = await mcp_full_fx.call_tool(
        "get_vector_features",
        {"collection_id": "public.flood_extent_emsr864", "simplify": 0.001},
    )

    sc = result.structured_content
    assert sc["null_geometry_count"] == 1
    assert "simplify=0.001 is too coarse" in sc["null_geometry_note"]
    assert "0.0001" in sc["null_geometry_note"]
    # Ahead of the features a reader would otherwise take at face value.
    keys = list(sc)
    assert keys.index("null_geometry_count") < keys.index("features")


async def test_null_geometry_without_simplify_is_still_reported(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    """A null geometry that is not our doing is still a missing feature."""
    mock_httpx_request.return_value = _resp(
        200, {"type": "FeatureCollection", "features": [_NULL_GEOM_FEATURE], "numberMatched": 1}
    )

    result = await mcp_full_fx.call_tool(
        "get_vector_features", {"collection_id": "public.flood_extent_emsr864"}
    )

    sc = result.structured_content
    assert sc["null_geometry_count"] == 1
    assert "simplify" not in sc["null_geometry_note"]
    assert "from the backend" in sc["null_geometry_note"]


async def test_no_null_geometry_key_when_every_geometry_survived(
    mcp_full_fx: FastMCP, mock_httpx_request: AsyncMock
):
    mock_httpx_request.return_value = _resp(200, _FEATURES_BODY)

    result = await mcp_full_fx.call_tool(
        "get_vector_features",
        {"collection_id": "public.flood_extent_emsr864", "simplify": 0.0001},
    )

    assert "null_geometry_count" not in result.structured_content
