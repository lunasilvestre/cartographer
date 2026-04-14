# TiTiler API Discovery Notes

Scouted: 2026-04-14  
Backend: titiler-pgstac  
Base URL: `https://api.cheias.pt/raster`  
STAC API: `https://api.cheias.pt` (same host, root path)

---

## Critical Delta: Domain Names

The spec assumes `raster.cheias.pt` and `stac.cheias.pt`. **Neither subdomain resolves in public DNS.**

Actual endpoints:
- **STAC API** → `https://api.cheias.pt/`
- **TiTiler** → `https://api.cheias.pt/raster/`
- **COG data** → `https://data.cheias.pt/` (R2/object storage, resolves to Cloudflare)
- `titiler.cheias.pt` — NXDOMAIN (referenced in one `tilejson` asset href, but that's stale/wrong)

---

## 1. Verified URL Templates

All paths are relative to `https://api.cheias.pt/raster`. The `tileMatrixSetId` is always `WebMercatorQuad` (verified).

### Tool: info
```
GET /collections/{collection_id}/items/{item_id}/info?assets={asset}
GET /collections/{collection_id}/items/{item_id}/info.geojson?assets={asset}
```

### Tool: tilejson
```
GET /collections/{collection_id}/items/{item_id}/WebMercatorQuad/tilejson.json?assets={asset}
```
Note: TileMatrixSetId is **path segment**, not query param. The tile template URL returned:
```
https://api.cheias.pt/raster/collections/{collection_id}/items/{item_id}/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?assets={asset}
```

### Tool: point
```
GET /collections/{collection_id}/items/{item_id}/point/{lon},{lat}?assets={asset}
```

### Tool: statistics
```
POST /collections/{collection_id}/items/{item_id}/statistics?assets={asset}
GET  /collections/{collection_id}/items/{item_id}/statistics?assets={asset}
```
POST body must be a GeoJSON **Feature** (not bare Polygon, not FeatureCollection). Returns a Feature with `properties.statistics`.  
GET returns whole-item statistics keyed by band name (no AOI clipping).

### Collection-level (mosaic) — confirmed working
```
GET /collections/{collection_id}/info
GET /collections/{collection_id}/WebMercatorQuad/tilejson.json?assets={asset}
POST /collections/{collection_id}/statistics?assets={asset}
GET /collections/{collection_id}/point/{lon},{lat}?assets={asset}
```

---

## 2. Known-Good Test Triple

| Field         | Value                                                                                              |
|---------------|----------------------------------------------------------------------------------------------------|
| collection_id | `precipitation-daily`                                                                              |
| item_id       | `precipitation-storm-total`                                                                        |
| asset         | `data`                                                                                             |
| Item URL      | `https://api.cheias.pt/collections/precipitation-daily/items/precipitation-storm-total`           |
| COG href      | `https://data.cheias.pt/precipitation/storm-total.tif`                                             |
| Item bbox     | `[-9.6, 36.9, -6.1, 42.2]` (EPSG:4326)                                                           |
| dtype         | float32, 1 band, 175×265 px, overviews at levels 2 & 4                                            |

All other collections also use `data` as the sole raster asset key. `tilejson` is a second asset key but it is a link to a TileJSON document, not a raster band.

---

## 3. Canned GeoJSON Polygon

Small polygon inside item bounds, verified to return real statistics:

```json
{
  "type": "Feature",
  "geometry": {
    "type": "Polygon",
    "coordinates": [[
      [-8.2, 39.3],
      [-7.8, 39.3],
      [-7.8, 39.7],
      [-8.2, 39.7],
      [-8.2, 39.3]
    ]]
  },
  "properties": {}
}
```

Center: lon=-8.0, lat=39.5 (near Portalegre, Portugal). Returns 400 valid pixels.

---

## 4. Example Responses

### 4a. Info — `GET /raster/collections/precipitation-daily/items/precipitation-storm-total/info?assets=data`

```json
{
    "data": {
        "bounds": [-9.6, 36.9, -6.1, 42.2],
        "crs": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "band_metadata": [["b1", {}]],
        "band_descriptions": [["b1", ""]],
        "dtype": "float32",
        "nodata_type": "Nodata",
        "colorinterp": ["gray"],
        "scales": [1.0],
        "offsets": [0.0],
        "driver": "GTiff",
        "count": 1,
        "width": 175,
        "height": 265,
        "overviews": [2, 4],
        "nodata_value": null
    }
}
```

Top-level key is the **asset name** (`data`), not a fixed key like `info`.

### 4b. TileJSON — `GET /raster/collections/precipitation-daily/items/precipitation-storm-total/WebMercatorQuad/tilejson.json?assets=data`

```json
{
    "tilejson": "3.0.0",
    "version": "1.0.0",
    "scheme": "xyz",
    "tiles": [
        "https://api.cheias.pt/raster/collections/precipitation-daily/items/precipitation-storm-total/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?assets=data"
    ],
    "minzoom": 0,
    "maxzoom": 24,
    "bounds": [-9.6, 36.9, -6.1, 42.2],
    "center": [-7.85, 39.55, 0]
}
```

Note: `maxzoom: 24` is the TiTiler default ceiling, not derived from overviews. Effective native zoom is ~8–9.

### 4c. Point — `GET /raster/collections/precipitation-daily/items/precipitation-storm-total/point/-8.0,39.5?assets=data`

```json
{
    "coordinates": [-8.0, 39.5],
    "values": [272.8667297363281],
    "band_names": ["data_b1"],
    "band_descriptions": ["data_"]
}
```

Band name pattern: `{asset}_{band}` (e.g. `data_b1`). Same pattern appears in statistics keys.

### 4d. Statistics POST — body: Feature (see §3), `POST /raster/collections/.../statistics?assets=data`

```json
{
    "type": "Feature",
    "geometry": { "...polygon..." },
    "properties": {
        "statistics": {
            "data_b1": {
                "min": 226.8108367919922,
                "max": 361.79986572265625,
                "mean": 278.4316815948486,
                "count": 400.0,
                "sum": 111372.67263793945,
                "std": 31.575363192935487,
                "median": 273.80279541015625,
                "majority": 226.8108367919922,
                "minority": 226.8108367919922,
                "unique": 400.0,
                "histogram": [
                    [42, 54, 64, 76, 52, 37, 29, 20, 14, 12],
                    [226.81, 240.31, 253.81, 267.31, 280.81, 294.31, 307.80, 321.30, 334.80, 348.30, 361.80]
                ],
                "valid_percent": 90.91,
                "masked_pixels": 40.0,
                "valid_pixels": 400.0,
                "percentile_2": 229.21926879882812,
                "percentile_98": 351.2545166015625
            }
        }
    }
}
```

Statistics key pattern: `{asset}_{band}` (e.g. `data_b1`). Response wraps the input geometry back as a Feature.

### 4e. Collection-Level Info — `GET /raster/collections/precipitation-daily/info`

```json
{
    "search": {
        "hash": "42feb35cac095241169445d43c82ab02",
        "search": {
            "collections": ["precipitation-daily"],
            "filter-lang": "cql2-json"
        },
        "metadata": {
            "type": "mosaic",
            "bounds": [-9.6, 36.9, -6.1, 42.2],
            "name": "Mosaic for 'precipitation-daily' Collection"
        }
    },
    "links": [
        {
            "href": "https://api.cheias.pt/raster/collections/precipitation-daily/{tileMatrixSetId}/tilejson.json",
            "rel": "tilejson",
            "templated": true
        }
    ]
}
```

---

## 5. Deltas vs. Spec

| Spec assumption                         | Reality                                                                                      |
|-----------------------------------------|----------------------------------------------------------------------------------------------|
| Base URL `https://raster.cheias.pt`     | **WRONG** — NXDOMAIN. Actual: `https://api.cheias.pt/raster`                               |
| STAC URL `https://stac.cheias.pt`       | **WRONG** — NXDOMAIN. Actual: `https://api.cheias.pt`                                      |
| Default asset `cog_default`             | **WRONG** — all collections use `data`. There is no `cog_default` asset.                   |
| TileJSON path `.../tilejson.json?...`   | **Partial** — tileMatrixSetId is a **path segment**, not query param. Path: `/{tileMatrixSetId}/tilejson.json` |
| Known collections (copernicus-dem-30, sentinel-2-l2a, emsr864-observed-event-a, spi-*, sm-composite, soil-moisture-anomaly, precipitation-anomaly) | **WRONG** — none of these exist. Actual collections: `flood-extent-emsr861`, `flood-extent-emsr864`, `ivt`, `mslp`, `precipitation-daily`, `precondition`, `satellite-ir`, `soil-moisture-daily`, `sst-anomaly`, `wind-u` |
| Statistics POST body: Feature or bare Polygon | Feature **works**. Bare Polygon **fails** (422). FeatureCollection **works** but returns a different response shape (FeatureCollection of Features). Use Feature for single-AOI calls. |
| Auth requirements                       | No auth observed on any endpoint (all 200/422, no 401/403).                                 |
| `asset_statistics` endpoint             | Exists (`GET /items/{item_id}/asset_statistics`). Returns stats keyed as `{asset}.{band}` (e.g. `data.b1`) — different key format from `statistics` endpoint (`data_b1`). |

---

## 6. Gotchas

1. **DNS**: `raster.cheias.pt` and `stac.cheias.pt` are NXDOMAIN from all resolvers tested (local, 8.8.8.8, 1.1.1.1). The whole API surface lives under `api.cheias.pt`. The app's JS bundle reveals `api.cheias.pt/raster` — that's the correct discovery path.

2. **`tilejson` asset in STAC items is a dead link**: Each item has a `tilejson` asset pointing to `titiler.cheias.pt/cog/...` which also NXDOMAIN. Ignore that asset — use the TiTiler-pgstac route at `api.cheias.pt/raster` instead.

3. **Band name key format differs by endpoint**:
   - `statistics` and `point` → `{asset}_{band}` (underscore: `data_b1`)
   - `asset_statistics` → `{asset}.{band}` (dot: `data.b1`)
   - `info` → top-level key is asset name, inner key is bare `b1`

4. **`maxzoom: 24` is synthetic** — TiTiler returns 24 as the ceiling but the COG only has overviews at levels 2 and 4. Requesting very high zoom tiles will succeed but upsample aggressively. A reasonable display maxzoom is ~10.

5. **Response time**: All endpoints responded in < 1s with no rate limiting observed. No auth headers, no CORS restrictions detected on tested routes.

6. **`flood-extent-emsr861/864` items have empty `assets: {}`** — these are vector-only collections served via `api.cheias.pt/vector` (tipg OGC Features API). The TiTiler raster routes will fail for them.

7. **Tile URL scale suffix**: The TileJSON returns tile URLs with `@1x` suffix (e.g. `/{z}/{x}/{y}@1x`). The API also supports `@2x` for higher DPI. The plain `/{z}/{x}/{y}` (no suffix) also works.

---

## Appendix: All Available STAC Collections

```
flood-extent-emsr861   — vector only, no raster assets
flood-extent-emsr864   — vector only, no raster assets
ivt                    — raster, assets: [data, tilejson*]
mslp                   — raster, assets: [data, tilejson*]
precipitation-daily    — raster, assets: [data, tilejson*]
precondition           — raster, assets: [data, tilejson*]
satellite-ir           — raster, assets: [data, tilejson*]
soil-moisture-daily    — raster, assets: [data, tilejson*]
sst-anomaly            — raster, assets: [data, tilejson*]
wind-u                 — raster, assets: [data, tilejson*]
```
*`tilejson` asset is a metadata link, not a raster band. Pass `assets=data` to TiTiler.

## Appendix: OpenAPI Route Map (full list)

```
GET  /
GET  /algorithms
GET  /algorithms/{algorithmId}
GET  /collections/{collection_id}/WMTSCapabilities.xml
GET  /collections/{collection_id}/bbox/{minx},{miny},{maxx},{maxy}.{format}
GET  /collections/{collection_id}/bbox/{minx},{miny},{maxx},{maxy}/assets
GET  /collections/{collection_id}/bbox/{minx},{miny},{maxx},{maxy}/{width}x{height}.{format}
POST /collections/{collection_id}/feature
POST /collections/{collection_id}/feature.{format}
POST /collections/{collection_id}/feature/{width}x{height}.{format}
GET  /collections/{collection_id}/info
GET  /collections/{collection_id}/items/{item_id}/WMTSCapabilities.xml
GET  /collections/{collection_id}/items/{item_id}/asset_statistics
GET  /collections/{collection_id}/items/{item_id}/assets
GET  /collections/{collection_id}/items/{item_id}/bbox/{minx},{miny},{maxx},{maxy}.{format}
GET  /collections/{collection_id}/items/{item_id}/bbox/{minx},{miny},{maxx},{maxy}/{width}x{height}.{format}
POST /collections/{collection_id}/items/{item_id}/feature
POST /collections/{collection_id}/items/{item_id}/feature.{format}
POST /collections/{collection_id}/items/{item_id}/feature/{width}x{height}.{format}
GET  /collections/{collection_id}/items/{item_id}/info
GET  /collections/{collection_id}/items/{item_id}/info.geojson
GET  /collections/{collection_id}/items/{item_id}/point/{lon},{lat}
GET  /collections/{collection_id}/items/{item_id}/preview
GET  /collections/{collection_id}/items/{item_id}/preview.{format}
GET  /collections/{collection_id}/items/{item_id}/preview/{width}x{height}.{format}
GET  /collections/{collection_id}/items/{item_id}/renders
GET  /collections/{collection_id}/items/{item_id}/renders/{render_id}
GET,POST /collections/{collection_id}/items/{item_id}/statistics
GET  /collections/{collection_id}/items/{item_id}/tiles
GET  /collections/{collection_id}/items/{item_id}/tiles/{tileMatrixSetId}
GET  /collections/{collection_id}/items/{item_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}
GET  /collections/{collection_id}/items/{item_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}.{format}
GET  /collections/{collection_id}/items/{item_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}@{scale}x
GET  /collections/{collection_id}/items/{item_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}@{scale}x.{format}
GET  /collections/{collection_id}/items/{item_id}/{tileMatrixSetId}/map.html
GET  /collections/{collection_id}/items/{item_id}/{tileMatrixSetId}/tilejson.json
GET  /collections/{collection_id}/point/{lon},{lat}
GET  /collections/{collection_id}/point/{lon},{lat}/assets
POST /collections/{collection_id}/statistics
GET  /collections/{collection_id}/tiles
GET  /collections/{collection_id}/tiles/{tileMatrixSetId}
GET  /collections/{collection_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}
GET  /collections/{collection_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}.{format}
GET  /collections/{collection_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}/assets
GET  /collections/{collection_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}@{scale}x
GET  /collections/{collection_id}/tiles/{tileMatrixSetId}/{z}/{x}/{y}@{scale}x.{format}
GET  /collections/{collection_id}/{tileMatrixSetId}/map.html
GET  /collections/{collection_id}/{tileMatrixSetId}/tilejson.json
GET  /colorMaps
GET  /colorMaps/{colorMapId}
GET  /conformance
GET  /healthz
GET  /searches/
GET  /searches/list
POST /searches/register
... (searches/{search_id} mirrors collections/{collection_id} routes)
GET  /tileMatrixSets
GET  /tileMatrixSets/{tileMatrixSetId}
```
