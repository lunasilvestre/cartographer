"""Workflow tools: composites that chain the raster and vector proxies.

These three aggregate over many backend calls, which makes their error contract
different from the other eight tools. A failure affecting *one* item is the
tool's product, not a tool failure: it is reported in band, next to the items
that succeeded, and counted in a top-level ``partial_failures`` field so that a
reader cannot mistake a partial answer for a complete one. A failure that leaves
nothing useful to return — the driving query failed, or every single item failed
— raises ``ToolError`` like every other tool.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp.exceptions import ToolError

from ..http import Backends, list_all_collections
from . import raster as raster_tools
from .vector import is_internal, normalize_collection_id


def statistics_body_for(geometry: Any) -> dict:
    """Shape a feature's geometry into a TiTiler statistics body."""
    geometry_type = geometry.get("type") if isinstance(geometry, dict) else None
    if geometry_type in ("Polygon", "MultiPolygon"):
        return {"type": "Feature", "geometry": geometry, "properties": {}}
    if geometry_type == "Feature":
        return geometry
    raise ValueError(f"unsupported geometry type: {geometry_type}")


def register(mcp, backends: Backends) -> None:
    @mcp.tool()
    async def flood_rainfall_analysis(
        flood_collection_id: str = "public.flood_extent_emsr864",
        precipitation_collection_id: str = "precipitation-daily",
        precipitation_item_id: str = "precipitation-storm-total",
        event_date: str | None = None,
        limit: int = 5,
    ) -> dict:
        """Analyze rainfall over flood-affected areas.

        Fetches flood extent polygons from the vector collection, then computes
        precipitation statistics for each polygon using the raster statistics tool.
        Returns flood features enriched with rainfall stats under
        `properties.rainfall_stats` — mean, min, max, median, std, percentiles,
        and a histogram, all in the raster's own units (mm for precipitation).

        There is no catchment total, and `sum` is not one: it is the arithmetic
        sum of pixel values, so `sum / count == mean`. Reporting it as "total
        rainfall" produces a number with no physical meaning — depths in mm added
        together across pixels are not a depth or a volume. Use `mean` for
        "how much rain fell over this area"; a volume needs pixel area, which
        this server does not compute.

        If event_date is provided, filters flood polygons to that date.
        This is the core disaster-response workflow: "how much did it rain
        where the flooding happened?"

        Partial results are possible and are reported explicitly: a feature
        whose statistics could not be computed carries `properties.rainfall_error`
        instead of `properties.rainfall_stats`, and the response carries
        `partial_failures` (a count) and `failed` (the feature ids). The tool
        raises only if the flood query itself fails or if every feature failed.
        """
        if limit < 1:
            raise ToolError(f"eo-mcp: limit must be at least 1, got {limit}.")

        datetime_filter = None
        if event_date is not None:
            datetime_filter = f"{event_date}T00:00:00Z/{event_date}T23:59:59Z"

        normalized_id = normalize_collection_id(flood_collection_id)
        params: list[tuple[str, str | int]] = [("f", "geojson"), ("limit", limit)]
        if datetime_filter is not None:
            params.append(("datetime", datetime_filter))

        features_result = await backends.vector.request(
            "GET", f"/collections/{normalized_id}/items", params=params
        )
        features = (
            features_result.get("features") or []
            if isinstance(features_result, dict)
            else []
        )

        matched = (
            features_result.get("numberMatched") if isinstance(features_result, dict) else None
        )
        truncated_note = None
        if isinstance(matched, int) and matched > len(features):
            truncated_note = (
                f"Analysed {len(features)} of {matched} flood polygons matching "
                f"this query (limit={limit}). This is a sample, not the whole "
                f"flood extent — totals computed here cover only these polygons."
            )

        base = {
            "type": "FeatureCollection",
            "flood_collection": flood_collection_id,
            "precipitation_item": f"{precipitation_collection_id}/{precipitation_item_id}",
        }

        if not features:
            return {
                **base,
                "features": [],
                "partial_failures": 0,
                "failed": [],
                "note": (
                    f"{normalized_id} returned no flood polygons"
                    + (f" for {event_date}" if event_date else "")
                    + "; there was nothing to compute rainfall over."
                ),
            }

        async def _stats_for_feature(feature: dict) -> dict:
            properties = feature.setdefault("properties", {})
            try:
                body = statistics_body_for(feature.get("geometry"))
            except ValueError as exc:
                properties["rainfall_error"] = str(exc)
                return feature

            try:
                properties["rainfall_stats"] = await raster_tools.statistics(
                    backends,
                    collection_id=precipitation_collection_id,
                    item_id=precipitation_item_id,
                    body=body,
                )
            except ToolError as exc:
                properties["rainfall_error"] = str(exc)
            return feature

        # Concurrency is bounded inside the raster client, so the fan-out here
        # cannot outrun the backend even when every request is being retried.
        enriched = list(
            await asyncio.gather(*(_stats_for_feature(f) for f in features))
        )

        failed = [
            f.get("id", f"features[{i}]")
            for i, f in enumerate(enriched)
            if "rainfall_error" in f.get("properties", {})
        ]
        if len(failed) == len(enriched):
            first = enriched[0]["properties"]["rainfall_error"]
            raise ToolError(
                f"eo-mcp: rainfall statistics failed for all {len(enriched)} "
                f"flood polygons, so there is no analysis to report. First "
                f"failure: {first}"
            )

        # The failure summary goes first, before the features, and says in
        # words what the count means. A model reading 38 successful features
        # and 2 failures will otherwise summarise "mean rainfall over the
        # flooded area was X" — true of 38 polygons, presented as true of 40.
        # The three counts must tell the same story as the prose beside them.
        # `features_analysed` and a bare `features_requested` both equalling 2
        # read as "2 of 2, complete" when 5,496 polygons matched — putting the
        # real total back in the integers is what stops a reader who skims the
        # note from getting the pre-fix answer.
        header: dict[str, Any] = {
            "partial_failures": len(failed),
            "features_analysed": len(enriched) - len(failed),
            "features_attempted": len(enriched),
        }
        if isinstance(matched, int):
            header["features_matched"] = matched
        if failed:
            header["failed"] = failed
            header["note"] = (
                f"INCOMPLETE: rainfall was computed for "
                f"{len(enriched) - len(failed)} of {len(enriched)} flood "
                f"polygons. The {len(failed)} listed in 'failed' have "
                f"properties.rainfall_error instead of properties.rainfall_stats "
                f"and are absent from any total or average you compute over "
                f"this response. Any summary must say so."
            )
        else:
            header["failed"] = []

        if truncated_note:
            header["truncated"] = True
            header["truncation_note"] = truncated_note

        return {**header, **base, "features": enriched}

    @mcp.tool()
    async def multi_layer_summary(
        geojson: dict,
        raster_layers: list[dict],
    ) -> dict:
        """Compute raster statistics from multiple layers over the same area of interest.

        Provide a GeoJSON Polygon and a list of raster layer specs, each containing:
        - collection_id (str): STAC collection
        - item_id (str): STAC item
        - asset (str, optional): defaults to 'data'
        - label (str, optional): human-readable name for the result

        Returns a summary dict with one entry per layer. Each entry carries
        `ok: true` and the zonal statistics, or `ok: false` and an `error`
        explaining why that layer failed; `partial_failures` counts the latter.
        The tool raises only if every layer failed.

        Use this to build multi-variable profiles of an area (e.g., rainfall +
        soil moisture + wind speed over a flood zone).
        """
        if not raster_layers:
            raise ToolError(
                "eo-mcp: raster_layers is empty, so there is nothing to summarise."
            )
        body = raster_tools.statistics_body(geojson)

        labels: list[str] = []
        for index, layer in enumerate(raster_layers):
            missing = [k for k in ("collection_id", "item_id") if not layer.get(k)]
            if missing:
                raise ToolError(
                    f"eo-mcp: raster_layers[{index}] is missing required "
                    f"{' and '.join(missing)}."
                )
            labels.append(
                layer.get("label") or f"{layer['collection_id']}/{layer['item_id']}"
            )

        # Results are keyed by label, so two layers sharing one would overwrite
        # each other and the response would simply contain fewer layers than
        # were asked for, with partial_failures at 0 because nothing failed.
        # The realistic case needs no user error at all: the default label is
        # collection_id/item_id, so requesting two assets of the same item
        # collides. Reject it before any request, the same way a non-integer
        # feature_id is rejected.
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        if duplicates:
            raise ToolError(
                f"eo-mcp: raster_layers has more than one layer labelled "
                f"{', '.join(repr(d) for d in duplicates)}. Results are keyed by "
                f"label, so duplicates would silently replace each other and you "
                f"would receive fewer layers than you asked for. Give each layer "
                f"a distinct 'label' — the default is 'collection_id/item_id', so "
                f"two assets of the same item need explicit labels."
            )

        async def _stats_for_layer(layer: dict) -> tuple[str, dict]:
            collection_id = layer["collection_id"]
            item_id = layer["item_id"]
            asset = layer.get("asset", "data")
            label = layer.get("label") or f"{collection_id}/{item_id}"
            try:
                stats = await raster_tools.statistics(
                    backends,
                    collection_id=collection_id,
                    item_id=item_id,
                    body=body,
                    params=[("assets", asset)],
                )
            except ToolError as exc:
                return label, {"ok": False, "error": str(exc)}
            return label, {"ok": True, "statistics": stats}

        results = dict(
            await asyncio.gather(*(_stats_for_layer(layer) for layer in raster_layers))
        )
        failures = [label for label, entry in results.items() if not entry["ok"]]
        if len(failures) == len(results):
            raise ToolError(
                f"eo-mcp: statistics failed for all {len(results)} layer(s), so "
                f"there is no summary to report. First failure: "
                f"{results[failures[0]]['error']}"
            )

        header: dict[str, Any] = {"partial_failures": len(failures)}
        if failures:
            header["failed"] = failures
            header["note"] = (
                f"INCOMPLETE: {len(failures)} of {len(results)} layers failed "
                f"({', '.join(failures)}) and carry ok=false with an error "
                f"instead of statistics. A multi-variable profile described "
                f"from this response is missing those layers."
            )
        else:
            header["failed"] = []

        return {**header, "aoi": geojson, "layers": results}

    @mcp.tool()
    async def collection_health_check(
        collection_ids: list[str] | None = None,
    ) -> dict:
        """Check the health and availability of raster and vector collections.

        Tests connectivity to TiTiler (raster) and tipg (vector) backends.
        For each collection, reports: reachable (bool), type (raster/vector),
        item/feature count (if available), temporal extent, spatial extent.

        If collection_ids is None, checks every collection both backends
        advertise, following pagination so the listing is complete. Note that
        both backends paginate /collections at ten by default, so a naive
        listing under-reports.

        A collection being unreachable is the answer this tool exists to give,
        so it never raises for that. It raises only when neither backend can be
        enumerated at all.
        """
        raster_ids: list[str] = []
        vector_ids: list[str] = []
        enumeration_errors: dict[str, str] = {}

        if collection_ids is None:
            vector_page, stac_page = await asyncio.gather(
                list_all_collections(backends.vector, {"f": "json"}),
                list_all_collections(backends.stac, {}),
                return_exceptions=True,
            )

            if isinstance(vector_page, BaseException):
                enumeration_errors["vector"] = str(vector_page)
            else:
                vector_ids = [
                    c["id"]
                    for c in vector_page
                    if c.get("id") and not is_internal(c["id"])
                ]

            if isinstance(stac_page, BaseException):
                enumeration_errors["stac"] = str(stac_page)
            else:
                raster_ids = [c["id"] for c in stac_page if c.get("id")]

            if len(enumeration_errors) == 2:
                raise ToolError(
                    "eo-mcp: neither backend could be enumerated, so no health "
                    "report is possible. vector: {vector} | STAC: {stac}".format(
                        vector=enumeration_errors["vector"],
                        stac=enumeration_errors["stac"],
                    )
                )
        else:
            # A dotted id is a PostGIS schema-qualified table (tipg); anything
            # else is a STAC collection.
            for cid in collection_ids:
                (vector_ids if "." in cid else raster_ids).append(cid)

        # Probe each collection once even if a backend listed it twice.
        vector_ids = list(dict.fromkeys(vector_ids))
        raster_ids = list(dict.fromkeys(raster_ids))

        async def _probe_vector(col_id: str) -> tuple[str, dict]:
            try:
                result = await backends.vector.request(
                    "GET", f"/collections/{col_id}", params={"f": "json"}
                )
            except ToolError as exc:
                return col_id, {"reachable": False, "type": "vector", "error": str(exc)}
            extent = result.get("extent", {}) if isinstance(result, dict) else {}
            return col_id, {
                "reachable": True,
                "type": "vector",
                "title": result.get("title"),
                "spatial_extent": extent.get("spatial", {}).get("bbox"),
                "temporal_extent": extent.get("temporal", {}).get("interval"),
            }

        async def _probe_raster(col_id: str) -> tuple[str, dict]:
            try:
                data = await backends.stac.request(
                    "GET", f"/collections/{col_id}/items", params={"limit": 1}
                )
            except ToolError as exc:
                return col_id, {"reachable": False, "type": "raster", "error": str(exc)}
            features = data.get("features", []) if isinstance(data, dict) else []
            # numberMatched is the only real total. This STAC deployment returns
            # null for it on /items, and the old code fell back to
            # numberReturned — which is always 1, because the probe asks for
            # limit=1. That produced `item_count: 1` for every collection: a
            # clean integer, in a plausibly-named field, inside a
            # `reachable: true` payload. A null is honest; a 1 is a lie.
            matched = data.get("numberMatched") if isinstance(data, dict) else None
            return col_id, {
                "reachable": True,
                "type": "raster",
                "item_count": matched if isinstance(matched, int) else None,
                "has_items": bool(features),
                "sample_item": features[0].get("id") if features else None,
            }

        vector_results, raster_results = await asyncio.gather(
            asyncio.gather(*(_probe_vector(cid) for cid in vector_ids)),
            asyncio.gather(*(_probe_raster(cid) for cid in raster_ids)),
        )

        sections = {
            "raster": dict(raster_results),
            "vector": dict(vector_results),
        }
        unreachable = [
            cid
            for section in sections.values()
            for cid, entry in section.items()
            if not entry["reachable"]
        ]

        # Failure summary first: a field at the end of a long payload is a field
        # that gets skimmed past.
        header: dict[str, Any] = {"partial_failures": len(unreachable)}
        if unreachable:
            header["unreachable"] = unreachable
            header["note"] = (
                f"{len(unreachable)} of "
                f"{len(sections['raster']) + len(sections['vector'])} collections "
                f"could not be reached: {', '.join(unreachable)}. Any statement "
                f"about the catalogue's health must name these."
            )
        else:
            header["unreachable"] = []
        if enumeration_errors:
            header["enumeration_errors"] = enumeration_errors
        return {**header, **sections}
