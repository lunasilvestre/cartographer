"""Vector tools: thin proxies onto tipg's OGC Features API.

Failures raise ``fastmcp.exceptions.ToolError``; see :mod:`eo_mcp.http` for the
full error contract.
"""

from __future__ import annotations

import asyncio

from fastmcp.exceptions import ToolError

from ..http import Backends, list_all_collections, require

#: tipg exposes PostGIS's own function-backed tables alongside real data. They
#: are named by PostGIS convention, not by this deployment, so matching on the
#: convention generalises to any PostGIS instance. They are hidden by default
#: because an agent shown ``st_hexagongrid`` will eventually try to query it.
_INTERNAL_TABLE_PREFIXES = ("st_", "postgis_", "spatial_ref_sys", "geometry_columns")


def normalize_collection_id(collection_id: str) -> str:
    """Prepend 'public.' schema prefix if no dot present in collection_id."""
    if "." not in collection_id:
        return f"public.{collection_id}"
    return collection_id


def is_internal(collection_id: str) -> bool:
    table = collection_id.split(".", 1)[-1].lower()
    return table.startswith(_INTERNAL_TABLE_PREFIXES)


def slim_feature(feature: dict) -> dict:
    """Drop tipg's self-referential `id` and `links` from a returned feature.

    tipg sets each feature's `id` to the WKB hex of its own geometry and embeds
    that same hex in a `links` URL. Measured against
    `public.flood_extent_emsr864` on 2026-07-31, those two fields are 95% of a
    real flood feature — 5.96 MB for a feature whose geometry is 1.82 MB.

    They are also unusable: `docs/tipg-api-notes.md` records that `items/{id}`
    does not round-trip against PostGIS tables, which is precisely why
    `get_vector_feature` filters on `ogc_fid` instead. Returning 4 MB of dead
    weight into a model's context is a cost with no matching benefit. `ogc_fid`
    stays in `properties`, and that is the id every tool here accepts.
    """
    if not isinstance(feature, dict):
        return feature
    return {k: v for k, v in feature.items() if k not in ("id", "links")}


