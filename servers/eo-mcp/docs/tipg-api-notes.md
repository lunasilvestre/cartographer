# TiPg (tipg) API Discovery Notes

Scouted: 2026-04-14  
Backend: tipg OGC Features API  
Base URL: `https://api.cheias.pt/vector`  
Version: tipg (OGC Features API compliant)

---

## Critical Deltas vs. Build Spec

| Spec assumption | Reality |
|---|---|
| Column named `event_date` | **WRONG** — column is `datetime` (timestamptz). No `event_date` anywhere. |
| `public.` prefix optional | **WRONG** — `public.` prefix is **required**. Collection id is `public.flood_extent_emsr864`, not `flood_extent_emsr864`. |
| `?f=json` for queryables | **WRONG** — queryables requires `?f=schemajson`. `?f=json` returns a 422 validation error. |
| style.json returns 404 | **WRONG** — style.json returns HTTP 200 with a valid Mapbox GL style. |
| CQL2 `datetime='2026-02-08'` | **FAILS** — tipg rejects bare date strings with HTTP 500. Use `TIMESTAMP('2026-02-08T00:00:00Z')` instead. |
| Single item fetch via `items/{id}` | **BROKEN** — tipg stores the geom WKB as the GeoJSON feature `id`. `items/{wkb_id}` returns 500 (parse error). Use CQL2 `filter=ogc_fid=N` instead. |

---

## 1. All Collections

Base URL: `https://api.cheias.pt/vector/collections`

Total collections: **13** (includes system/utility tables — filter to 7 domain tables).

### Domain Collections

| Collection ID | Spatial Extent (WGS84) | Temporal Extent |
|---|---|---|
| `public.flood_extent_emsr861` | [-9.353, 39.350, -8.453, 40.261] | 2026-01-30 (single date) |
| `public.flood_extent_emsr864` | [-9.158, 37.186, -7.397, 42.082] | 2026-02-08 (single date) |
| `public.frontal_boundaries` | [-50.5, 36.0, 0.0, 58.5] | 2026-01-28 to 2026-02-10 |
| `public.storm_tracks` | [-32.0, 30.0, -6.0, 41.5] | 2026-01-27 to 2026-02-11 |
| `public.discharge_stations` | [-8.99, 37.64, -7.66, 42.03] | 2026-01-27 to 2026-02-12 |
| `public.rivers_portugal` | [-9.308, 37.166, -6.083, 42.283] | No temporal extent |
| `public.consequence_events` | [-9.205, 37.187, -7.142, 41.161] | 2026-01-29 to 2026-02-14 |

### System/Utility Tables (tipg exposes these — avoid in tool calls)

| Collection ID | Note |
|---|---|
| `public.postgis_srs` | PostGIS internal SRS table |
| `public.postgis_srs_all` | PostGIS internal |
| `public.postgis_srs_search` | PostGIS internal |
| `public.st_subdivide` | PostGIS function |
| `public.st_hexagongrid` | PostGIS function |
| `public.st_squaregrid` | PostGIS function |

---

## 2. emsr864 — Full Collection Metadata

```
GET https://api.cheias.pt/vector/collections/public.flood_extent_emsr864?f=json
```

```json
{
    "id": "public.flood_extent_emsr864",
    "extent": {
        "spatial": {
            "bbox": [[-9.158, 37.186, -7.397, 42.082]],
            "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
        },
        "temporal": {
            "interval": [["2026-02-08T00:00:00+00:00", "2026-02-08T00:00:00+00:00"]],
            "trs": "http://www.opengis.net/def/uom/ISO-8601/0/Gregorian"
        }
    },
    "itemType": "feature",
    "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"]
}
```

Alternate formats exposed via `links`:
- `items?f=csv` — CSV
- `items?f=geojsonseq` — GeoJSON sequence (NDJSON)

---

## 3. emsr864 Queryables

**IMPORTANT**: Must use `?f=schemajson` — `?f=json` returns 422.

```
GET https://api.cheias.pt/vector/collections/public.flood_extent_emsr864/queryables?f=schemajson
```

```json
{
    "title": "public.flood_extent_emsr864",
    "type": "object",
    "$schema": "https://json-schema.org/draft/2019-09/schema",
    "properties": {
        "geom":       { "$ref": "https://geojson.org/schema/Geometry.json" },
        "activation": { "name": "activation", "type": "string" },
        "aoi":        { "name": "aoi",        "type": "string" },
        "area_ha":    { "name": "area_ha",    "type": "number" },
        "datetime":   { "name": "datetime",   "type": "string" },
        "event_type": { "name": "event_type", "type": "string" },
        "ogc_fid":    { "name": "ogc_fid",    "type": "number" },
        "storm":      { "name": "storm",      "type": "string" }
    }
}
```

