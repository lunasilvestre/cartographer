# Cartographer

**An MCP server that lets an LLM agent query a live Earth Observation catalogue in natural language — no geospatial code required.**

`eo-mcp` exposes eleven tools over a production [eoAPI](https://eoapi.dev/) stack (STAC + TiTiler + tipg). Ask *"how much rain fell over the flooded areas near Coimbra?"* and the agent discovers the flood polygons, runs server-side zonal statistics against the precipitation raster, and answers — without transferring a single pixel to the client.

The server does **zero** geospatial processing. It is a thin, typed proxy: all compute stays on the backend.

[![CI](https://github.com/lunasilvestre/cartographer/actions/workflows/ci.yml/badge.svg)](https://github.com/lunasilvestre/cartographer/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

---

## Try it in 60 seconds

The server is pre-configured against a **live public backend** at `api.cheias.pt` — real Copernicus EMS flood delineations and ERA5 meteorology from the February 2026 Portuguese floods. You don't need to deploy anything or clone this repo.

You'll need [uv](https://docs.astral.sh/uv/getting-started/installation/).

### Claude Code

```bash
claude mcp add eo-mcp -- uvx --from "git+https://github.com/lunasilvestre/cartographer.git#subdirectory=servers/eo-mcp" eo-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

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

Restart, then ask:

> Run a health check on the eo-mcp collections.

> What flood polygons are in `public.flood_extent_emsr864` around Coimbra, and how much rain fell over them?

> Give me a tile URL for `precipitation-daily` with the viridis colormap rescaled 0–80.

### Verify it another way

If you'd rather not wire it into a client, hit the backend directly — the same endpoints the tools proxy:

```bash
curl -s https://api.cheias.pt/collections | jq '.collections[].id'
curl -s "https://api.cheias.pt/vector/collections?f=json" | jq '.collections[].id'
```

---

## The eleven tools

### Raster — TiTiler

| Tool | What it does |
|---|---|
| `get_raster_info` | Band count, dtype, CRS, bounds, nodata, pixel dimensions for a STAC item's asset. |
| `get_tile_url` | TileJSON + XYZ template, with `color_formula`, `colormap`, `rescale`, and band selection. |
| `get_zonal_statistics` | Mean/min/max/std/count/histogram/percentiles over a GeoJSON AOI. Categorical mode for classification rasters. |

### Vector — tipg / OGC API Features

| Tool | What it does |
|---|---|
| `list_vector_collections` | Available PostGIS collections with spatial and temporal extents. |
| `get_vector_features` | GeoJSON query with bbox, datetime, CQL2 filter, property selection, simplify, sortby, paging. |
| `get_vector_feature` | Single feature by `ogc_fid`. |
| `get_vector_tile_url` | TileJSON + a ready-to-use MapLibre GL style for MVT tiles. |
| `get_collection_queryables` | Filterable properties with JSON-schema types — lets an agent discover what it can filter on. |

### Workflows — composite

| Tool | What it does |
|---|---|
| `flood_rainfall_analysis` | Fetches flood polygons, computes precipitation stats per polygon, merges results back into each feature. |
| `multi_layer_summary` | Zonal statistics across several raster layers over one AOI, concurrently. |
| `collection_health_check` | Probes STAC and tipg backends, reports reachability and extent per collection. |

The workflow tools exist because chaining raster and vector calls by hand costs an agent three to five round trips and a lot of tokens. `flood_rainfall_analysis` collapses that into one call with bounded concurrency.

---

## Design notes

**Why a proxy and not a geospatial library.** Handing an LLM `rasterio` means shipping data to the client and hoping the generated code is correct. Proxying TiTiler means the computation is a well-tested server-side operation and the agent only ever sees a small JSON result. Failure modes become HTTP status codes instead of silent numerical errors.

**No arbitrary URLs.** Every tool takes a `collection_id` / `item_id` and resolves against the configured backend. No tool accepts a caller-supplied URL, so pointing this at a backend doesn't hand callers an SSRF primitive.

**Discovery is somebody else's job.** `eo-mcp` complements [`stac-mcp`](https://github.com/BnJam/stac-mcp) rather than duplicating it: use stac-mcp to *find* items, eo-mcp to *analyse* them.

**Taxonomy.** A skill never duplicates a tool; a tool never contains rendering logic.

| Type | What it is | Runtime? | Location |
|---|---|---|---|
| MCP server | Responds to tool calls | Yes | `servers/` |
| Prompt | Workflow recipe chaining tools | No | `prompts/` |

---

## The demo backend

`api.cheias.pt` is a production eoAPI deployment — `stac-fastapi-pgstac` + `titiler-pgstac` + `tipg` behind nginx — built for [cheias.pt](https://github.com/lunasilvestre/cheias-pt), a study of the February 2026 Portuguese floods.

**STAC collections (11).** Raster: `precipitation-daily`, `soil-moisture-daily`, `precondition`, `ivt`, `mslp`, `satellite-ir`, `sst-anomaly`, `wind-u`, `wind-v`. Vector-backed: `flood-extent-emsr861`, `flood-extent-emsr864`.

**Vector collections (7 domain, plus PostGIS utility tables).** `public.flood_extent_emsr861`, `public.flood_extent_emsr864`, `public.frontal_boundaries`, `public.storm_tracks`, `public.discharge_stations`, `public.rivers_portugal`, `public.consequence_events`.

The `public.` schema prefix is required. Flood-extent columns: `ogc_fid`, `datetime`, `activation`, `aoi`, `area_ha`, `event_type`, `storm`.

> **Availability.** This is a personal deployment offered for evaluation, not a service with an uptime commitment. Point the server at your own eoAPI stack via the environment variables below if you need reliability.

> **Data licensing** is separate from this repo's Apache-2.0 code licence. Flood delineations are Copernicus EMS; meteorology is ERA5/NOAA. Check each source's terms before reuse.

---

## Configuration

All optional — the defaults target the public demo backend.

| Variable | Default | Description |
|---|---|---|
| `EO_MCP_RASTER_URL` | `https://api.cheias.pt/raster` | TiTiler base URL |
| `EO_MCP_VECTOR_URL` | `https://api.cheias.pt/vector` | tipg (OGC Features) base URL |
| `EO_MCP_STAC_URL` | `https://api.cheias.pt` | STAC API base URL |
| `EO_MCP_AUTH_TOKEN` | *(empty)* | Bearer token, if your backend requires one |
| `EO_MCP_REQUEST_TIMEOUT` | `30.0` | Per-request timeout in seconds |

See [`.env.example`](.env.example).

---

## Development

```bash
git clone https://github.com/lunasilvestre/cartographer.git
cd cartographer/servers/eo-mcp

uv venv --python 3.13
uv pip install -e '.[dev]'

uv run pytest -m "not slow"   # 60 unit tests, no network
uv run pytest                 # + 11 live integration tests against api.cheias.pt
uv run ruff check src/ tests/
```

Set `EO_MCP_SKIP_INTEGRATION=1` to skip the live tests explicitly. CI runs unit tests and lint on 3.11/3.12/3.13; integration tests are not run in CI, since they depend on an external host.

`servers/eo-mcp/docs/` contains live-probed API notes for TiTiler and tipg, including six places where the real API diverges from its documentation. Worth reading before extending the tools.

---

## Repository layout

```
cartographer/
├── servers/eo-mcp/          # the MCP server
│   ├── src/eo_mcp/
│   │   ├── server.py        # FastMCP entrypoint
│   │   ├── config.py        # pydantic-settings
│   │   └── tools/           # raster.py, vector.py, workflows.py
│   ├── tests/               # 60 unit + 11 integration
│   └── docs/                # live-probed API notes
└── prompts/                 # workflow recipes
    └── disaster/flood-extent.md
```

## Licence

Apache-2.0. See [LICENSE](LICENSE).
