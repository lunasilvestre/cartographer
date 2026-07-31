# Cartographer

Spatial intelligence harness for cloud-native Earth Observation.

**This file is operating instructions for an agent working on this repository.**
It is not user documentation. When a fact belongs to a reader, it lives in the
document named below and this file links to it rather than copying it — a
duplicated fact is a fact that will go stale.

| You need | Read |
|---|---|
| What the project is, install, config | `README.md` |
| Tool signatures and the error contract | `servers/eo-mcp/README.md` |
| Why it is built this way | `ARCHITECTURE.md` |
| A real analysis with real output | `docs/WORKED-EXAMPLE.md` |
| The backend contract, self-hosting | `docs/SELF-HOSTING.md` |
| What CI enforces | `CONTRIBUTING.md` |
| Where the live API diverges from its docs | `servers/eo-mcp/docs/*-api-notes.md` |

## Layout

- `servers/eo-mcp/` — the MCP server (TiTiler raster + tipg vector)
- `prompts/` — workflow recipes tagged by server and complexity
- `docs/` — reader documentation
- `.github/workflows/` — CI

There is no `skills/` directory and no skills. Do not reference one as if there
were; create it only when writing the first skill.

## Hard constraints

**Exactly eleven tools, with these names.** CI asserts the set via
`EXPECTED_TOOLS` in `.github/scripts/mcp_stdio_smoke.py`, which drives a real MCP
handshake, so a rename or addition fails the build rather than drifting past the
documents that quote it.

- **Raster (3):** `get_raster_info`, `get_tile_url`, `get_zonal_statistics`
- **Vector (5):** `list_vector_collections`, `get_vector_features`, `get_vector_feature`, `get_vector_tile_url`, `get_collection_queryables`
- **Workflows (3):** `flood_rainfall_analysis`, `multi_layer_summary`, `collection_health_check`

Changing a tool's *parameters* is ordinary work. Changing the *set* needs
agreement first, and then updates to all of: `.github/scripts/mcp_stdio_smoke.py`
(the `EXPECTED_TOOLS` set), `README.md`, `servers/eo-mcp/README.md`, and this
file. `ci.yml` contains no tool names — it only invokes the script.

**The server performs no geospatial computation.** It builds URLs, validates
parameters, forwards requests, shapes responses. If a change needs `numpy`,
`rasterio` or `shapely`, it is in the wrong repository.

**No tool accepts a URL, hostname or file path.** Every tool resolves against the
configured backend from a `collection_id` / `item_id`. Accepting a caller-supplied
URL would make the server an SSRF primitive. See `SECURITY.md`.

**Never state a backend fact you have not probed.** The documentation in this
repo was wrong once because collection names were written from memory. Verify:

```bash
curl -s "https://api.cheias.pt/collections?limit=100" | jq -r '.collections[].id'
curl -s "https://api.cheias.pt/vector/collections?f=json" | jq -r '.collections[].id'
```

## Running and testing

```bash
cd servers/eo-mcp
uv sync --extra dev                 # creates .venv from uv.lock
uv run python -m eo_mcp.server

uv run ruff check src/ tests/
EO_MCP_SKIP_INTEGRATION=1 uv run pytest -m "not slow" -q
uv run pytest                       # + live integration tests

EO_MCP_SKIP_INTEGRATION=1 uv run coverage run -m pytest -m "not slow"
uv run coverage report              # floor set in [tool.coverage.report], branch on
```

**Never mix `uv pip install` with `uv run`.** `uv run` re-syncs from `uv.lock`
and silently discards what `uv pip install` did. Use `uv sync` + `uv run`, or
`uv pip install` + `.venv/bin/…` — never one of each.

Integration tests hit a live personal backend. They must stay skippable via
`EO_MCP_SKIP_INTEGRATION=1` and must **never** gate CI — a backend outage must
not turn the build red.

## Error contract

Every failure raises `fastmcp.exceptions.ToolError`. **No tool returns an
`{"error": ...}` dictionary**, because a returned dict is a *successful* result
as far as MCP is concerned, and a model will narrate its contents as fact.

The three workflow tools are the deliberate exception: they aggregate many calls,
report per-item failures in band with a `partial_failures` count, and raise only
when no useful result is possible.

Full contract, retry policy and real error messages: `servers/eo-mcp/README.md`.

## Backends

Production eoAPI deployment (`stac-fastapi-pgstac` + `titiler-pgstac` + `tipg` +
nginx), built for the February 2026 Portuguese floods study. Deployment repo:
`cheias-pt-eoapi`.

- STAC: <https://api.cheias.pt>
- Raster: <https://api.cheias.pt/raster>
- Vector: <https://api.cheias.pt/vector>

