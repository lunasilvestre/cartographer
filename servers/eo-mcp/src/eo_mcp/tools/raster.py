from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


async def _get_client(settings) -> httpx.AsyncClient:
    global _client
    if _client is None:
        headers = {}
        if settings.auth_token:
            headers["Authorization"] = f"Bearer {settings.auth_token}"
        _client = httpx.AsyncClient(
            base_url=settings.raster_url,
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


def register(mcp, settings) -> None:
    @mcp.tool()
    async def get_raster_info(
        collection_id: str,
        item_id: str,
        asset: str = "data",
    ) -> dict:
        """Get band info, data types, CRS, nodata values, bounds, and dimensions for a raster asset of a STAC item. Use this to understand what data is available before running analysis."""
        client = await _get_client(settings)
        path = f"/collections/{collection_id}/items/{item_id}/info"
        return await _request(client, "GET", path, params={"assets": asset})

    @mcp.tool()
    async def get_tile_url(
        collection_id: str,
        item_id: str,
        asset: str = "data",
        color_formula: str | None = None,
        colormap: str | None = None,
        rescale: str | None = None,
        bidx: list[int] | None = None,
    ) -> dict:
        """Generate a TiTiler tilejson URL for rendering a raster asset on a map. Returns tilejson_url, tile_url_template, bounds, minzoom, and maxzoom. Use color_formula for rio-color expressions (e.g. "Gamma RGB 3.5 Saturation 1.7"), colormap for named colormaps (e.g. "viridis", "terrain"), rescale for min/max stretch (e.g. "0,3000"), and bidx for band selection (e.g. [4,3,2] for false color)."""
        client = await _get_client(settings)
        path = f"/collections/{collection_id}/items/{item_id}/WebMercatorQuad/tilejson.json"

        params: list[tuple[str, str | int]] = [("assets", asset)]
        if color_formula is not None:
            params.append(("color_formula", color_formula))
        if colormap is not None:
            params.append(("colormap_name", colormap))
        if rescale is not None:
            params.append(("rescale", rescale))
        if bidx is not None:
            for b in bidx:
                params.append(("bidx", b))

        result = await _request(client, "GET", path, params=params)
        if "error" in result:
            return result

        return {
            "tilejson_url": str(client.base_url).rstrip("/") + path
            + "?"
            + "&".join(f"{k}={v}" for k, v in params),
            "tile_url_template": result["tiles"][0],
            "bounds": result["bounds"],
            "center": result.get("center"),
            "minzoom": result["minzoom"],
            "maxzoom": result["maxzoom"],
        }

    @mcp.tool()
    async def get_zonal_statistics(
        collection_id: str,
        item_id: str,
        geojson: dict,
        asset: str = "data",
        categorical: bool = False,
        histogram_bins: int | None = None,
    ) -> dict:
        """Compute raster statistics (mean, min, max, std, count, histogram, percentiles) for an area of interest. Provide a GeoJSON Polygon, MultiPolygon, or Feature as the AOI. Computation is server-side via TiTiler — no raster data is transferred. Use categorical=True for classification rasters (land cover, flood masks)."""
        geojson_type = geojson.get("type")
        if geojson_type in ("Polygon", "MultiPolygon"):
            body = {"type": "Feature", "geometry": geojson, "properties": {}}
        elif geojson_type == "Feature":
            body = geojson
        elif geojson_type == "FeatureCollection":
            features = geojson.get("features", [])
            if not features:
                return {"error": "FeatureCollection has no features", "status": 400}
            body = features[0]
        else:
            return {
                "error": "geojson must be a Polygon, MultiPolygon, Feature, or FeatureCollection",
                "status": 400,
            }

        client = await _get_client(settings)
        path = f"/collections/{collection_id}/items/{item_id}/statistics"

        params: list[tuple[str, str]] = [("assets", asset)]
        if categorical:
            params.append(("categorical", "true"))
        if histogram_bins is not None:
            params.append(("histogram_bins", str(histogram_bins)))

        return await _request(client, "POST", path, params=params, json=body)
