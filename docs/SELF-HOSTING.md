# Running against your own backend

The demo backend at `api.cheias.pt` is a personal deployment with no uptime
commitment. If you need reliability, or you want to analyse your own data, point
`eo-mcp` somewhere else. Nothing in the server is specific to the demo.

---

## The short version

Set three environment variables:

```bash
export EO_MCP_STAC_URL=https://your-host/stac
export EO_MCP_RASTER_URL=https://your-host/raster
export EO_MCP_VECTOR_URL=https://your-host/vector
```

Or in your MCP client config:

```json
{
  "mcpServers": {
    "eo-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/lunasilvestre/cartographer.git#subdirectory=servers/eo-mcp", "eo-mcp"],
      "env": {
        "EO_MCP_STAC_URL": "https://your-host/stac",
        "EO_MCP_RASTER_URL": "https://your-host/raster",
        "EO_MCP_VECTOR_URL": "https://your-host/vector"
      }
    }
  }
}
```

Then confirm it took:

> Run a health check on the eo-mcp collections.

The three URLs are independent. You can point the raster tools at your own
TiTiler while leaving the vector tools on the demo, or any other combination.

---

## What the server requires of a backend

`eo-mcp` is a proxy, so "self-hosting" means running the services it proxies. It
never invents paths — these are the only endpoints it calls, extracted from
`src/eo_mcp/tools/`.

### Against `EO_MCP_STAC_URL` — a STAC API

| Path | Used by |
|---|---|
| `GET /collections` | `collection_health_check` |
| `GET /collections/{collection_id}/items` | `collection_health_check` |

### Against `EO_MCP_RASTER_URL` — TiTiler (specifically `titiler-pgstac`)

| Path | Used by |
|---|---|
| `GET /collections/{collection_id}/items/{item_id}/info` | `get_raster_info` |
| `GET /collections/{collection_id}/items/{item_id}/WebMercatorQuad/tilejson.json` | `get_tile_url` |
| `POST /collections/{collection_id}/items/{item_id}/statistics` | `get_zonal_statistics`, `flood_rainfall_analysis`, `multi_layer_summary` |

Note the shape: these are the *pgstac* routes, keyed by STAC collection and item,
not the plain-COG routes (`/cog/info?url=…`). A vanilla TiTiler without the pgstac
extension will not serve them. This is deliberate — see
[SECURITY.md](../SECURITY.md) on why no tool accepts a URL.

The `tileMatrixSetId` is hard-coded to `WebMercatorQuad`.

### Against `EO_MCP_VECTOR_URL` — tipg (OGC API Features)

| Path | Used by |
|---|---|
| `GET /collections` | `list_vector_collections`, `collection_health_check` |
| `GET /collections/{collection_id}` | `collection_health_check` |
| `GET /collections/{collection_id}/items` | `get_vector_features`, `get_vector_feature`, `flood_rainfall_analysis` |
| `GET /collections/{collection_id}/queryables` | `get_collection_queryables` |
| `GET /collections/{collection_id}/tiles/WebMercatorQuad/tilejson.json` | `get_vector_tile_url` |
| `GET /collections/{collection_id}/tiles/WebMercatorQuad/style.json` | `get_vector_tile_url` |

---

## Deploying the stack