The collection inventory (11 STAC, 13 tipg of which 7 are domain data) is in
`README.md`. The `public.` prefix is required on tipg collection ids; the tools
normalise a bare name.

`list_vector_collections` now filters PostGIS utility tables itself and reports
them under `excluded_internal` — do not tell anyone to filter them by hand, and
do not add a hardcoded exclusion list; the filter matches the PostGIS naming
convention so it generalises to other backends.

## Failure modes that matter here

This is a proxy, so the dangerous failures are silent rather than loud. All three
below return HTTP 200. Guarding against *confidently wrong* is the design problem.

**The `flood-extent-*` STAC items are not flood footprints.** Their geometry is a
placeholder rectangle covering mainland Portugal. Using one as an AOI computes
over ~193x too much area and returns a precise, plausible, wrong number. Flood
geometry always comes from tipg. Worked through in `docs/WORKED-EXAMPLE.md`.

**STAC `/collections` pages at 10** and this catalogue has 11. An unpaginated
read silently omits `wind-v`. Pass `?limit=100` in any curl you write.
`collection_health_check` follows the pagination itself.

**`cheias:rescale` is collection-level but ranges are per-item.**
`precipitation-daily` publishes `[0, 80]`; its `precipitation-storm-total` item
runs 100–582. `get_tile_url` checks before applying: the collection value is used
if it spans the item's range, otherwise the item's own 2nd–98th percentiles.
`rescale_source` says which won. Not a plain default — a checked one.

**A too-wide ramp is reported, not corrected.** If the collection value spans the
data but the data uses under a third of it, the collection value still applies
and `rescale_note` explains. This is deliberate: a shared scale is what makes
sibling items comparable, and per-item rescaling would render a dry day and a wet
day identically. Do not "fix" it. Conversely, when the tool *does* override, the
tile is no longer on the same scale as its siblings — say so if a reader is
building a time series.

**`item_data_range: null` means the check could not run** — the item published no
usable percentiles (`satellite-ir`), so the collection value was taken on trust.
The only case where the applied rescale cannot be compared against the data.

**`color_formula` breaks these rasters.** It operates on RGB bands; every raster
here is single-band float32. TileJSON returns fine and the tile then 500s with
`index 1 is out of bounds for axis 0 with size 1`. Never put it in an example.

**There is no `total` in a statistics block, and `sum` is not one.** `sum` adds
pixel values, so for a depth raster it is millimetres summed across pixels —
`sum / count` is just `mean` again. Never report it as a rainfall total; that is
a confident nonsense number a reader has no way to catch. A real catchment total
needs pixel area × depth, which this server does not compute by design. Report
the mean with the area beside it. Areas *are* additive; rainfall is not.

**Requests above roughly 1 MB are rejected by the edge** — 413 or 403 depending
on network path, HTML not JSON either way. `get_vector_features` strips tipg's
WKB-hex `id` and `links` (~95% of a raw Feature), but the largest polygons still
exceed the cap alone (`ogc_fid=111` is 1.8 MB stripped), so use
**`simplify=0.0001`** for large polygons.

**Never recommend a coarser `simplify` without checking.** Past about `0.0005`
tipg stops coarsening and returns **null geometry** instead — HTTP 200,
properties intact, shape gone. Measured on `flood_extent_emsr864`: `0.0005` nulls
29%, `0.001` nulls **61%**, `0.01` nulls 96%; `0.0001` loses none. `0.001` is the
obvious round number to reach for and it is the wrong one.
`get_vector_features` reports `null_geometry_count` and `null_geometry_note`.
`area_ha` is a stored column that survives a null geometry, so an area total
looks correct while the shapes are missing — it cannot detect the loss or measure
simplification error.

More in `servers/eo-mcp/docs/tipg-api-notes.md`, which records six places the
live tipg API diverges from its documentation — the date column is `datetime` not
`event_date`, queryables need `?f=schemajson`, CQL2 needs `TIMESTAMP(...)`
wrappers, and `items/{id}` is unusable against PostGIS tables.

## Taxonomy

- **MCP server** — runtime process, responds to tool calls (`servers/`)
- **Prompt** — workflow recipe chaining tools (`prompts/`)
- **Skill** — static markdown teaching a model to use tools (none exist)

A skill never duplicates a tool. A tool never contains rendering logic.

## Sidekick servers

External projects that pair well; install separately. Details and verified
versions in `docs/SIDEKICKS.md`.

- **stac-mcp** (BnJam) — STAC discovery. `pip install stac-mcp`
- **gdal-mcp** (Wayfinder-Foundry) — local raster/vector transform. `pip install gdal-mcp`

For analytical SQL over geoparquet a DuckDB-backed server works well, but no
specific package is endorsed here — `duckdb-mcp` is not a published PyPI package
despite being an obvious name to guess.
