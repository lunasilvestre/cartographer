from __future__ import annotations

import asyncio

import httpx

from . import raster, vector


def register(mcp, settings) -> None:
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
        Returns flood features enriched with rainfall stats (mean, max, min, total).

        If event_date is provided, filters flood polygons to that date.
        This is the core disaster-response workflow: "how much did it rain
        where the flooding happened?"
        """
        # Build datetime range if event_date provided
        datetime_filter = None
        if event_date is not None:
            datetime_filter = f"{event_date}T00:00:00Z/{event_date}T23:59:59Z"

        # Fetch flood features from vector backend
        vclient = await vector._get_client(settings)
        normalized_id = vector._normalize_collection_id(flood_collection_id)
        params: list[tuple[str, str | int]] = [
            ("f", "geojson"),
            ("limit", limit),
        ]
        if datetime_filter is not None:
            params.append(("datetime", datetime_filter))

        features_result = await vector._request(
            vclient, "GET", f"/collections/{normalized_id}/items", params=params
        )

        if "error" in features_result:
            return features_result

        features = features_result.get("features", [])
        if not features:
            return {
                "type": "FeatureCollection",
                "features": [],
                "flood_collection": flood_collection_id,
                "precipitation_item": f"{precipitation_collection_id}/{precipitation_item_id}",
            }

        # Compute zonal stats for each feature in parallel with a semaphore
        rclient = await raster._get_client(settings)
        semaphore = asyncio.Semaphore(5)

        async def _stats_for_feature(feature: dict) -> dict:
            geometry = feature.get("geometry")
            if geometry is None:
                feature.setdefault("properties", {})["rainfall_error"] = "no geometry"
                return feature

            body: dict
            geom_type = geometry.get("type")
            if geom_type in ("Polygon", "MultiPolygon"):
                body = {"type": "Feature", "geometry": geometry, "properties": {}}
            elif geom_type == "Feature":
                body = geometry
            else:
                feature.setdefault("properties", {})["rainfall_error"] = (
                    f"unsupported geometry type: {geom_type}"
                )
                return feature

            async with semaphore:
                stats = await raster._request(
                    rclient,
                    "POST",
                    f"/collections/{precipitation_collection_id}/items/{precipitation_item_id}/statistics",
                    params=[("assets", "data")],
                    json=body,
                )

            props = feature.setdefault("properties", {})
            if "error" in stats:
                props["rainfall_error"] = stats["error"]
            else:
                props["rainfall_stats"] = stats

            return feature

        enriched = await asyncio.gather(*(_stats_for_feature(f) for f in features))

        return {
            "type": "FeatureCollection",
            "features": list(enriched),
            "flood_collection": flood_collection_id,
            "precipitation_item": f"{precipitation_collection_id}/{precipitation_item_id}",
        }

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

        Returns a summary dict with one entry per layer containing the zonal statistics.
        Use this to build multi-variable profiles of an area (e.g., rainfall + soil moisture
        + wind speed over a flood zone).
        """
        # Validate input geometry
        geojson_type = geojson.get("type")
        if geojson_type in ("Polygon", "MultiPolygon"):
            body = {"type": "Feature", "geometry": geojson, "properties": {}}
        elif geojson_type == "Feature":
            inner_type = (geojson.get("geometry") or {}).get("type")
            if inner_type not in ("Polygon", "MultiPolygon"):
                return {
                    "error": "Feature geometry must be Polygon or MultiPolygon",
                    "status": 400,
                }
            body = geojson
        else:
            return {
                "error": "geojson must be a Polygon, MultiPolygon, or Feature containing one",
                "status": 400,
            }

        rclient = await raster._get_client(settings)

        async def _stats_for_layer(layer: dict) -> tuple[str, dict]:
            collection_id = layer["collection_id"]
            item_id = layer["item_id"]
            asset = layer.get("asset", "data")
            label = layer.get("label") or f"{collection_id}/{item_id}"

            stats = await raster._request(
                rclient,
                "POST",
                f"/collections/{collection_id}/items/{item_id}/statistics",
                params=[("assets", asset)],
                json=body,
            )
            return label, stats

        results = await asyncio.gather(*(_stats_for_layer(layer) for layer in raster_layers))

        return {
            "aoi": geojson,
            "layers": dict(results),
        }

    @mcp.tool()
    async def collection_health_check(
        collection_ids: list[str] | None = None,
    ) -> dict:
        """Check the health and availability of raster and vector collections.

        Tests connectivity to TiTiler (raster) and tipg (vector) backends.
        For each collection, reports: reachable (bool), type (raster/vector),
        item/feature count (if available), temporal extent, spatial extent.

        If collection_ids is None, checks all known collections from both backends.
        Use this to verify the backend is operational before running analysis.
        """
        vclient = await vector._get_client(settings)

        raster_ids: list[str] = []
        vector_ids: list[str] = []

        if collection_ids is None:
            # Fetch vector collections from tipg
            vec_list = await vector._request(
                vclient, "GET", "/collections", params={"f": "json"}
            )
            if "error" not in vec_list:
                for col in vec_list.get("collections", []):
                    col_id = col.get("id", "")
                    if col_id:
                        vector_ids.append(col_id)

            # Fetch raster collections from STAC
            try:
                async with httpx.AsyncClient(timeout=settings.request_timeout) as stac_client:
                    stac_headers = {}
                    if settings.auth_token:
                        stac_headers["Authorization"] = f"Bearer {settings.auth_token}"
                    stac_resp = await stac_client.get(
                        f"{settings.stac_url}/collections",
                        headers=stac_headers,
                    )
                    if stac_resp.status_code < 400:
                        stac_data = stac_resp.json()
                        for col in stac_data.get("collections", []):
                            col_id = col.get("id", "")
                            if col_id:
                                raster_ids.append(col_id)
            except Exception as exc:  # noqa: BLE001
                raster_ids = [f"(stac-error: {exc})"]
        else:
            # Split provided IDs into vector (contains ".") vs raster (assume STAC)
            for cid in collection_ids:
                if "." in cid:
                    vector_ids.append(cid)
                else:
                    raster_ids.append(cid)

        async def _probe_vector(col_id: str) -> tuple[str, dict]:
            result = await vector._request(
                vclient, "GET", f"/collections/{col_id}", params={"f": "json"}
            )
            if "error" in result:
                return col_id, {"reachable": False, "type": "vector", "error": result["error"]}
            extent = result.get("extent", {})
            return col_id, {
                "reachable": True,
                "type": "vector",
                "title": result.get("title"),
                "spatial_extent": extent.get("spatial", {}).get("bbox"),
                "temporal_extent": extent.get("temporal", {}).get("interval"),
            }

        async def _probe_raster(col_id: str) -> tuple[str, dict]:
            # Lightweight probe: list items (limit=1) from STAC directly
            try:
                async with httpx.AsyncClient(timeout=settings.request_timeout) as stac_client:
                    stac_headers = {}
                    if settings.auth_token:
                        stac_headers["Authorization"] = f"Bearer {settings.auth_token}"
                    resp = await stac_client.get(
                        f"{settings.stac_url}/collections/{col_id}/items",
                        params={"limit": 1},
                        headers=stac_headers,
                    )
                if resp.status_code >= 400:
                    return col_id, {
                        "reachable": False,
                        "type": "raster",
                        "error": resp.text,
                        "status": resp.status_code,
                    }
                data = resp.json()
                features = data.get("features", [])
                return col_id, {
                    "reachable": True,
                    "type": "raster",
                    "item_count": data.get("numberMatched") or data.get("numberReturned"),
                    "sample_item": features[0].get("id") if features else None,
                }
            except Exception as exc:  # noqa: BLE001
                return col_id, {"reachable": False, "type": "raster", "error": str(exc)}

        vector_results, raster_results = await asyncio.gather(
            asyncio.gather(*(_probe_vector(cid) for cid in vector_ids)),
            asyncio.gather(*(_probe_raster(cid) for cid in raster_ids)),
        )

        return {
            "raster": dict(raster_results),
            "vector": dict(vector_results),
        }
