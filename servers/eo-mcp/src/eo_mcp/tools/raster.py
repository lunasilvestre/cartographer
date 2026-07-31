"""Raster tools: thin proxies onto TiTiler.

Failures raise ``fastmcp.exceptions.ToolError``; see :mod:`eo_mcp.http` for the
full error contract.
"""

from __future__ import annotations

from typing import Any

from fastmcp.exceptions import ToolError

from ..http import Backends, require, rescale_covers, rescale_low_contrast

_AREA_TYPES = ("Polygon", "MultiPolygon")


def _minimal_feature(feature: dict) -> dict:
    """Reduce a Feature to what TiTiler actually reads: geometry and properties.

    tipg stamps every feature with an ``id`` that is the WKB hex of its own
    geometry, and a ``links`` array embedding that same hex in a URL. On a real
    flood polygon those two fields are **95% of the payload** — 5.96 MB for a
    feature whose geometry is 1.82 MB, measured against
    ``public.flood_extent_emsr864`` on 2026-07-31. Passing a Feature straight
    from ``get_vector_features`` into ``get_zonal_statistics`` therefore blew
    past the backend's request-size limit for reasons that had nothing to do
    with the geometry.

    Both fields are useless here: the id is the one documented in
    ``docs/tipg-api-notes.md`` as not round-tripping, and the links point back
    at it. Dropping them is lossless. ``properties`` is kept — it is small, and
    it is how a caller matches a result back to the feature that produced it.
    """
    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": feature.get("properties") or {},
    }


def statistics_body(geojson: dict) -> dict:
    """Validate an area-of-interest and shape it into a TiTiler request body.

    A bare geometry is wrapped into a Feature. A Feature is reduced to geometry
    plus properties. A FeatureCollection keeps **every** feature: TiTiler
    computes statistics per feature and returns a FeatureCollection, so there is
    no need — and no excuse — to silently analyse only the first one.
    """
    if not isinstance(geojson, dict):
        raise ToolError(
            f"eo-mcp: geojson must be a GeoJSON object, got {type(geojson).__name__}."
        )

    geojson_type = geojson.get("type")

    if geojson_type in _AREA_TYPES:
        return {"type": "Feature", "geometry": geojson, "properties": {}}

    if geojson_type == "Feature":
        _require_area_geometry(geojson, where="the Feature")
        return _minimal_feature(geojson)

    if geojson_type == "FeatureCollection":
        features = geojson.get("features") or []
        if not features:
            raise ToolError(
                "eo-mcp: the FeatureCollection has no features, so there is no "
                "area to compute statistics over."
            )
        for index, feature in enumerate(features):
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ToolError(
                    f"eo-mcp: features[{index}] of the FeatureCollection is not "
                    f"a GeoJSON Feature."
                )
            _require_area_geometry(feature, where=f"features[{index}]")
        return {
            "type": "FeatureCollection",
            "features": [_minimal_feature(f) for f in features],
        }

    raise ToolError(
        f"eo-mcp: geojson must be a Polygon, MultiPolygon, Feature, or "
        f"FeatureCollection; got type={geojson_type!r}. Statistics are computed "
        f"over an area, so points and lines cannot be used."
    )


def _require_area_geometry(feature: dict, *, where: str) -> None:
    geometry = feature.get("geometry")
    geometry_type = geometry.get("type") if isinstance(geometry, dict) else None
    if geometry_type not in _AREA_TYPES:
        raise ToolError(
            f"eo-mcp: the geometry of {where} is type={geometry_type!r}, but "
            f"statistics require a Polygon or MultiPolygon."
        )


