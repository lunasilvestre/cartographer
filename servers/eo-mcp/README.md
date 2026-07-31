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
| `EO_MCP_REQUEST_TIMEOUT` | `30.0` | Per-request timeout, seconds |

## MCP client config

### No install (recommended)

`uvx` fetches, builds and runs the server in one step — nothing to clone.

Claude Code:

```bash
claude mcp add eo-mcp -- uvx --from "git+https://github.com/lunasilvestre/cartographer.git#subdirectory=servers/eo-mcp" eo-mcp
```

Claude Desktop — add to `claude_desktop_config.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "eo-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/lunasilvestre/cartographer.git#subdirectory=servers/eo-mcp",
        "eo-mcp"
      ]
    }
  }
}
```

### From a local checkout

Point `command` at the interpreter in your venv, so the client doesn't depend on
shell activation:

```json
{
  "mcpServers": {
    "eo-mcp": {
      "command": "/absolute/path/to/cartographer/servers/eo-mcp/.venv/bin/python",
      "args": ["-m", "eo_mcp.server"],
      "env": {
        "EO_MCP_RASTER_URL": "https://api.cheias.pt/raster",
        "EO_MCP_VECTOR_URL": "https://api.cheias.pt/vector",
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

Verified live 2026-07-31.

**STAC — 11 collections.** Nine carry raster assets under key `data`:
`precipitation-daily`, `soil-moisture-daily`, `precondition`, `ivt`, `mslp`,
`satellite-ir`, `sst-anomaly`, `wind-u`, `wind-v`.

Two are vector-backed with no raster assets — they link out to tipg:
`flood-extent-emsr861`, `flood-extent-emsr864`.

Raster collections carry `cheias:rescale` and `cheias:colormap` properties. Read them
off the collection rather than guessing values for `get_tile_url`.

**Vector (tipg / PostGIS) — 13 collections, of which 7 are domain data.** The `public.`
schema prefix is required; the tools normalise a bare name, so either
`flood_extent_emsr864` or `public.flood_extent_emsr864` works.

`public.flood_extent_emsr861`, `public.flood_extent_emsr864`, `public.frontal_boundaries`,
`public.storm_tracks`, `public.discharge_stations`, `public.rivers_portugal`,
`public.consequence_events`.

The other six (`public.postgis_srs`, `public.postgis_srs_all`, `public.postgis_srs_search`,
`public.st_subdivide`, `public.st_hexagongrid`, `public.st_squaregrid`) are PostGIS
utility tables exposed by tipg, not data.

Flood-extent columns: `ogc_fid`, `datetime`, `activation`, `aoi`, `area_ha`, `event_type`, `storm`.

### Availability

`api.cheias.pt` is a personal deployment offered for evaluation, with no uptime
commitment. Point the server at your own eoAPI stack via the environment variables
above if you need reliability.

Data licensing is separate from this repo's Apache-2.0 code licence: flood
delineations are Copernicus EMS, meteorology is ERA5/NOAA. Check each source's terms
before reuse.

See `docs/tipg-api-notes.md` for live-probed endpoint details and quirks.
