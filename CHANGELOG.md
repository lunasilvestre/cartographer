# Changelog

All notable changes to `eo-mcp` are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this
project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it reaches a first tagged release.

**Nothing has been released yet.** The package has never been published to PyPI
and no version has been tagged. Everything below describes work on `main`.

---

## [Unreleased]

The repository's first public state. Prior to this the code was developed in
private and the documentation had drifted badly from the live backend.

### Added

- `docs/WORKED-EXAMPLE.md` — a real end-to-end analysis (flood polygons around
  Coimbra, precipitation statistics over them) with output captured from the
  live backend rather than composed by hand.
- `docs/SELF-HOSTING.md` — the exact set of backend endpoints the server calls,
  so a reader can point it at their own eoAPI stack.
- `docs/SIDEKICKS.md` — complementary MCP servers, with package existence and
  repository URLs verified.
- `CONTRIBUTING.md`, `SECURITY.md`, this changelog, and GitHub issue and pull
  request templates.
- Apache-2.0 `LICENSE` at the repository root and under `servers/eo-mcp/`.
- `.env.example` documenting every `EO_MCP_*` setting.
- Configuration validation at startup: base URLs are checked for scheme, host
  and absence of a query string, timeouts and retry counts for sane values, each
  rejection naming the offending environment variable.
- `EO_MCP_CONNECT_TIMEOUT`, `EO_MCP_MAX_RETRIES`, `EO_MCP_RETRY_BACKOFF` and
  `EO_MCP_MAX_CONCURRENCY` settings, splitting connect from read timeouts and
  bounding retry and fan-out behaviour.
- `py.typed`, so downstream type checkers see the package's annotations.
- CI jobs beyond the unit matrix: a locked-install job (`uv lock --check`,
  `uv sync --frozen`), a clean-install smoke test that asserts the built wheel
  ships `py.typed` and that the installed console script completes a real MCP
  handshake over stdio serving exactly eleven tools, and a Docker job that runs
  the same handshake against the container and asserts a non-root UID.
- A tag-triggered PyPI release workflow (`.github/workflows/release.yml`, on
  `v*`) using trusted publishing, which verifies that the tag matches the
  packaged version and that the built wheel serves eleven tools before
  uploading. **Nothing has been published yet** — the `eo-mcp` name is available
  on PyPI but no tag has been cut, so `pip install eo-mcp` does not work.

### Changed

- Documentation restructured so each file has one job and facts are not
  duplicated across files: the root `README.md` is the front door,
  `servers/eo-mcp/README.md` is the canonical tool reference, `ARCHITECTURE.md`
  is design rationale, and `CLAUDE.md` is agent-facing instructions rather than
  a second user manual.
- `fastmcp` requirement corrected to `>=3.0,<4`. The 2.x server API is
  genuinely incompatible — tool results moved from `list[Content]` to
  `ToolResult` and `fastmcp.exceptions` was restructured.
- `ruff` pinned to `>=0.16,<0.17` with an explicit `[tool.ruff.lint]` select,
  because ruff's default rule set widens between releases and was turning the
  build red on linter releases rather than code changes.

### Fixed

- **Collection lists across all documentation were fictional.** They named
  collections that do not exist on the backend (`copernicus-dem-30`,
  `sentinel-2-l2a`, `spi-*`, `sm-composite` and others). Replaced with lists
  probed live on 2026-07-31: 11 STAC collections (9 with raster assets under key
  `data`, 2 vector-backed) and 13 tipg collections of which 7 are domain data.
- **`prompts/disaster/flood-extent.md` instructed the agent to take the flood
  footprint from the STAC item.** That item's geometry is a placeholder
  rectangle covering all of mainland Portugal. Following the recipe computed
  rainfall over roughly 171,700 km² and reported it as the flood footprint,
  against an actual flooded area of 892 km² — an area error of about 193x,
  returned with HTTP 200 and no warning. The recipe now sources flood geometry
  from tipg, and the failure is documented so readers do not repeat it.
- The root `README.md` verification snippet listed STAC collections without a
  `limit`, returning 10 of 11 and silently omitting `wind-v` three lines below
  the text claiming 11 collections.
- Documented demo prompt asked for a `viridis` colormap; the collection actually
  publishes `cheias:colormap = "blues"`.
- `ARCHITECTURE.md` promised a top-level `docs/` containing `PREREQUISITES.md`,
  `SETUP-ATLAS.md`, `SETUP-SLIPLANE.md` and `SIDEKICKS.md`, none of which
  existed, while `CLAUDE.md` said the directory did not exist. Resolved: the
  personal-infrastructure setup guides are gone from the promise, and `docs/`
  now exists with content that is real.
- `CLAUDE.md` credited `stac-mcp` to "Wayfinder" and `gdal-mcp` to "JordanGunn";
  per PyPI metadata `stac-mcp` is by BnJam and `gdal-mcp` is Wayfinder-Foundry.
  It also listed `duckdb-mcp` as installable — no such package exists on PyPI.