The demo runs [eoAPI](https://eoapi.dev/) — `stac-fastapi-pgstac` +
`titiler-pgstac` + `tipg` over PostGIS/pgstac, behind nginx. The eoAPI project
publishes deployment recipes for Docker Compose, Kubernetes (Helm) and AWS CDK:

- <https://github.com/developmentseed/eoAPI>
- <https://eoapi.dev/>

**This repository does not ship a backend deployment and does not attempt to
document one.** eoAPI's own documentation is better than anything that could be
maintained here, and a stale copy of someone else's install guide is worse than
a link. If you already have a STAC API, a TiTiler and a tipg, you have
everything `eo-mcp` needs.

---

## Configuration reference

| Variable | Default | Notes |
|---|---|---|
| `EO_MCP_STAC_URL` | `https://api.cheias.pt` | Base URL, no trailing path beyond the API root |
| `EO_MCP_RASTER_URL` | `https://api.cheias.pt/raster` | TiTiler base |
| `EO_MCP_VECTOR_URL` | `https://api.cheias.pt/vector` | tipg base |
| `EO_MCP_AUTH_TOKEN` | *(empty)* | Sent as `Authorization: Bearer …` to all three |
| `EO_MCP_CONNECT_TIMEOUT` | `5.0` | Seconds to establish a connection |
| `EO_MCP_REQUEST_TIMEOUT` | `30.0` | Seconds for the response body |
| `EO_MCP_MAX_RETRIES` | `2` | Retries after the first attempt; `0` disables |
| `EO_MCP_RETRY_BACKOFF` | `0.5` | Exponential backoff base, seconds |
| `EO_MCP_MAX_CONCURRENCY` | `5` | Max in-flight requests per backend |

### URL validation

Base URLs are validated when the server starts. Verified behaviour:

| You set | Result |
|---|---|
| `https://api.cheias.pt/raster/` | Accepted; trailing slash normalised away |
| `http://localhost:8000` | Accepted; the scheme is never silently upgraded |
| `""` | Rejected: *"EO_MCP_RASTER_URL is empty. Set it to a base URL…"* |
| `api.cheias.pt/raster` | Rejected: *"…which has no URL scheme. Prefix it with https://"* |
| `ftp://api.cheias.pt` | Rejected: *"…whose scheme 'ftp' is not supported. Use http:// or https://."* |
| `https://api.cheias.pt/raster?x=1` | Rejected: *"A base URL must not carry a query string or a fragment."* |

Timeouts must be greater than zero; `EO_MCP_MAX_RETRIES` and
`EO_MCP_RETRY_BACKOFF` must be zero or more; `EO_MCP_MAX_CONCURRENCY` must be at
least one. Each rejection names the offending variable.

---

## Things your backend will need to get right

These are the places the demo backend's behaviour surprised us. If you are
building your own, they are worth checking.

**Request body size.** `get_zonal_statistics` POSTs the AOI as a GeoJSON body.
Requests above roughly 1 MB are rejected by the demo backend's edge — a `413`
from nginx on one network path, a `403` from Cloudflare on another, HTML rather
than JSON either way. nginx's `client_max_body_size` defaults to 1 MiB; raise it
if you serve large geometries, and check whatever CDN sits in front.

Two things inflate the body: raw geometry (one Copernicus EMS polygon in the demo
data is 31,437 vertices — use `simplify`), and tipg's Feature envelope, whose
WKB-hex `id` and `links` are ~95% of the payload. `get_vector_features` strips
the latter for you, but the largest polygons still exceed the cap on geometry
alone. See the
[worked example](WORKED-EXAMPLE.md#the-trap-the-tools-remove-for-you).

**Collection pagination.** STAC's `/collections` pages by default — the demo
returns 10 of 11 unless you pass `?limit=100`. If your catalogue has more
collections than one page, anything that reads the unpaginated list will be
quietly incomplete.

**Asset keys.** Every raster collection in the demo uses the asset key `data`,
which is why that is the default for `asset` on the raster tools. If yours uses
something else, pass `asset=` explicitly.

**Schema prefixes.** tipg exposes PostGIS tables as `public.{table}`. The tools
normalise a bare name to add the prefix. They also filter PostGIS's own utility
tables (`postgis_srs*`, `st_subdivide`, `st_hexagongrid`, `st_squaregrid`) out of
`list_vector_collections`.

More divergences between the live API and its documentation are recorded in
[`servers/eo-mcp/docs/tipg-api-notes.md`](../servers/eo-mcp/docs/tipg-api-notes.md)
and [`titiler-api-notes.md`](../servers/eo-mcp/docs/titiler-api-notes.md).