### Column Reference

| Column | Type | Notes |
|---|---|---|
| `geom` | Geometry | PostGIS geometry column; not returned in properties |
| `activation` | string | e.g. `"EMSR864"` |
| `aoi` | string | Area of Interest ID, e.g. `"AOI05"` |
| `area_ha` | number | Feature area in hectares |
| `datetime` | string (timestamptz) | ISO 8601 with timezone, e.g. `"2026-02-08T00:00:00+00:00"` |
| `event_type` | string | e.g. `"5-Flood"` |
| `ogc_fid` | number | Integer primary key; use this for single-feature lookup via CQL2 |
| `storm` | string | Storm name, e.g. `"Leonardo/Marta"` |

**There is NO `event_date` column. The date column is `datetime`.**

---

## 4. Sample Features

```
GET https://api.cheias.pt/vector/collections/public.flood_extent_emsr864/items?f=geojson&limit=2
```

Response envelope:
```json
{
    "type": "FeatureCollection",
    "id": "public.flood_extent_emsr864",
    "title": "public.flood_extent_emsr864",
    "numberMatched": 5496,
    "numberReturned": 2,
    "links": [
        { "rel": "collection", "href": "...collections/public.flood_extent_emsr864" },
        { "rel": "self",       "href": "...items?f=geojson&limit=2" },
        { "rel": "next",       "href": "...items?f=geojson&limit=2&offset=2" }
    ],
    "features": [...]
}
```

Individual feature shape:
```json
{
    "type": "Feature",
    "id": "<WKB hex string of geom — NOT a usable id for items/{id}>",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[-8.773, 40.065], ...]]
    },
    "properties": {
        "activation": "EMSR864",
        "aoi": "AOI05",
        "area_ha": 2.356609850575627,
        "datetime": "2026-02-08T00:00:00+00:00",
        "event_type": "5-Flood",
        "ogc_fid": 3500,
        "storm": "Leonardo/Marta"
    }
}
```

**Feature `id` field**: tipg sets this to the WKB hex of the geometry column. It cannot be used to call `items/{id}` (returns 500). Use `?filter=ogc_fid=N&filter-lang=cql2-text` for single-feature access.

**Geometry type**: `Polygon` (5496 features in emsr864, all single-date 2026-02-08).

**Distinct values in emsr864** (all features share same storm/event):
- `event_type`: `["5-Flood"]`
- `storm`: `["Leonardo/Marta"]`
- `aoi`: `["AOI05"]`

---

## 5. Filter Patterns

### 5a. bbox — CONFIRMED WORKING

```
GET .../items?f=geojson&bbox=-8.5,39.0,-7.5,39.8&limit=2
```

Returns `numberMatched: 244` (out of 5496). bbox filtering is spatially accurate.

### 5b. OGC datetime parameter — CONFIRMED WORKING

```
GET .../items?f=geojson&datetime=2026-02-08T00:00:00Z/2026-02-08T23:59:59Z&limit=2
```

Returns `numberMatched: 5496` (correct — all features are on that date).

```
GET .../items?f=geojson&datetime=2025-01-01T00:00:00Z/2025-01-02T00:00:00Z&limit=2
```

Returns `numberMatched: 0` (correct — no features on that date). **OGC datetime filtering works.**

Format: ISO 8601 interval `start/end` or single timestamp.

### 5c. CQL2 — PARTIALLY WORKING

```
?filter=<expression>&filter-lang=cql2-text
```

| CQL2 expression | Result |
|---|---|
| `datetime='2026-02-08'` | **HTTP 500** — bare date string rejected. "expected datetime.date or datetime.datetime instance, got 'str'" |
| `datetime=TIMESTAMP('2026-02-08T00:00:00Z')` | HTTP 200, returns 5496 (correct) |
| `datetime>=TIMESTAMP('2026-02-08T00:00:00Z') AND datetime<=TIMESTAMP('2026-02-08T23:59:59Z')` | HTTP 200, returns 5496 (correct) |
| `ogc_fid=3500` | HTTP 200, returns `numberMatched: 1` (single feature) |
| `ogc_fid>3000 AND ogc_fid<3003` | HTTP 200, returns `numberMatched: 2` |
| `event_type='5-Flood'` | HTTP 200, returns 5496 (correct — all features match) |
| `event_type='nonexistent'` | HTTP 200, returns `numberMatched: 0` (correct filtering) |
| `S_INTERSECTS(geom,POLYGON((...)))` | HTTP 200, returns spatially filtered results (244 matched) |

