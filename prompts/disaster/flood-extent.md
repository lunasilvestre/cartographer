---
title: Flood Extent Mapping
domain: disaster-response
servers_required: [eo-mcp]
servers_optional: [stac-mcp]
complexity: basic
tool_calls: 2
demo_endpoint: api.cheias.pt
---

# Flood Extent Mapping

Characterise a flood event using Copernicus EMS flood-extent polygons and the
precipitation record that drove it.

A fully worked run of this recipe, with real output, is in
[`docs/WORKED-EXAMPLE.md`](../../docs/WORKED-EXAMPLE.md).

## When to use

You have an area of interest and an event date range, and you want to know how
much rain fell over the land that actually flooded.

## Inputs needed

- **AOI bounding box** — `[min_lon, min_lat, max_lon, max_lat]` in WGS84.
- **Event date range** — ISO 8601, if you need to disambiguate between events.

---

## Read this before step 1

**The flood geometry comes from tipg, never from STAC.**

There are STAC collections named `flood-extent-emsr861` and
`flood-extent-emsr864`, and each has an item that looks like the flood footprint.
It is not. Their items carry `assets: {}`, a single `datetime` property, and a
geometry that is a placeholder rectangle covering all of mainland Portugal:

```
[[-9.5,37],[-6,37],[-6,42.2],[-9.5,42.2],[-9.5,37]]
```

Using that as an AOI computes over roughly 171,700 km² instead of the 892 km²
that actually flooded — an area error of about 193x. It returns HTTP 200 and a
precise number (279.47 mm, which is simply the national mean), and nothing warns
you. The flood polygons never enter the computation.

The real delineations are in tipg, as thousands of individual polygons.

---

## Steps

### 1. Fetch the flood polygons

```
get_vector_features(
    collection_id = "public.flood_extent_emsr864",
    bbox          = [min_lon, min_lat, max_lon, max_lat],
    sortby        = "-area_ha",
    simplify      = 0.0001,
    limit         = 10,
)
```

- Use `public.flood_extent_emsr864` (storm Leonardo/Marta, 2026-02-08, 5,496
  polygons) or `public.flood_extent_emsr861` (storm Kristin, 2026-01-30, 506
  polygons). The `public.` prefix is required; the tool normalises a bare name.
- `sortby="-area_ha"` puts the largest polygons first, which is usually what you
  want to report.
- **`simplify` is not optional at this scale** — see the caveat below.
- To filter by date, use `datetime="2026-02-08T00:00:00Z/2026-02-08T23:59:59Z"`.
  Both collections are single-date, so this is only needed across collections.

Check `numberMatched` against `numberReturned`: the former is the true count, the
latter is bounded by `limit`.

### 2. Compute precipitation over each polygon

```
get_zonal_statistics(
    collection_id = "precipitation-daily",
    item_id       = "precipitation-storm-total",
    geojson       = <a Feature from step 1>,
)
```

- Pass a Feature straight through from step 1. A bare Polygon or MultiPolygon is
  wrapped for you; a FeatureCollection returns per-feature statistics.
- `precipitation-storm-total` is total accumulated precipitation in millimetres
  for the whole storm period. For a single day use
  `precipitation-daily-YYYY-MM-DD`.
- The asset key is `data` on every raster collection, which is the tool's
  default. There is no `cog_default` asset.

`flood_rainfall_analysis` performs both steps in one call when you want the first
N polygons of a collection without a bounding box.

### 3. Interpret

Report per polygon: `ogc_fid`, `area_ha`, and mean / min / max precipitation.

Compare against the item-wide mean (279.47 mm for `precipitation-storm-total`) so
the number has a reference point — "340 mm" means little on its own.

Qualify confidence using `valid_percent` and `valid_pixels` from the statistics
response. Treat anything under 20 % valid, or fewer than about 5 valid pixels, as
low confidence.

---

## Output format