- **Chaining a vector query into a statistics call always failed with a 413.**
  tipg sets each GeoJSON Feature's `id` to the hex-encoded WKB of its geometry
  and repeats it inside a `links` URL, so a Feature for one simplified flood
  polygon was 2,115,084 bytes of which the geometry was 93,578 — 95.6% redundant
  bulk, past nginx's 1 MiB body cap, returned as an HTML error page rather than
  JSON. `get_vector_features` now strips `id` and `links`, bringing the same
  Feature to 93,808 bytes.
- **`collection_health_check` reported 10 of 11 STAC collections**, silently
  omitting `wind-v`, because it read `/collections` without a `limit` and STAC
  pages at 10. It now follows the pagination.
- **`get_vector_features` gave no signal that a result was a page.** It now sets
  `truncated` and carries a `note` stating how many of the matching features are
  shown, so a partial result cannot be described as the complete set.
- **`simplify` could silently discard geometries.** Past a tolerance of about
  `0.0005`, tipg stops coarsening geometries and returns them as `null` — HTTP
  200 with properties intact. On `flood_extent_emsr864`, `0.001` — the obvious
  round number — nulls 61% of the collection, and because `area_ha` is a stored
  column an area total still looks correct. `get_vector_features` now reports
  `null_geometry_count` and a `null_geometry_note` naming the tolerance as the
  cause, and the documentation recommends `0.0001` throughout.
- Backend errors now name the environment variable that configures the backend
  they came from, so a misconfigured URL is distinguishable from a missing
  collection without a second round trip.
- `serverInfo.version` reported FastMCP's version rather than the package's, so
  clients were told `eo-mcp` was version `3.x`. It now reports the packaged
  version, asserted in CI against `pyproject.toml`.

- **`get_vector_feature` was vulnerable to CQL2 filter injection.** `feature_id`
  was interpolated unvalidated into a CQL2 expression, so `"1 OR 1=1"` returned a
  *different* feature and reported it as a success. It must now parse as an
  integer `ogc_fid`, rejected before any HTTP call.
- **`get_tile_url` applied collection-level rescale without checking it fitted
  the item.** `precipitation-daily` publishes `cheias:rescale = [0, 80]` while its
  `precipitation-storm-total` item runs 100–582, so the default clamped every
  pixel to the top of the ramp and rendered a flat image with no error. The
  collection value is now used only when it spans the item's data range,
  otherwise the item's own 2nd–98th percentiles are used, and `rescale_source`
  reports which won.
- `collection_health_check` reported `item_count: 1` where the backend gave no
  real total. It now reports `null` for unknown, with a separate `has_items`.
- **`flood_rainfall_analysis` advertised a `total` that does not exist.** Its
  docstring promised "mean, max, min, total"; the statistics block has no such
  key. The nearest thing, `sum`, is pixel values added together — for a depth
  raster that is millimetres summed across pixels, with no physical meaning, and
  `sum / count` is simply `mean`. A reader told to expect a total would have
  reported one. Both docstrings now say so, and a unit test asserts that no tool
  description advertises a total.
- **`multi_layer_summary` silently dropped layers with colliding labels.**
  Results are keyed by label and the default label is `collection_id/item_id`, so
  requesting two *assets of the same item* returned fewer layers than asked for
  with `partial_failures: 0`. Duplicate labels are now a pre-flight `ToolError`.

### Known issues

- Requests above roughly 1 MB are rejected by the demo backend's edge — a `413`
  from nginx on one network path, a `403` from Cloudflare on another, HTML rather
  than JSON either way. Stripping the tipg envelope is not always enough: the
  largest polygon in `flood_extent_emsr864` (`ogc_fid=111`, 10,437 ha) is
  1,817,111 bytes on geometry alone and needs `simplify`. A simplified AOI
  answers about a slightly different shape — 339.67 mm at tolerance `0.0001`
  versus 338.26 mm at `0.001` for the same polygon, against a raster whose cells
  are 0.02° (~2.2 km), so `0.001` moves the boundary about 1/20th of a pixel.
- `color_formula` operates on RGB bands and every raster on the demo backend is
  single-band float32. `get_tile_url` returns a valid TileJSON, but fetching a
  tile gives `HTTP 500 index 1 is out of bounds for axis 0 with size 1`. The
  parameter is passed through rather than rejected, since a multi-band backend
  would want it.
- Where an item publishes no usable percentiles (`satellite-ir`),
  `get_tile_url` reports `item_data_range: null` and applies the collection's
  rescale unverified. Accepted rather than fixed: there is nothing to check
  against, and the null is the signal.

---

## Earlier history

Development before the public state, from commit messages:

- **v0.3 + v0.4** — vector tools over tipg (5) and composite workflow tools (3).
- **v0.1** — initial `eo-mcp` server with the three raster tools over TiTiler,
  plus the live-probed TiTiler and tipg API notes under `servers/eo-mcp/docs/`.

These were never tagged or released; `pyproject.toml` still declares `0.1.0`.
