from __future__ import annotations

import asyncio

import httpx

_client: httpx.AsyncClient | None = None


async def _get_client(settings) -> httpx.AsyncClient:
    global _client
    if _client is None:
        headers = {}
        if settings.auth_token:
            headers["Authorization"] = f"Bearer {settings.auth_token}"
        _client = httpx.AsyncClient(
            base_url=settings.vector_url,
            timeout=settings.request_timeout,
            headers=headers,
        )
    return _client


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params=None,
    json=None,
) -> dict:
    r = await client.request(method, path, params=params, json=json)
    if r.status_code >= 400:
        return {"error": r.text, "status": r.status_code, "url": str(r.url)}
    return r.json()


def _normalize_collection_id(collection_id: str) -> str:
    """Prepend 'public.' schema prefix if no dot present in collection_id."""
    if "." not in collection_id:
        return f"public.{collection_id}"
    return collection_id


def register(mcp, settings) -> None:
    @mcp.tool()
    async def list_vector_collections() -> dict:
        """List available vector data collections from the OGC Features API.

        Returns collection IDs, titles, descriptions, spatial extents, and
        temporal ranges. Use this to discover what vector data is available
        before querying features.
        """
        client = await _get_client(settings)
        result = await _request(client, "GET", "/collections", params={"f": "json"})
        if "error" in result:
            return result

        simplified = []
        for col in result.get("collections", []):
            extent = col.get("extent", {})
            spatial = extent.get("spatial", {})
            temporal = extent.get("temporal", {})
            simplified.append(
                {
                    "id": col.get("id"),
                    "title": col.get("title"),
                    "description": col.get("description"),
                    "extent_spatial": spatial.get("bbox"),
                    "extent_temporal": temporal.get("interval"),
                }
            )
        return {"collections": simplified}

    @mcp.tool()
    async def get_vector_features(
        collection_id: str,
        limit: int = 10,
        offset: int = 0,
        bbox: list[float] | None = None,
        datetime: str | None = None,
        filter: str | None = None,
        filter_lang: str = "cql2-text",
        properties: list[str] | None = None,
        simplify: float | None = None,
        sortby: str | None = None,
        geom_column: str | None = None,
    ) -> dict:
        """Query vector features from a PostGIS collection with optional spatial,
        temporal, and attribute filters.

        Returns GeoJSON FeatureCollection. Use bbox for spatial filtering (4 floats:
        west,south,east,north). Use datetime for temporal filtering (ISO 8601 instant
        or interval like '2026-02-01/2026-02-10'). Use filter with CQL2 expressions
        for attribute filtering (e.g. "event_date='2026-02-08'" or "area_ha > 100").
        Use simplify (decimal degrees) to reduce geometry complexity for large polygons.
        Use properties to select specific columns (reduces payload).
        Use sortby for ordering (prefix with - for descending, e.g. '-event_date').
        Limit defaults to 10; max depends on server config (typically 10000).
        """
        collection_id = _normalize_collection_id(collection_id)
        client = await _get_client(settings)
        path = f"/collections/{collection_id}/items"

        params: list[tuple[str, str | int | float]] = [
            ("f", "geojson"),
            ("limit", limit),
        ]
        if offset > 0:
            params.append(("offset", offset))
        if bbox is not None:
            params.append(("bbox", ",".join(str(v) for v in bbox)))
        if datetime is not None:
            params.append(("datetime", datetime))
        if filter is not None:
            params.append(("filter", filter))
            params.append(("filter-lang", filter_lang))
        if properties is not None:
            params.append(("properties", ",".join(properties)))
        if simplify is not None:
            params.append(("simplify", simplify))
        if sortby is not None:
            params.append(("sortby", sortby))
        if geom_column is not None:
            params.append(("geom-column", geom_column))

        return await _request(client, "GET", path, params=params)

    @mcp.tool()
    async def get_vector_feature(
        collection_id: str,
        feature_id: str,
    ) -> dict:
        """Get a single vector feature by ogc_fid from a PostGIS collection.

        Returns a GeoJSON Feature with all properties and full geometry.
        feature_id is the integer primary key (ogc_fid). The tipg /items/{id}
        endpoint is not usable against PostGIS tables (the tile-layer id is a
        WKB hex string that does not round-trip), so this tool queries via
        ogc_fid CQL2 filter and returns the first feature.
        """
        collection_id = _normalize_collection_id(collection_id)
        client = await _get_client(settings)
        path = f"/collections/{collection_id}/items"
        params = [
            ("f", "geojson"),
            ("filter", f"ogc_fid={feature_id}"),
            ("filter-lang", "cql2-text"),
            ("limit", 1),
        ]
        result = await _request(client, "GET", path, params=params)
        if "error" in result:
            return result
        features = result.get("features", [])
        if not features:
            return {"error": f"feature {feature_id} not found", "status": 404}
        return features[0]

    @mcp.tool()
    async def get_vector_tile_url(
        collection_id: str,
    ) -> dict:
        """Generate a vector tile URL template for rendering a PostGIS collection on a map.

        Returns a TileJSON document with tile URL template, bounds, min/max zoom,
        and a default MapLibre style. The tiles use MVT (Mapbox Vector Tiles) format
        with source layer named 'default'.
        """
        collection_id = _normalize_collection_id(collection_id)
        client = await _get_client(settings)
        tilejson_path = f"/collections/{collection_id}/tiles/WebMercatorQuad/tilejson.json"
        style_path = f"/collections/{collection_id}/tiles/WebMercatorQuad/style.json"

        tilejson_result, style_result = await asyncio.gather(
            _request(client, "GET", tilejson_path),
            _request(client, "GET", style_path),
        )

        if "error" in tilejson_result:
            return tilejson_result

        style = None if "error" in style_result else style_result

        return {
            "tilejson": tilejson_result,
            "style": style,
            "tile_url_template": tilejson_result["tiles"][0],
            "bounds": tilejson_result["bounds"],
            "minzoom": tilejson_result["minzoom"],
            "maxzoom": tilejson_result["maxzoom"],
        }

    @mcp.tool()
    async def get_collection_queryables(
        collection_id: str,
    ) -> dict:
        """Get the filterable properties (queryables) for a vector collection.

        Returns property names, types, and descriptions. Use this to understand
        what CQL2 filters are available before calling get_vector_features with
        a filter expression.
        """
        collection_id = _normalize_collection_id(collection_id)
        client = await _get_client(settings)
        path = f"/collections/{collection_id}/queryables"
        return await _request(client, "GET", path, params={"f": "schemajson"})
