# eo-mcp

An MCP server exposing raster analysis (TiTiler), vector queries (tipg) and
composite geospatial workflows as LLM-callable tools.

This is the package README and the **canonical tool reference**. For the project
pitch, a worked example with real output, and the design rationale, start at the
[repository root](../../README.md).

The server performs **no geospatial computation**. It builds URLs, validates
parameters, forwards HTTP requests and shapes responses. All compute stays on the
backend.

---

## Install

### No checkout

```bash
uvx --from "git+https://github.com/lunasilvestre/cartographer.git#subdirectory=servers/eo-mcp" eo-mcp
```

> Not yet executable against GitHub — this repository is not public at the time
> of writing, so the URL cannot resolve. The command's *form* is verified: the
> identical invocation against `git+file://` on a local clone builds and starts
> the server, as does `uvx --from ./servers/eo-mcp eo-mcp`.

**`pip install eo-mcp` and `uvx eo-mcp` do not work yet.** The PyPI name is
reserved and a tag-triggered release workflow is in place, but nothing has been
published. Until the first `v*` tag ships, use one of the paths below.

### From a checkout

```bash
cd servers/eo-mcp
uv sync --extra dev
uv run python -m eo_mcp.server
```

`uv sync` creates the virtualenv and installs the exact versions from the
committed `uv.lock`. Don't mix `uv pip install` with `uv run` — `uv run`
re-syncs from the lockfile and silently discards what `uv pip install` did. See
[CONTRIBUTING.md](../../CONTRIBUTING.md#setup).

No `uv`? Plain pip works:

```bash
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/eo-mcp
```

### Docker

```bash
cd servers/eo-mcp
docker build -t eo-mcp .
docker run --rm -i eo-mcp
```

The server speaks MCP over stdio, so `-i` is required and there is no port to
publish. Without it stdin is closed and the container exits immediately.

Pass configuration with `-e`:

```bash
docker run --rm -i \
  -e EO_MCP_RASTER_URL=https://your-host/raster \
  -e EO_MCP_VECTOR_URL=https://your-host/vector \
  -e EO_MCP_STAC_URL=https://your-host \
  eo-mcp
```

The image is multi-stage, installs from `uv.lock` for reproducibility, runs as a
non-root user (uid 10001), and ships `py.typed`. Roughly 306 MB uncompressed,
69.6 MB compressed. CI builds it and drives a real MCP handshake against the
container on every push, so it is tested rather than merely built.

Requires Python 3.11+. Runtime dependencies: `fastmcp>=3.0,<4`, `httpx`,
`pydantic`, `pydantic-settings`.

---

## MCP client configuration

### Claude Code

```bash
claude mcp add eo-mcp -- uvx --from "git+https://github.com/lunasilvestre/cartographer.git#subdirectory=servers/eo-mcp" eo-mcp
```

### Claude Desktop

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

### Docker

```json
{
  "mcpServers": {
    "eo-mcp": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "eo-mcp"]
    }
  }
}
```

### From a local checkout

Point `command` at the venv interpreter so the client does not depend on shell
activation:

```json
{
  "mcpServers": {
    "eo-mcp": {
      "command": "/absolute/path/to/cartographer/servers/eo-mcp/.venv/bin/python",
      "args": ["-m", "eo_mcp.server"],
      "env": {
        "EO_MCP_RASTER_URL": "https://api.cheias.pt/raster",
        "EO_MCP_VECTOR_URL": "https://api.cheias.pt/vector",
        "EO_MCP_STAC_URL": "https://api.cheias.pt"
      }
    }
  }
}
```

---

## Configuration

Every setting is optional; defaults target the public demo backend. All are read
from `EO_MCP_`-prefixed environment variables.

| Variable | Default | Description |
|---|---|---|
| `EO_MCP_RASTER_URL` | `https://api.cheias.pt/raster` | TiTiler base URL |
| `EO_MCP_VECTOR_URL` | `https://api.cheias.pt/vector` | tipg base URL |
| `EO_MCP_STAC_URL` | `https://api.cheias.pt` | STAC API base URL |
| `EO_MCP_AUTH_TOKEN` | *(empty)* | Bearer token, sent to all three backends |
| `EO_MCP_CONNECT_TIMEOUT` | `5.0` | Seconds to establish a connection |
| `EO_MCP_REQUEST_TIMEOUT` | `30.0` | Seconds for the response body |
| `EO_MCP_MAX_RETRIES` | `2` | Retries after the first attempt; `0` disables |
| `EO_MCP_RETRY_BACKOFF` | `0.5` | Exponential backoff base, seconds |
| `EO_MCP_MAX_CONCURRENCY` | `5` | Max in-flight requests per backend |

URLs are validated at startup. Trailing slashes are normalised away, `http://` is
accepted for local backends, and a missing scheme, empty value or query string is
rejected with a message naming the variable. Full behaviour in
[docs/SELF-HOSTING.md](../../docs/SELF-HOSTING.md#url-validation).

---

## Error contract

**Every failure raises `fastmcp.exceptions.ToolError`.** No tool returns an
`{"error": ...}` dictionary.

This matters for an LLM-facing server. A returned dictionary is a *successful*
tool result as far as MCP is concerned, so a model reading it has no signal that
anything went wrong and will narrate its contents as fact. `ToolError` marks the
result as an error, which both the client and the model can see.

Four failure classes:

| Class | When | Raised |
|---|---|---|
| Invalid argument | Rejected before any HTTP call | `ToolError` |
| Backend HTTP error | Any status ≥ 400 surviving retries | `ToolError` |
| Transport failure | Connect error or read timeout, after retries | `ToolError` |
| Malformed payload | A 200 missing a needed key, or a non-JSON body | `ToolError` |

Messages carry the backend, status, method, full request URL, the backend's own
response body, and **the environment variable that configures that backend** — so
"is this a missing collection or a wrong URL?" is answerable at a glance. Real
examples:

```
eo-mcp: raster backend returned HTTP 404 for
GET https://api.cheias.pt/raster/collections/precipitation-daily/items/nope/info?assets=data
— {"detail":"No item 'nope' found in 'precipitation-daily' collection"}
(this backend is configured by EO_MCP_RASTER_URL)
```

```
eo-mcp: vector backend returned HTTP 404 for
GET https://api.cheias.pt/vector/collections/public.no_such_table/queryables?f=schemajson
— {"detail":"Table/Function 'public.no_such_table' not found."}
```

```
eo-mcp: geojson must be a Polygon, MultiPolygon, Feature, or FeatureCollection;
got type='Nonsense'. Statistics are computed over an area, so points and lines
cannot be used.
```

### Partial failure in the workflow tools

**The three workflow tools can return a successful-looking result that contains
failures.** They aggregate over many backend calls, where a per-item failure is
the tool's product rather than a tool failure. They raise only when they can
produce no useful result at all.

**Every one of them carries a top-level `partial_failures` integer. Check it.**
A non-zero value means part of the answer is missing, and the response is
otherwise shaped exactly like a complete one.

| Tool | Per-item signal | Raises only when |
|---|---|---|
| `flood_rainfall_analysis` | each feature gets `properties.rainfall_stats` **or** `properties.rainfall_error`; top-level `failed` lists them | the vector query fails, or *every* feature's stats failed |
| `multi_layer_summary` | each layer carries `ok: true\|false`, failed ones carry `error`; top-level `failed` | *every* layer failed |
| `collection_health_check` | `reachable: false` per collection; top-level `unreachable` list | it cannot enumerate from either backend at all |

For `collection_health_check`, a down collection is **the answer, not an error** —
reporting unreachability is the tool's entire job, so it will not raise for it.

Real shape, one good layer and one bogus item:

```json
{
  "partial_failures": 1,
  "failed": ["bogus"],
  "layers": {
    "rain":  { "ok": true,  "...": "..." },
    "bogus": { "ok": false, "error": "eo-mcp: raster backend returned HTTP 404 for POST …" }
  }
}
```

`flood_rainfall_analysis` also reports `features_analysed`, `features_attempted`,
`features_matched`, `truncated` and `truncation_note`, so a partial answer cannot
be mistaken for a complete one. An empty result carries a `note` explaining *why*
it is empty, rather than looking like a possible bug.

The other eight tools are all-or-nothing: a complete result, or `ToolError`.

### Retries

| Failure | Retried? |
|---|---|
| Connect error / connect timeout | Yes |
| 429, 500, 502, 503, 504 | Yes |
| **Read or write timeout** | **No** |
| Any other 4xx | No |

Only requests safe to repeat are retried: `GET`/`HEAD`/`OPTIONS`, plus the
read-only `POST /statistics` calls which opt in explicitly.

**A read timeout is deliberately not retried.** A backend that accepts the socket
and then never answers costs a full `EO_MCP_REQUEST_TIMEOUT` per attempt, so
retrying turns the default 30 s budget into roughly 92 s of silence for one tool
call — past the point where MCP clients give up. It is also the least likely
retry to succeed, since a backend hung on a slow query hangs the same way next
time. A *connect* failure is the opposite: it fails in `EO_MCP_CONNECT_TIMEOUT`
(5 s) and is exactly the transient a retry fixes.

A read timeout instead raises immediately with a message suggesting you narrow
the request or raise `EO_MCP_REQUEST_TIMEOUT`.

Backoff is exponential with full jitter so concurrent fan-out does not
resynchronise into a thundering herd, capped at 10 s per sleep. A `Retry-After`
header wins over the computed delay.

---

## Tool reference

Eleven tools. Parameter names, types and defaults below are extracted from the
running server's schemas.

### Raster — TiTiler

#### `get_raster_info`

Band count, data type, CRS, bounds, nodata and pixel dimensions for a STAC item's
raster asset.

| Parameter | Type | Default |
|---|---|---|
| `collection_id` | string | *required* |
| `item_id` | string | *required* |
| `asset` | string | `"data"` |

Returns TiTiler's info document keyed by asset name. Every raster collection on
the demo backend uses the asset key `data`; there is no `cog_default`.

#### `get_tile_url`

TileJSON document plus an XYZ tile URL template.

| Parameter | Type | Default |
|---|---|---|
| `collection_id` | string | *required* |
| `item_id` | string | *required* |
| `asset` | string | `"data"` |
| `color_formula` | string \| null | `None` |
| `colormap` | string \| null | `None` |
| `rescale` | string \| null | `None` |
| `bidx` | array \| null | `None` |

The tile matrix set is always `WebMercatorQuad`. `rescale` takes a
`"min,max"` string.

**Rescale is a checked default, not a magic one.** The full rule:

> An argument you pass wins. Otherwise the collection's `cheias:rescale` is used
> if it **spans** the item's data range. If it does not span the data, the item's
> own 2nd–98th percentiles are used instead, because applying the collection
> value would clamp every pixel to one flat colour. If it spans the data but the
> data occupies **less than a third** of the ramp, the collection value is still
> used — a shared scale is what makes items in a collection comparable — and
> `rescale_note` explains the low contrast and gives you the value to pass for a
> single-item view.

That third clause is deliberate and was measured. Occupancy of the ramp carries
real signal: on `precipitation-daily`'s `[0, 80]`, a dry day occupies 11% and a
wet day 40%. Rescaling each item to its own percentiles would render them
identically, and a reader comparing two tiles would conclude the drought was a
flood. **A dry day should render dim.** So a too-wide ramp is *reported*, not
corrected; only a ramp that does not fit at all is overridden.

Measured live, all four cases:

| Item | `rescale` | `rescale_source` | `item_data_range` | `rescale_note` |
|---|---|---|---|---|
| `wind-u-2026-02-15T18` | `-20,20` | `collection-metadata` | `[-9.6, 17.6]` | `null` |
| `precondition-2026-02-15` | `0,100` | `collection-metadata` | `[0.0, 20.2]` | low-contrast advisory |
| `precipitation-storm-total` | `133.435…,516.470…` | `item-percentiles` | `[133.4, 516.5]` | override explanation |
| any, with `rescale="100,600"` | `100,600` | `caller` | — | `null` |

```json
"render": {
  "asset": "data",
  "colormap": "blues",
  "rescale": "133.43502807617188,516.470703125",
  "rescale_source": "item-percentiles",
  "color_formula": null,
  "from_collection_metadata": ["colormap"],
  "item_data_range": [133.43502807617188, 516.470703125],
  "rescale_note": "The collection publishes cheias:rescale 0,80, which does not span this item's data (133.435 to 516.471) — applying it would clamp every pixel and render a flat image. Using the item's own 2nd-98th percentiles instead. Note this tile is NOT on the same scale as other items in this collection."
}
```

`rescale_source` is one of `caller`, `collection-metadata`, `item-percentiles`,
`none`. `from_collection_metadata` names what was inherited — the colormap is
inherited here because a colormap has no range to clash with.

> **When the tool overrides, the tile stops being comparable to its siblings.**
> That is the cost of not rendering a flat image, and the note says so. If you
> are building a time series or an animation, keep every frame on the collection
> ramp by passing the same explicit `rescale` to all of them.

**`item_data_range: null` means the check could not run.** Some items publish no
usable percentiles — `satellite-ir` is one — so the collection value was taken on
trust and nothing was verified. It is the one case where you cannot compare the
applied rescale against the data before trusting a tile.

Resolving the item range costs one extra call to the item's statistics endpoint,
cached per item for the life of the process.

> **`color_formula` breaks these rasters.** It operates on RGB bands, and every
> raster on the demo backend is single-band float32. The tool returns a valid
> TileJSON, but fetching a tile gives
> `HTTP 500 {"detail":"index 1 is out of bounds for axis 0 with size 1"}`.
> Leave it unset unless your backend serves genuine multi-band imagery.

#### `get_zonal_statistics`

Statistics for a raster clipped to a GeoJSON area of interest: min, max, mean,
median, std, count, sum, histogram, percentiles, and valid/masked pixel counts.

> **There is no `total`, and `sum` is not one.** `sum` is the arithmetic sum of
> pixel values, so for a depth-valued raster it is millimetres added together
> across pixels — not a depth, not a volume, no physical meaning. `sum / count`
> is simply `mean` again. A genuine catchment total needs pixel area × depth, and
> this server computes no geometry by design. Report the mean with the area
> beside it.

| Parameter | Type | Default |
|---|---|---|
| `collection_id` | string | *required* |
| `item_id` | string | *required* |
| `geojson` | object | *required* |
| `asset` | string | `"data"` |
| `categorical` | boolean | `false` |
| `histogram_bins` | integer \| null | `None` |

`geojson` accepts a bare Polygon or MultiPolygon (wrapped into a Feature for
you), a Feature, or a FeatureCollection. A FeatureCollection is passed through
whole — TiTiler computes statistics per feature and returns a FeatureCollection,
rather than silently analysing only the first. Points and lines are rejected.

> **Body size limit.** The AOI is sent as a request body, and requests above
> roughly 1 MB are rejected by the demo backend's edge — as **413 or 403
> depending on the network path**, and as an HTML page rather than JSON either
> way. The tool turns both into a `ToolError` naming the body size and
> suggesting `simplify`.
>
> `get_vector_features` strips tipg's WKB-hex `id` and `links` — about 95% of a
> raw Feature — so its output chains into this tool directly. What remains is
> geometry size, and the largest polygons still exceed the cap on their own:
> `ogc_fid=111` (10,437 ha) is 1,817,111 bytes even after stripping. Use
> `simplify` on `get_vector_features` for large areas. See the
> [worked example](../../docs/WORKED-EXAMPLE.md#the-trap-the-tools-remove-for-you).

### Vector — tipg / OGC API Features

#### `list_vector_collections`

Available collections with spatial and temporal extents.

| Parameter | Type | Default |
|---|---|---|
| `include_internal` | boolean | `false` |

tipg exposes PostGIS's own function-backed tables in the same schema as real
data, and an agent shown those will eventually try to query one. They are
excluded by default, leaving the 7 domain collections — but the exclusion is
**visible, not silent**: the response carries an `excluded_internal` list.

```json
{
  "collections": [ "…7 domain collections…" ],
  "excluded_internal": [
    "public.postgis_srs", "public.postgis_srs_all", "public.postgis_srs_search",
    "public.st_subdivide", "public.st_hexagongrid", "public.st_squaregrid"
  ]
}
```

`include_internal=true` returns everything. The filter matches the PostGIS naming
convention (`st_`, `postgis_`, `spatial_ref_sys`, `geometry_columns`) rather than
a hardcoded list of this deployment's tables, so it generalises to your own
backend. `collection_health_check` skips them too.

#### `get_vector_features`

Query a collection as GeoJSON.

| Parameter | Type | Default |
|---|---|---|
| `collection_id` | string | *required* |
| `limit` | integer | `10` |
| `offset` | integer | `0` |
| `bbox` | array \| null | `None` |
| `datetime` | string \| null | `None` |
| `filter` | string \| null | `None` |
| `filter_lang` | string | `"cql2-text"` |
| `properties` | array \| null | `None` |
| `simplify` | number \| null | `None` |
| `sortby` | string \| null | `None` |
| `geom_column` | string \| null | `None` |

- `bbox` is `[min_lon, min_lat, max_lon, max_lat]` in WGS84.
- `datetime` takes an ISO 8601 instant or `start/end` interval.
- `sortby` accepts a column, `-` prefixed for descending (`"-area_ha"`).
- `simplify` is a Douglas-Peucker tolerance **in degrees**, applied server-side.
  **Use `0.0001`** — see the warning below. Report the tolerance alongside any
  number derived from a simplified geometry.

> **`simplify` above ~`0.0005` silently destroys geometries.** Past that
> tolerance tipg stops coarsening and starts returning `null` geometry: HTTP 200,
> properties intact, shape gone. Measured over 500 features of
> `flood_extent_emsr864`:
>
> | `simplify` | Null geometry |
> |---|---|
> | none / `0.0001` | 0 of 500 |
> | `0.0005` | 143 of 500 (29%) |
> | `0.001` | 305 of 500 (61%) |
> | `0.01` | 481 of 500 (96%) |
>
> `0.001` is the obvious round number and it loses most of the collection. The
> response reports **`null_geometry_count`** and a **`null_geometry_note`** naming
> the cause whenever any feature comes back without geometry — check it whenever
> you pass `simplify`. Note that `area_ha` is a stored column and survives a null
> geometry, so an area total will look correct while the shapes are missing.

**Paged results say so.** Compare `numberMatched` (true count) against
`numberReturned` (bounded by `limit`). When they differ the response sets
`truncated: true` and carries a `note`:

> Showing 1 of 87 matching features (limit=1, offset=0). This is a page, not the
> whole result — do not describe it as the complete set. Raise limit, page with
> offset, or narrow with bbox/filter.

Each returned Feature is `{type, geometry, properties}`. tipg's `id` and `links`
are stripped, because it sets `id` to the hex-encoded WKB of the geometry and
repeats it in `links` — about 95% of the payload, and enough to push a statistics
request past the backend's 1 MiB body cap.

CQL2 datetime comparisons need a `TIMESTAMP(...)` wrapper — a bare date string
returns 500 from tipg. See [tipg-api-notes.md](docs/tipg-api-notes.md).

#### `get_vector_feature`

A single feature by `ogc_fid`.

| Parameter | Type | Default |
|---|---|---|
| `collection_id` | string | *required* |
| `feature_id` | string | *required* |

**`feature_id` must contain an integer `ogc_fid`.** Anything else is rejected
before any HTTP call:

```
eo-mcp: feature_id must be an integer ogc_fid, got '1 OR 1=1'.
Read ogc_fid from a feature's properties in get_vector_features.
```

That validation is load-bearing. The tool routes via a CQL2 `ogc_fid=` filter
rather than `items/{id}` — tipg sets the GeoJSON feature `id` to the geometry's
WKB hex, so `items/{id}` tries to parse it as a geometry and returns 500 — and an
unvalidated `feature_id` interpolated into a CQL2 expression was a filter
injection. `"1 OR 1=1"` used to return a *different* feature, reported as a
success.

Get `ogc_fid` from `properties` on a `get_vector_features` result.

#### `get_vector_tile_url`

TileJSON plus a MapLibre GL style for MVT vector tiles.

| Parameter | Type | Default |
|---|---|---|
| `collection_id` | string | *required* |

The vector tile layer id is always `"default"`, not the collection id — use it as
`source-layer` when building your own style.

#### `get_collection_queryables`

Filterable properties with JSON-schema types, so an agent can discover what it
can filter on before constructing a CQL2 expression.

| Parameter | Type | Default |
|---|---|---|
| `collection_id` | string | *required* |

Requests `?f=schemajson`; this endpoint rejects `?f=json` with a 422.

### Workflows — composite

These aggregate many backend calls. They report per-item failures in band with a
`partial_failures` count rather than raising, and raise only when no useful
result is possible.

#### `flood_rainfall_analysis`

Fetches flood polygons and computes precipitation statistics for each, merging
results back into every feature.

| Parameter | Type | Default |
|---|---|---|
| `flood_collection_id` | string | `"public.flood_extent_emsr864"` |
| `precipitation_collection_id` | string | `"precipitation-daily"` |
| `precipitation_item_id` | string | `"precipitation-storm-total"` |
| `event_date` | string \| null | `None` |
| `limit` | integer | `5` |

Takes the first `limit` polygons in collection order; it does not accept a
bounding box. For a specific area, use `get_vector_features` with `bbox` and then
`get_zonal_statistics`, as in the
[worked example](../../docs/WORKED-EXAMPLE.md).

Response carries `partial_failures`, `failed`, `truncated` and `truncation_note`
alongside the FeatureCollection. Each feature gets `properties.rainfall_stats` or
`properties.rainfall_error`. See
[Partial failure](#partial-failure-in-the-workflow-tools).

**Three counts, all different.** Reading only one of them is how you get a wrong
answer:

| Field | Meaning |
|---|---|
| `features_matched` | How many polygons match the query in the backend |
| `features_attempted` | How many the tool tried to analyse — bounded by `limit`, and fewer if fewer match |
| `features_analysed` | How many produced statistics — fewer if any failed |

Real output for `limit=2` against `flood_extent_emsr864`:

```json
{
  "features_analysed": 2,
  "features_attempted": 2,
  "features_matched": 5496,
  "truncated": true
}
```

`analysed: 2` next to `attempted: 2` reads as "2 of 2, complete" on its own —
`features_matched` is the field that tells you it is a sample of 5,496.

**`features_matched` is omitted entirely when the backend reports no total**,
rather than falling back to the attempted count. Same rule as `item_count: null`
on `collection_health_check`: a fabricated total is worse than an absent one.

#### `multi_layer_summary`

Zonal statistics across several raster layers over one AOI, concurrently.

| Parameter | Type | Default |
|---|---|---|
| `geojson` | object | *required* |
| `raster_layers` | array | *required* |

Each entry in `raster_layers` is `{collection_id, item_id, asset?, label?}`.
`asset` defaults to `"data"`; `label` defaults to `"{collection_id}/{item_id}"`.

**Labels must be unique.** Results are keyed by label, so duplicates would
silently overwrite each other and return fewer layers than you asked for with
`partial_failures: 0`. A collision is now a pre-flight `ToolError`. This needs no
user error to hit: since the default label omits the asset, requesting two
*assets of the same item* collides. Give those layers explicit `label`s.

Each layer in the response carries `ok: true|false`, with `error` on failures,
plus top-level `partial_failures` and `failed`.

#### `collection_health_check`

Probes STAC and tipg, reporting reachability, type, and extent or item count per
collection.

| Parameter | Type | Default |
|---|---|---|
| `collection_ids` | array \| null | `None` |

With `None` it enumerates both backends, following STAC's pagination so an
11-collection catalogue does not silently report 10. Supplied ids are routed by
shape: ids containing a `.` go to tipg, others to STAC. PostGIS utility tables
are skipped.

Response carries `raster`, `vector`, plus top-level `partial_failures` and
`unreachable` (a list of ids), and `enumeration_errors` when a backend's listing
itself failed. **A collection being down is the answer, not an error** — this
tool does not raise for unreachability.

Per collection, `item_count` is `null` where the backend does not report a real
total rather than being asserted as `1`, and a separate `has_items` boolean says
whether anything is there. A null count means unknown, not empty.

---

## Backends

- STAC API: <https://api.cheias.pt>
- Raster (TiTiler): <https://api.cheias.pt/raster>
- Vector (tipg): <https://api.cheias.pt/vector>

The collection inventory for the demo backend is in the
[root README](../../README.md#the-demo-backend). Pointing at your own stack is
covered in [docs/SELF-HOSTING.md](../../docs/SELF-HOSTING.md).

`api.cheias.pt` is a personal deployment offered for evaluation, with no uptime
commitment. Data licensing is separate from this repo's Apache-2.0 code licence:
flood delineations are Copernicus EMS, meteorology is ERA5/NOAA.

### API notes

`docs/tipg-api-notes.md` and `docs/titiler-api-notes.md` record where the live
APIs diverge from their documentation, with the requests that demonstrate each
one. Read them before extending the tools.

---

## Testing

```bash
uv run ruff check src/ tests/
EO_MCP_SKIP_INTEGRATION=1 uv run pytest -m "not slow" -q   # unit, no network
uv run pytest                                              # + live integration
uv run coverage run -m pytest -m "not slow" && uv run coverage report -m
```

The integration tests hit the live backend and **never run in CI**. Skip them
with `EO_MCP_SKIP_INTEGRATION=1` or `-m "not slow"`.

Full contributor workflow: [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Licence

Apache-2.0. See [LICENSE](LICENSE).