- Mean, min and max precipitation (mm) per flood polygon, with `ogc_fid` and `area_ha`
- Total flooded **area** across the polygons considered (areas are additive; rainfall is not)
- Source identifiers: the tipg collection, and the precipitation collection and item
- The item-wide mean as a baseline
- A confidence qualifier derived from `valid_percent` and `valid_pixels`

**Never report a rainfall total.** The statistics block has no `total` key, and
`sum` is not one — it is pixel values added together, so `sum / count` is just
`mean` again. "Total rainfall was 8,152 mm" is confident nonsense. A real
catchment total needs pixel area × depth, which this server does not compute.
Report the mean with the polygon area beside it.

---

## Caveats

**Never use the STAC flood-extent item as an AOI.** See above. This is the single
most consequential mistake available in this workflow.

**Requests above roughly 1 MB are rejected by the edge** — 413 or 403 depending
on the network path, HTML rather than JSON either way. `get_vector_features`
strips tipg's WKB-hex `id` and `links` (~95% of a raw Feature) for you, so what
remains is geometry size, which is what `simplify` is for. A raw EMS polygon can
be 31,437 vertices, and the largest in emsr864 (`ogc_fid=111`, 10,437 ha) is
still 1.8 MB after stripping.

**Use `simplify=0.0001`, and do not round it up.** Past about `0.0005` tipg stops
coarsening geometries and returns them as `null` — HTTP 200, properties intact,
shape gone. Measured over 500 features of this collection: `0.0005` nulls 29%,
**`0.001` nulls 61%**, `0.01` nulls 96%. `0.0001` loses none.

`get_vector_features` reports `null_geometry_count` and `null_geometry_note` when
this happens. **Check it whenever you pass `simplify`.**

The precision cost of `0.0001` is negligible — about 11 m against a 2.2 km raster
cell, one two-hundredth of a pixel — so there is no reason to reach for a coarser
value. For reference, `ogc_fid=3579` gives 339.67 mm at `0.0001` versus 338.26 mm
at `0.001`, about 0.4 %; but that comparison only exists because that polygon is
large enough to survive `0.001`, and most are not.

**`area_ha` is a stored column**, not computed from the returned geometry. It
survives even a null geometry, so an area total looks correct while the shapes
are gone. It cannot be used to measure simplification error or to detect loss.

**The raster is coarse.** `precipitation-storm-total` is 175 × 265 pixels over
`[-9.6, 36.9, -6.1, 42.2]`, roughly 2 km cells. A 3,918 ha polygon contains only
24 valid pixels. Small polygons may contain fewer than five — report those with
explicit caution or not at all.

**Vector-only flood collections have no raster assets.** Do not call
`get_raster_info` or `get_tile_url` on `flood-extent-emsr861` or
`flood-extent-emsr864`.

**Geometry must be WGS84 (EPSG:4326).** Both backends serve CRS84 and TiTiler
clips statistics to the polygon you provide.

**EMS products lag the peak.** Observed-event delineations may trail maximum
inundation by 12–72 hours, so the mapped extent can understate the flood.

---

## Reference values

Verified against the live backend on 2026-07-31 — useful for checking that a run
is behaving.

| Fact | Value |
|---|---|
| `public.flood_extent_emsr864` | 5,496 polygons, 2026-02-08, storm Leonardo/Marta |
| `public.flood_extent_emsr861` | 506 polygons, 2026-01-30, storm Kristin |
| Total flooded area, emsr864 | 89,199.48 ha |
| Flood polygons in a Coimbra bbox `[-8.55, 40.10, -8.30, 40.30]` | 87, totalling 5,062.12 ha |
| Largest of those | `ogc_fid=3579`, 3,918.25 ha, mean 339.67 mm |
| `precipitation-storm-total` item-wide | min 100.31, mean 279.47, max 581.68 mm |
| Flood-extent columns | `ogc_fid`, `datetime`, `activation`, `aoi`, `area_ha`, `event_type`, `storm` |