**CQL2 Summary**: Works for numeric comparisons, string equality, and spatial predicates. Datetime filtering requires `TIMESTAMP('...')` wrapper — bare date strings fail.

### 5d. Single Feature by ogc_fid — Use CQL2

```
GET .../items?f=geojson&filter=ogc_fid=3500&filter-lang=cql2-text&limit=1
```

Returns `numberMatched: 1`. This is the reliable single-feature access pattern.

Do **not** use `items/{id}` — the WKB hex id causes a 500 parse error.

---

## 6. TileJSON

```
GET https://api.cheias.pt/vector/collections/public.flood_extent_emsr864/tiles/WebMercatorQuad/tilejson.json
```

```json
{
    "tilejson": "3.0.0",
    "name": "public.flood_extent_emsr864",
    "version": "1.0.0",
    "scheme": "xyz",
    "tiles": [
        "https://api.cheias.pt/vector/collections/public.flood_extent_emsr864/tiles/WebMercatorQuad/{z}/{x}/{y}"
    ],
    "vector_layers": [
        {
            "id": "default",
            "fields": {
                "activation": "string",
                "aoi": "string",
                "area_ha": "number",
                "datetime": "string",
                "event_type": "string",
                "ogc_fid": "number",
                "storm": "string"
            },
            "minzoom": 0,
            "maxzoom": 22
        }
    ],
    "minzoom": 0,
    "maxzoom": 22,
    "bounds": [-9.158, 37.186, -7.397, 42.082],
    "center": [-8.278, 39.634, 0]
}
```

Key notes:
- Vector tile layer id is always `"default"` (not the collection id).
- Tile URL has **no** `@1x`/`@2x` suffix (contrast with TiTiler raster tiles).
- `maxzoom: 22` is tipg's configured ceiling.

---

## 7. style.json — CONFIRMED WORKING (not 404)

```
GET https://api.cheias.pt/vector/collections/public.flood_extent_emsr864/tiles/WebMercatorQuad/style.json
```

HTTP 200. Returns a Mapbox GL v8 style with three layers (fill, stroke, point):

```json
{
    "version": 8,
    "name": "TiPg",
    "layers": [
        {
            "id": "public.flood_extent_emsr864_fill",
            "source": "public.flood_extent_emsr864",
            "source-layer": "default",
            "type": "fill",
            "filter": ["==", ["geometry-type"], "Polygon"],
            "paint": {"fill-color": "rgba(200, 100, 240, 0.4)", "fill-outline-color": "#000"}
        },
        {
            "id": "public.flood_extent_emsr864_stroke",
            "source": "public.flood_extent_emsr864",
            "source-layer": "default",
            "type": "line",
            "filter": ["==", ["geometry-type"], "LineString"],
            "paint": {"line-color": "#000", "line-width": 1, "line-opacity": 0.75}
        },
        {
            "id": "public.flood_extent_emsr864_point",
            "source": "public.flood_extent_emsr864",
            "source-layer": "default",
            "type": "circle",
            "filter": ["==", ["geometry-type"], "Point"],
            "paint": {"circle-color": "#000", "circle-radius": 2.5, "circle-opacity": 0.75}
        }
    ],
    "sources": {
        "public.flood_extent_emsr864": {
            "type": "vector",
            "scheme": "xyz",
            "tiles": ["https://api.cheias.pt/vector/collections/public.flood_extent_emsr864/tiles/WebMercatorQuad/{z}/{x}/{y}"],
            "bounds": [-9.158, 37.186, -7.397, 42.082],
            "minzoom": 0,
            "maxzoom": 22
        }
    },
    "center": [-8.278, 39.634],
    "zoom": 0
}
```

Layer id naming convention: `{collection_id}_{fill|stroke|point}`.

---

## 8. tipg-Specific Parameters

### simplify

```
GET .../items?f=geojson&limit=1&simplify=0.001
```

Applies Douglas-Peucker simplification server-side. For ogc_fid=3500:
- Without simplify: 184 vertices
- With `simplify=0.001`: 4 vertices (aggressive reduction at this tolerance)

Works, HTTP 200. Unit is degrees (CRS84).

### bbox-only

```
GET .../items?f=geojson&bbox-only=true&limit=2
```

Replaces each feature's geometry with its bounding-box rectangle (5 vertices, closed ring). Properties returned unchanged. HTTP 200. Useful for lightweight spatial queries.

---

## 9. emsr861 — Schema Comparison

```
GET https://api.cheias.pt/vector/collections/public.flood_extent_emsr861?f=json
```

Exists. 506 features, single date 2026-01-30.

Queryables are **identical** to emsr864:
- Same columns: `geom`, `activation`, `aoi`, `area_ha`, `datetime`, `event_type`, `ogc_fid`, `storm`
- Same types

