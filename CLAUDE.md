# Cartographer

Spatial intelligence harness for cloud-native Earth Observation.

## What's here

- `servers/eo-mcp/` — MCP server for TiTiler raster analysis + tipg vector queries
- `prompts/` — Curated workflow recipes tagged by server + complexity
- `.github/workflows/` — CI (lint, unit tests on 3.11–3.13, clean-install smoke test)

Note: `skills/` and a top-level `docs/` are planned but do not exist yet. Do not
reference them as if they do. API notes live in `servers/eo-mcp/docs/`.

## MCP Servers

### eo-mcp (this repo)

Eleven tools, all thin proxies — the server performs no geospatial computation.

- **Raster (3):** `get_raster_info`, `get_tile_url`, `get_zonal_statistics`
- **Vector (5):** `list_vector_collections`, `get_vector_features`, `get_vector_feature`, `get_vector_tile_url`, `get_collection_queryables`
- **Workflows (3):** `flood_rainfall_analysis`, `multi_layer_summary`, `collection_health_check`
- **Backends:** TiTiler at `$EO_MCP_RASTER_URL`, tipg at `$EO_MCP_VECTOR_URL`, STAC at `$EO_MCP_STAC_URL` (defaults under `https://api.cheias.pt`)
- **Run:** `cd servers/eo-mcp && uv run python -m eo_mcp.server`

If you add or remove a tool, update: this file, `README.md`, `servers/eo-mcp/README.md`,
and the `EXPECTED` set in `.github/workflows/ci.yml`. The smoke test asserts the exact
tool set, so a mismatch fails CI rather than drifting silently.

### Sidekick servers (external, install separately)

- **stac-mcp** (Wayfinder): STAC discovery. `pip install stac-mcp`
- **gdal-mcp** (JordanGunn): Local raster/vector transform. `pip install gdal-mcp`
- **duckdb-mcp**: Analytical queries over geoparquet/CSV.

## Taxonomy

- MCP server = runtime process, responds to tool calls
- Skill = static markdown, teaches Claude how to use tools
- Prompt = workflow recipe, chains tools to answer domain questions

A skill NEVER duplicates a tool. A tool NEVER contains rendering logic.

## Testing

```bash
cd servers/eo-mcp
uv run pytest -m "not slow"   # 60 unit tests, no network
uv run pytest                 # + 11 live integration tests against api.cheias.pt
uv run ruff check src/ tests/
uv run coverage run -m pytest -m "not slow" && uv run coverage report -m
```

Integration tests are excluded from CI because they depend on an external host.
Set `EO_MCP_SKIP_INTEGRATION=1` to skip them locally.

## Backends

Production eoAPI deployment (`stac-fastapi-pgstac` + `titiler-pgstac` + `tipg` + nginx),
built for the February 2026 Portuguese floods study. Deployment repo: `cheias-pt-eoapi`.

- STAC: <https://api.cheias.pt>
- Raster (TiTiler): <https://api.cheias.pt/raster>
- Vector (tipg): <https://api.cheias.pt/vector>

### Collections — verified live 2026-07-31

**STAC (11 total).** Raster assets under key `data`:
`precipitation-daily`, `soil-moisture-daily`, `precondition`, `ivt`, `mslp`,
`satellite-ir`, `sst-anomaly`, `wind-u`, `wind-v`.
Vector-backed (no raster assets; they link out to tipg):
`flood-extent-emsr861`, `flood-extent-emsr864`.

Raster collections carry `cheias:rescale` and `cheias:colormap` properties — prefer
those over guessing values for `get_tile_url`.

**Vector / tipg (13 total; 7 domain).** The `public.` schema prefix is **required**:
`public.flood_extent_emsr861`, `public.flood_extent_emsr864`, `public.frontal_boundaries`,
`public.storm_tracks`, `public.discharge_stations`, `public.rivers_portugal`,
`public.consequence_events`.
The remaining six (`postgis_srs*`, `st_subdivide`, `st_hexagongrid`, `st_squaregrid`)
are PostGIS utility tables — filter them out when listing.

Flood-extent columns: `ogc_fid`, `datetime`, `activation`, `aoi`, `area_ha`,
`event_type`, `storm`.

## Known API quirks

`servers/eo-mcp/docs/tipg-api-notes.md` records six places where the live tipg API
diverges from its documentation — the date column is `datetime` not `event_date`,
queryables need `?f=schemajson`, CQL2 needs `TIMESTAMP(...)` wrappers, and
`items/{id}` is unusable against PostGIS tables. Read it before extending vector tools.
