# eo-mcp

## What it is

`eo-mcp` is a thin MCP server that proxies TiTiler (raster) and tipg (vector, OGC Features) endpoints as LLM-callable tools, plus higher-level workflow tools that chain raster + vector operations. It does zero geospatial processing — all compute runs server-side. It complements `stac-mcp` (STAC discovery): use stac-mcp to find items, then eo-mcp to analyze them.

## Tools

### Raster (TiTiler)
- **`get_raster_info`** — Band count, data type, CRS, bounds, nodata, pixel dimensions.
- **`get_tile_url`** — TileJSON URL + tile URL template, with optional color formula, colormap, rescale, band selection.
- **`get_zonal_statistics`** — Raster statistics (mean/min/max/std/count/histogram) for a GeoJSON AOI. Categorical supported.

### Vector (tipg / OGC Features)
- **`list_vector_collections`** — List available PostGIS collections with extents.
- **`get_vector_features`** — Query a collection as GeoJSON with optional bbox, datetime, CQL2 filter, properties, simplify, sortby, limit/offset.
- **`get_vector_feature`** — Fetch a single feature by `ogc_fid` (the `/items/{id}` path isn't usable against PostGIS tables; this tool routes via CQL2 filter).
- **`get_vector_tile_url`** — TileJSON + MapLibre style for MVT vector tiles.
- **`get_collection_queryables`** — Filterable properties (name + JSON-schema type) for a collection.

### Workflows (composite)
- **`flood_rainfall_analysis`** — Fetch flood polygons and compute precipitation statistics per polygon, merging results into each feature.
- **`multi_layer_summary`** — Zonal statistics from multiple raster layers over the same AOI.
- **`collection_health_check`** — Probe tipg + STAC backends and report reachability/extent per collection.

## Install

```bash
cd servers/eo-mcp
uv venv --python 3.13
uv pip install -e '.[dev]'
```

## Run

```bash
uv run python -m eo_mcp.server
```

Environment variables (all optional, defaults shown):

| Variable | Default | Description |
|---|---|---|
| `EO_MCP_RASTER_URL` | `https://api.cheias.pt/raster` | TiTiler base URL |
| `EO_MCP_VECTOR_URL` | `https://api.cheias.pt/vector` | tipg (OGC Features) base URL |
| `EO_MCP_STAC_URL` | `https://api.cheias.pt` | STAC API base URL |
| `EO_MCP_AUTH_TOKEN` | _(empty)_ | Bearer token for authenticated backends |

## MCP client config

Add to `claude_desktop_config.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "eo-mcp": {
      "command": "python",
      "args": ["-m", "eo_mcp.server"],
      "env": {
        "EO_MCP_RASTER_URL": "https://api.cheias.pt/raster",
        "EO_MCP_STAC_URL": "https://api.cheias.pt",
        "EO_MCP_AUTH_TOKEN": ""
      }
    }
  }
}
```

## Testing

```bash
# Unit tests only (no network)
uv run pytest -m "not slow"

# All tests including live integration against cheias.pt
uv run pytest

# Lint
uv run ruff check src/ tests/

# Coverage
uv run coverage run -m pytest -m "not slow" && uv run coverage report -m
```

## Backends

- STAC API: https://api.cheias.pt
- Raster (TiTiler): https://api.cheias.pt/raster
- Vector (tipg): https://api.cheias.pt/vector

### Collections

Raster collections (asset key `data` on all):
`precipitation-daily`, `soil-moisture-daily`, `ivt`, `mslp`, `precondition`, `satellite-ir`, `sst-anomaly`, `wind-u`

Vector collections (PostGIS, schema prefix `public.` required; the tools accept either `flood_extent_emsr864` or `public.flood_extent_emsr864`):
`public.flood_extent_emsr864`, `public.flood_extent_emsr861`

Flood-extent columns: `ogc_fid`, `datetime`, `activation`, `aoi`, `area_ha`, `event_type`, `storm`.

See `docs/tipg-api-notes.md` for live-probed endpoint details and quirks.