def register(mcp, backends: Backends) -> None:
    vector = backends.vector

    @mcp.tool()
    async def list_vector_collections(include_internal: bool = False) -> dict:
        """List available vector data collections from the OGC Features API.

        Returns collection IDs, titles, descriptions, spatial extents, and
        temporal ranges. Use this to discover what vector data is available
        before querying features.

        PostGIS ships function-backed utility tables (st_hexagongrid,
        postgis_srs, and friends) in the same schema as real data. They are
        excluded by default and counted under `excluded_internal`; pass
        include_internal=True to see them.
        """
        # Paginated: tipg returns all 13 in one page today, but a deployment
        # with more would be silently truncated at the default page size.
        collections = await list_all_collections(vector, {"f": "json"})

        simplified = []
        excluded = []
        for col in collections:
            col_id = col.get("id") or ""
            if not include_internal and is_internal(col_id):
                excluded.append(col_id)
                continue
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
        return {"collections": simplified, "excluded_internal": excluded}

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
        for attribute filtering (e.g. "datetime='2026-02-08'" or "area_ha > 100").
        Use simplify (decimal degrees) to reduce geometry complexity for large polygons.
        WARNING: past a certain tolerance tipg returns null geometry rather than a
        coarser one, with HTTP 200 and properties intact, so the loss is silent.
        Measured on public.flood_extent_emsr864 (2026-07-31): 0.0001 nulls nothing,
        0.0005 nulls 29%, 0.001 nulls 61%, 0.01 nulls 96%. Prefer 0.0001 — roughly
        11 m, already a 6x size cut. This tool counts nulled geometries for you and
        reports them in `null_geometry_count`; check it before using the result.
        Use properties to select specific columns (reduces payload).
        Use sortby for ordering (prefix with - for descending, e.g. '-datetime').
        Limit defaults to 10; max depends on server config (typically 10000).
        """
        if limit < 1:
            raise ToolError(f"eo-mcp: limit must be at least 1, got {limit}.")
        if offset < 0:
            raise ToolError(f"eo-mcp: offset cannot be negative, got {offset}.")
        if bbox is not None and len(bbox) != 4:
            raise ToolError(
                f"eo-mcp: bbox needs exactly 4 numbers (west, south, east, "
                f"north), got {len(bbox)}."
            )

        collection_id = normalize_collection_id(collection_id)
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

        url = vector.url_for(path, params)
        result = await vector.request("GET", path, params=params)
        # A payload without `features` is not an empty result, it is a broken
        # response; saying so beats returning something that reads as "no data".
        require(result, "features", source="tipg", url=url)

        features = [slim_feature(f) for f in (result.get("features") or [])]
        matched = result.get("numberMatched")
        out = {**result, "features": features}

        warnings: dict[str, object] = {}

        # Past some tolerance tipg drops the geometry entirely rather than
        # coarsening it, and still answers 200 with the properties intact. A
        # feature with no geometry is not a smaller feature, it is a missing
        # one, and nothing else in the payload says so.
        nulled = sum(1 for f in features if f.get("geometry") is None)
        if nulled:
            warnings["null_geometry_count"] = nulled
            warnings["null_geometry_note"] = (
                f"{nulled} of {len(features)} features came back with null "
                f"geometry"
                + (
                    f" — simplify={simplify} is too coarse for this collection. "
                    f"Try 0.0001, which loses none."
                    if simplify is not None
                    else " from the backend."
                )
                + " Those features cannot be used as an area of interest and are "
                "not part of any spatial result derived from this response."
            )

        # A page of results presented as the whole answer is the same bug class
        # as an error dict presented as data. Say so, at the top of the payload.
        if isinstance(matched, int) and matched > len(features):
            warnings["truncated"] = True
            warnings["note"] = (
                f"Showing {len(features)} of {matched} matching features "
                f"(limit={limit}, offset={offset}). This is a page, not the "
                f"whole result — do not describe it as the complete set. "
                f"Raise limit, page with offset, or narrow with bbox/filter."
            )
        else:
            warnings["truncated"] = False
        return {**warnings, **out}

    @mcp.tool()
    async def get_vector_feature(
        collection_id: str,
        feature_id: str,
    ) -> dict:
        """Get a single vector feature by ogc_fid from a PostGIS collection.

        Returns a GeoJSON Feature with all properties and full geometry.
        feature_id is the integer primary key (ogc_fid); it must be an integer.
        The tipg /items/{id} endpoint is not usable against PostGIS tables (the
        tile-layer id is a WKB hex string that does not round-trip), so this
        tool queries via an ogc_fid CQL2 filter.
        """
        # feature_id is interpolated into a CQL2 expression, so it must be
        # proven to be an integer first. Without this, "1 OR 1=1" matches every
        # row in the table and the tool returns whichever one sorts first — a
        # different feature than the caller asked for, reported as a success.
        try:
            ogc_fid = int(str(feature_id).strip())
        except (TypeError, ValueError):
            raise ToolError(
                f"eo-mcp: feature_id must be an integer ogc_fid, got "
                f"{feature_id!r}. Read ogc_fid from a feature's properties in "
                f"get_vector_features."
            ) from None

        collection_id = normalize_collection_id(collection_id)
        path = f"/collections/{collection_id}/items"
        params = [
            ("f", "geojson"),
            ("filter", f"ogc_fid={ogc_fid}"),
            ("filter-lang", "cql2-text"),
            ("limit", 1),
        ]
        url = vector.url_for(path, params)
        result = await vector.request("GET", path, params=params)
        features = require(result, "features", source="tipg", url=url)
        if not features:
            raise ToolError(
                f"eo-mcp: no feature with ogc_fid={ogc_fid} exists in "
                f"{collection_id}. The query succeeded and matched nothing."
            )
        return slim_feature(features[0])

    @mcp.tool()
    async def get_vector_tile_url(
        collection_id: str,
    ) -> dict:
        """Generate a vector tile URL template for rendering a PostGIS collection on a map.

        Returns a TileJSON document with tile URL template, bounds, min/max zoom,
        and a default MapLibre style. The tiles use MVT (Mapbox Vector Tiles) format
        with source layer named 'default'. The style is best-effort: if tipg cannot
        produce one, `style` is null and the tile URLs are still returned.
        """
        collection_id = normalize_collection_id(collection_id)
        tilejson_path = f"/collections/{collection_id}/tiles/WebMercatorQuad/tilejson.json"
        style_path = f"/collections/{collection_id}/tiles/WebMercatorQuad/style.json"

        tilejson_result, style_result = await asyncio.gather(
            vector.request("GET", tilejson_path),
            vector.request("GET", style_path),
            return_exceptions=True,
        )

        if isinstance(tilejson_result, BaseException):
            raise tilejson_result
        # A missing or broken style is not a reason to withhold the tile URLs;
        # anything that is not a backend error is a real bug and still escapes.
        if isinstance(style_result, ToolError):
            style = None
        elif isinstance(style_result, BaseException):
            raise style_result
        else:
            style = style_result

        url = vector.url_for(tilejson_path)
        tiles = require(tilejson_result, "tiles", source="tipg tilejson", url=url)
        if not isinstance(tiles, list) or not tiles:
            raise ToolError(
                f"eo-mcp: tipg tilejson response from {url} has an empty "
                f"'tiles' list, so there is no tile URL to return."
            )

        return {
            "tilejson": tilejson_result,
            "style": style,
            "tile_url_template": tiles[0],
            "bounds": require(tilejson_result, "bounds", source="tipg tilejson", url=url),
            "minzoom": require(tilejson_result, "minzoom", source="tipg tilejson", url=url),
            "maxzoom": require(tilejson_result, "maxzoom", source="tipg tilejson", url=url),
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
        collection_id = normalize_collection_id(collection_id)
        path = f"/collections/{collection_id}/queryables"
        # tipg only emits the JSON-schema form under ?f=schemajson; the default
        # negotiation returns HTML.
        return await vector.request("GET", path, params={"f": "schemajson"})
