---
title: Flood Extent Mapping
domain: disaster-response
servers_required: [eo-mcp]
servers_optional: [stac-mcp]
complexity: basic
tool_calls: 3
demo_endpoint: api.cheias.pt/raster
---

# Flood Extent Mapping

## When to use

Use this workflow when you have an AOI and an event date range and want to characterise a flood event using Copernicus EMS flood-extent vectors and the precipitation record that drove it.

## Inputs needed

- **AOI polygon** — GeoJSON Polygon or MultiPolygon covering the area of interest.
- **Event date range** — start and end dates bracketing the flood event (ISO 8601).

## Steps

1. **Discover flood geometry** — Call `stac-mcp.search_items` on collection `flood-extent-emsr864`, filtered by the AOI bbox and event date range. Select the best-matching item. This collection is vector-only; it provides the flood footprint polygon but has no raster assets.

2. **Characterise driving rainfall** — Call `eo-mcp.get_zonal_statistics` with `collection_id=precipitation-daily`, `item_id=precipitation-storm-total`, asset key `data`, and the flood footprint geometry from step 1 as the `geojson` input (pass a GeoJSON Feature wrapper). This returns mean and max accumulated precipitation over the flood footprint.

3. **Interpret results** — Report mean and max precipitation (mm) over the flood footprint, the flood item ID used as the AOI source, and the precipitation item ID. Flag any nodata fraction above 20 % as low-confidence.

## Output format

Report the following:

- Mean and max precipitation (mm) over the flood footprint
- Flood geometry source: `item_id` and `collection_id` from `flood-extent-emsr864`
- Precipitation source: `item_id` (`precipitation-storm-total`) and `collection_id` (`precipitation-daily`)
- Confidence qualifier (high / medium / low) based on nodata fraction and temporal offset

## Caveats

- **Vector-only flood collections**: `flood-extent-emsr861` and `flood-extent-emsr864` carry no raster assets. Do not call `get_raster_info` or `get_tile_url` on them.
- **Asset key**: All raster collections use asset key `data`. There is no `cog_default` asset.
- **GeoJSON wrapper**: `get_zonal_statistics` expects a GeoJSON Feature (not a bare Geometry) in the `geojson` field.
- **AOI clipping**: TiTiler clips statistics to the provided polygon. Ensure the polygon is in WGS84 (EPSG:4326).
- **Temporal gaps**: EMS observed-event products may lag the event peak by 12–72 hours. The selected item date may not capture maximum inundation.