def register(mcp, backends: Backends) -> None:
    raster = backends.raster

    @mcp.tool()
    async def get_raster_info(
        collection_id: str,
        item_id: str,
        asset: str = "data",
    ) -> dict:
        """Get band info, data types, CRS, nodata values, bounds, and dimensions for a raster asset of a STAC item. Use this to understand what data is available before running analysis."""
        return await raster.request(
            "GET",
            f"/collections/{collection_id}/items/{item_id}/info",
            params={"assets": asset},
        )

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
        """Generate a TiTiler tilejson URL for rendering a raster asset on a map. Returns tilejson_url, tile_url_template, bounds, minzoom, maxzoom, and a `render` block recording exactly which styling was applied and where each value came from.

        Styling defaults come from the STAC collection when it publishes `cheias:rescale` and `cheias:colormap`, so you do not have to guess. Precedence: an argument you pass wins; otherwise the collection's value is used **if it spans this item's data range**; otherwise the item's own 2nd–98th percentiles are used. That check matters: `precipitation-daily` publishes `[0, 80]` while its `precipitation-storm-total` item runs 100–582, and applying the collection value there clamps every pixel and renders one flat colour with no error. A collection ramp that is merely *wider* than the item's data is kept, not overridden — the shared scale is what makes items comparable, and per-item rescaling would render a dry day and a wet day identically — but `render.rescale_note` will tell you contrast is low and give you the value to pass for a single-item view. `render.rescale_source` says which rule fired and `render.item_data_range` gives the numbers behind it; null there means the item published no usable percentiles, so the collection value was taken on trust and could not be checked.

        Use colormap for named colormaps (e.g. "viridis", "blues", "terrain"), rescale for min/max stretch (e.g. "0,80"), and bidx for band selection (e.g. [4,3,2] for false color). color_formula takes rio-color expressions, but note that RGB operations such as "Gamma RGB 3.5 Saturation 1.7" require a 3-band asset and return HTTP 500 on the single-band float rasters in this deployment.
        """
        defaults = await backends.render_defaults(collection_id)
        applied_from_collection = []
        rescale_source = "caller" if rescale is not None else "none"

        if colormap is None and "colormap" in defaults:
            colormap = defaults["colormap"]
            applied_from_collection.append("colormap")

        data_range = None
        rescale_note = None
        if rescale is None and "rescale" in defaults:
            data_range = await backends.item_data_range(collection_id, item_id, asset)
            if rescale_covers(defaults["rescale"], data_range):
                rescale = defaults["rescale"]
                rescale_source = "collection-metadata"
                applied_from_collection.append("rescale")
                if rescale_low_contrast(rescale, data_range):
                    # Advisory only. A wide collection ramp is usually
                    # deliberate — it is what makes sibling items comparable —
                    # so this reports rather than overrides.
                    rescale_note = (
                        f"This item's data ({data_range[0]:g} to {data_range[1]:g}) "
                        f"occupies less than a third of the collection's published "
                        f"rescale {rescale}, so the tile will render with few "
                        f"distinct tones. That is usually intended: the shared "
                        f"scale is what makes items in this collection comparable "
                        f"to each other, and rescaling per item would make a dry "
                        f"day and a wet day look identical. For a single-item view "
                        f"where contrast matters more than comparability, pass "
                        f"rescale='{data_range[0]:g},{data_range[1]:g}'."
                    )
            elif data_range is not None:
                # The collection scale does not span this item at all, so every
                # pixel clamps and the tile renders as one flat colour. That is
                # broken rather than merely dim, and worth overriding.
                rescale = f"{data_range[0]},{data_range[1]}"
                rescale_source = "item-percentiles"
                rescale_note = (
                    f"The collection publishes cheias:rescale "
                    f"{defaults['rescale']}, which does not span this item's data "
                    f"({data_range[0]:g} to {data_range[1]:g}) — applying it would "
                    f"clamp every pixel and render a flat image. Using the item's "
                    f"own 2nd-98th percentiles instead. Note this tile is NOT on "
                    f"the same scale as other items in this collection."
                )

        path = f"/collections/{collection_id}/items/{item_id}/WebMercatorQuad/tilejson.json"

        params: list[tuple[str, str | int]] = [("assets", asset)]
        if color_formula is not None:
            params.append(("color_formula", color_formula))
        if colormap is not None:
            params.append(("colormap_name", colormap))
        if rescale is not None:
            params.append(("rescale", rescale))
        if bidx is not None:
            for band in bidx:
                params.append(("bidx", band))

        url = raster.url_for(path, params)
        result = await raster.request("GET", path, params=params)

        tiles = require(result, "tiles", source="TiTiler tilejson", url=url)
        if not isinstance(tiles, list) or not tiles:
            raise ToolError(
                f"eo-mcp: TiTiler tilejson response from {url} has an empty "
                f"'tiles' list, so there is no tile URL to return."
            )

        return {
            # Percent-encoded, so a color_formula containing spaces still
            # produces a URL that works when pasted into a client.
            "tilejson_url": url,
            "tile_url_template": tiles[0],
            "bounds": require(result, "bounds", source="TiTiler tilejson", url=url),
            "center": result.get("center"),
            "minzoom": require(result, "minzoom", source="TiTiler tilejson", url=url),
            "maxzoom": require(result, "maxzoom", source="TiTiler tilejson", url=url),
            "render": {
                "asset": asset,
                "colormap": colormap,
                "rescale": rescale,
                "rescale_source": rescale_source,
                "color_formula": color_formula,
                "from_collection_metadata": applied_from_collection,
                # null means the check could not be run — the item published no
                # usable percentiles (satellite-ir is one), so the collection
                # value was taken on trust and cannot be compared against here.
                "item_data_range": list(data_range) if data_range else None,
                "rescale_note": rescale_note,
            },
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
        """Compute raster statistics (mean, min, max, std, count, histogram, percentiles) for an area of interest. Computation is server-side via TiTiler — no raster data is transferred. Use categorical=True for classification rasters (land cover, flood masks).

        `sum` is the arithmetic sum of pixel values, not a total in any physical sense — `sum / count == mean`. For a depth-valued raster such as precipitation in mm, adding depths across pixels gives a number with no meaning; report `mean` instead. A volume would need pixel area, which this server does not compute.

        Provide a GeoJSON Polygon, MultiPolygon, Feature, or FeatureCollection as the AOI. A single geometry or Feature returns one Feature carrying `properties.statistics`. A FeatureCollection returns a FeatureCollection with statistics computed **separately for every feature** — the whole collection is analysed, not just the first feature.
        """
        body = statistics_body(geojson)

        params: list[tuple[str, str]] = [("assets", asset)]
        if categorical:
            params.append(("categorical", "true"))
        if histogram_bins is not None:
            params.append(("histogram_bins", str(histogram_bins)))

        return await statistics(
            backends,
            collection_id=collection_id,
            item_id=item_id,
            body=body,
            params=params,
        )


async def statistics(
    backends: Backends,
    *,
    collection_id: str,
    item_id: str,
    body: Any,
    params: list[tuple[str, str]] | None = None,
) -> Any:
    """POST an AOI to TiTiler's statistics endpoint.

    Shared with the workflow tools. Declared retry-safe: the endpoint computes
    and returns statistics without mutating anything, so repeating it after a
    5xx is correct even though the method is POST.
    """
    return await backends.raster.request(
        "POST",
        f"/collections/{collection_id}/items/{item_id}/statistics",
        params=params if params is not None else [("assets", "data")],
        json=body,
        retry_safe=True,
    )
