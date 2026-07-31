# Complementary MCP servers

`eo-mcp` does one job: analyse raster and vector data that is already published
to a cloud-native backend. It deliberately does not discover, transform, or
aggregate. Those are other servers' jobs, and pairing them works well.

These are separate projects by other authors. Install them yourself; nothing
here vendors or wraps them.

---

## stac-mcp — discovery

Searches STAC catalogues: find collections, search items by bbox/datetime/query,
read item metadata.

```bash
pip install stac-mcp
```

- PyPI: <https://pypi.org/project/stac-mcp/> (v2.3.1 at the time of writing)
- Source: <https://github.com/BnJam/stac-mcp>

**Why pair it.** `eo-mcp`'s tools take a `collection_id` and an `item_id` and
assume you already know them. stac-mcp is how an agent finds them across a large
catalogue. The division is deliberate: use stac-mcp to *find* items, `eo-mcp` to
*analyse* them.

Against the demo backend this matters less — it has 11 collections and you can
list them in one call — but against a real catalogue with thousands of Sentinel-2
scenes, search is the whole problem.

---

## gdal-mcp — local transformation

Python-native GDAL operations (Rasterio / PyProj / Shapely): reproject, convert,
clip, build COGs, inspect files on local disk.

```bash
pip install gdal-mcp
```

- PyPI: <https://pypi.org/project/gdal-mcp/> (v1.1.3 at the time of writing)
- Source: <https://github.com/Wayfinder-Foundry/gdal-mcp>

**Why pair it.** `eo-mcp` never touches local files and never does CPU-bound
work — that is the point of it being a proxy. gdal-mcp is the opposite: it works
on files on the machine it runs on. The natural workflow is gdal-mcp to prepare
and publish a COG, then `eo-mcp` to analyse it once it is in the catalogue.

Note the cost asymmetry: gdal-mcp is CPU-bound and local, so it does not belong
on a shared or containerised host in the way `eo-mcp` does.

---

## Analytical SQL over geoparquet

For aggregating large tabular/geoparquet extracts, a DuckDB-backed MCP server is
a good third leg — DuckDB's `spatial` and `httpfs` extensions read remote
geoparquet directly.

**No specific package is recommended here.** Several DuckDB MCP servers exist
with varying scope and maintenance; `duckdb-mcp` is *not* a published PyPI
package despite being an obvious name to guess. Pick one, check it is
maintained, and verify it before trusting it with your data.

---

## How they fit together

| Need | Server | Where the work happens |
|---|---|---|
| Find items in a catalogue | stac-mcp | Remote STAC API |
| Statistics / tiles over published data | **eo-mcp** | Remote TiTiler / tipg |
| Reproject, clip, build a COG | gdal-mcp | Local CPU |
| Aggregate large tabular extracts | a DuckDB server | Local process |

The rule that keeps this clean: **a tool never contains rendering logic, and a
skill never duplicates a tool.** See [ARCHITECTURE.md](../ARCHITECTURE.md).

---

*Package versions and URLs verified 2026-07-31. These are third-party projects
on their own release cadence — check for newer versions rather than trusting the
numbers above.*