Sample feature properties:
```json
{
    "activation": "EMSR861",
    "aoi": "AOI05",
    "area_ha": 57.755,
    "datetime": "2026-01-30T00:00:00+00:00",
    "event_type": "5-Flood",
    "ogc_fid": 131,
    "storm": "Kristin"
}
```

Extent: [-9.353, 39.350, -8.453, 40.261].

---

## 10. Known-Good Parameter Combos for Integration Tests

| Test | URL pattern | Expected |
|---|---|---|
| List collections | `GET /collections?f=json` | 200, `numberMatched: 13` |
| Collection metadata | `GET /collections/public.flood_extent_emsr864?f=json` | 200, extent present |
| Queryables | `GET /collections/public.flood_extent_emsr864/queryables?f=schemajson` | 200, 7 properties |
| All items (paginated) | `GET /collections/public.flood_extent_emsr864/items?f=geojson&limit=10` | 200, `numberMatched: 5496` |
| bbox filter | `GET /collections/public.flood_extent_emsr864/items?f=geojson&bbox=-8.5,39.0,-7.5,39.8&limit=2` | 200, `numberMatched: 244` |
| datetime filter | `GET /collections/public.flood_extent_emsr864/items?f=geojson&datetime=2026-02-08T00:00:00Z/2026-02-08T23:59:59Z&limit=2` | 200, `numberMatched: 5496` |
| datetime filter (miss) | `GET /collections/public.flood_extent_emsr864/items?f=geojson&datetime=2025-01-01T00:00:00Z/2025-01-01T23:59:59Z&limit=2` | 200, `numberMatched: 0` |
| CQL2 numeric | `GET /collections/.../items?f=geojson&filter=ogc_fid=3500&filter-lang=cql2-text&limit=1` | 200, `numberMatched: 1` |
| CQL2 string | `GET /collections/.../items?f=geojson&filter=event_type='5-Flood'&filter-lang=cql2-text&limit=2` | 200, 5496 matched |
| CQL2 datetime TIMESTAMP | `GET /collections/.../items?f=geojson&filter=datetime=TIMESTAMP('2026-02-08T00:00:00Z')&filter-lang=cql2-text&limit=2` | 200, 5496 matched |
| TileJSON | `GET /collections/public.flood_extent_emsr864/tiles/WebMercatorQuad/tilejson.json` | 200, `tilejson: "3.0.0"` |
| style.json | `GET /collections/public.flood_extent_emsr864/tiles/WebMercatorQuad/style.json` | 200, Mapbox GL v8 |
| simplify | `GET /collections/.../items?f=geojson&filter=ogc_fid=3500&filter-lang=cql2-text&simplify=0.001&limit=1` | 200, polygon with 4 vertices |
| bbox-only | `GET /collections/.../items?f=geojson&bbox-only=true&limit=2` | 200, geometries are 5-vertex rectangles |

---

## 11. Known Gotchas

1. **`public.` prefix is mandatory.** `flood_extent_emsr864` (no prefix) returns 404. All collection ids in this tipg instance follow `public.{table_name}` convention.

2. **Queryables format param**: `?f=schemajson` required. `?f=json` returns 422 with `"Input should be 'schemajson' or 'html'"`. This is the only endpoint that doesn't accept `?f=json`.

3. **Feature `id` is WKB hex, not ogc_fid.** The GeoJSON Feature's `id` field is the hex-encoded WKB of the geometry. The `items/{id}` endpoint tries to parse this as a geometry and fails with 500. **Single-feature access pattern: `?filter=ogc_fid=N&filter-lang=cql2-text`.**

4. **CQL2 datetime requires TIMESTAMP() wrapper.** `filter=datetime='2026-02-08'` returns 500. Use `filter=datetime=TIMESTAMP('2026-02-08T00:00:00Z')` or use the OGC `datetime` query parameter instead.

5. **System tables exposed.** tipg exposes PostGIS internal tables (`postgis_srs`, `postgis_srs_all`, etc.) and function wrappers (`st_subdivide`, `st_hexagongrid`, `st_squaregrid`). These are real collection endpoints but have no meaningful data. Filter them out in the `list_vector_collections` tool by checking for domain-relevant ids.

6. **OGC datetime parameter works correctly.** It filters on the `datetime` column server-side. Both single timestamps and `start/end` interval syntax are accepted.

7. **No auth required.** All endpoints return 200/422 with no authentication headers required. No CORS restrictions observed.

8. **Vector tile layer name is always `"default"`.** When building Mapbox GL sources from TileJSON, use `source-layer: "default"` — not the collection id.
