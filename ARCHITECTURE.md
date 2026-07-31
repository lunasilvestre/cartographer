# Architecture

Why Cartographer is built the way it is. For *what it does* and *how to run it*,
start at the [README](README.md).

---

## The core decision: a proxy, not a library

The obvious way to give an LLM geospatial capability is to hand it `rasterio` and
a Python sandbox. Cartographer deliberately does not do that.

| | Generated code | Proxied service |
|---|---|---|
| Where compute happens | Client | Backend |
| Data transferred | Whole rasters | A small JSON result |
| Correctness | Depends on generated code | A tested server-side operation |
| Failure mode | Silent numerical error | HTTP status code |
| Attack surface | Arbitrary code execution | A fixed set of typed parameters |

The last two rows matter most for an LLM-facing tool. When an agent writes its
own windowed-read-and-mean, a subtly wrong result looks exactly like a correct
one. When it calls `POST /statistics`, a wrong request is a 4xx.

So: **`eo-mcp` performs no geospatial computation.** It builds URLs, validates
parameters, forwards requests, and shapes responses. If a tool ever needs
`numpy`, it is the wrong shape for this repo.

### The consequence worth stating plainly

A proxy inherits its backend's failure modes, and the interesting ones are silent
rather than loud. Three real examples from the demo backend, all of which return
HTTP 200:

- STAC `/collections` pages at 10, so an unpaginated read of an 11-collection
  catalogue is quietly incomplete.
- The `flood-extent-*` STAC items carry a national placeholder bbox, not a flood
  footprint. Using one as an AOI computes over an area ~193x too large and
  returns a precise, plausible, wrong number. This is walked through in the
  [worked example](docs/WORKED-EXAMPLE.md#the-mistake-this-example-exists-to-prevent).
- A collection-level `cheias:rescale` can be wildly wrong for an individual item
  inside that collection, producing a saturated tile with no error.

Guarding against *confidently wrong* is the design problem here, not guarding
against crashes. That is why the tools filter PostGIS utility tables out of
collection listings, normalise the `public.` prefix, and route single-feature
lookups through a CQL2 filter rather than tipg's broken `items/{id}`.

### A second, subtler class: correct output, wrong takeaway

Some responses contain no false statement anywhere and still mislead, because a
reader takes their meaning from one part and the truth lives in another. Two that
were found and fixed here:

- `get_zonal_statistics` returns a `sum`. It is the arithmetic sum of pixel
  values, so for a depth raster it is millimetres added across pixels —
  `sum / count` is just `mean` again. Nothing is wrong with returning it. But it
  is the only field shaped like a total, so a reader looking for one lands on it
  and reports a number with no physical meaning.
- `flood_rainfall_analysis` used to report `features_analysed: 2` beside
  `features_requested: 2`, with "2 of 5496" living only in a prose note. The
  integers said "complete"; the sentence said "sample". A model reading structured
  fields and skipping prose got the wrong answer from an entirely truthful
  response.

The fix in both cases is the same shape: **put the disambiguating fact in the
part the reader actually consumes.** `features_matched` became a field rather
than a phrase; the `sum` caveat sits in the tool's own description rather than
only in the documentation. Where a number cannot be known, it is omitted
(`features_matched`, `item_count`, `item_data_range`) rather than filled with a
plausible substitute — an absent value prompts a question, a fabricated one does
not.

This class is harder to test for than an outright error, because every assertion
you would write about the response passes.

---

## Taxonomy

| Type | What it is | Runtime? | Where it lives |
|---|---|---|---|
| **MCP server** | Process that responds to tool calls | Yes — needs a host | `servers/` |
| **Prompt** | Curated workflow recipe that chains tools | No — markdown template | `prompts/` |
| **Skill** | Static markdown that teaches a model to use tools | No — read at context load | *(none exist)* |

### Rules

- A skill never duplicates a tool. A tool never contains rendering logic.
- If a capability needs runtime → it is a **tool** in an MCP server.
- If it is knowledge about how to use existing tools → it is a **skill**.
- If it is a worked example that chains tools → it is a **prompt**.

### On `skills/`

There is no `skills/` directory and no skills. Three were once sketched —
`geo-viz` (render tile URLs as MapLibre artifacts), `geo-auto` (COG conversion
and STAC publishing patterns), `geo-ml` (Clay embeddings, classification) — and
none were written.

The directory will be created when the first skill is, not before. An empty
directory promising future work is a liability in a public repo.

---

## Server stack

| Server | Role | Compute | In this repo? |
|---|---|---|---|
| **eo-mcp** | Analyse cloud raster/vector | None — HTTP proxy | Yes |
| stac-mcp | Discover items in a catalogue | None — HTTP proxy | No, [see SIDEKICKS](docs/SIDEKICKS.md) |
| gdal-mcp | Transform local files | CPU-bound | No, [see SIDEKICKS](docs/SIDEKICKS.md) |

Only `eo-mcp` is developed here. The others are third-party projects that pair
well; the boundary between them is the taxonomy above.

---

## Tool design

Eleven tools in three groups. The split is by backend, except for the workflows.

**Raster (3)** and **vector (5)** are thin: one tool, roughly one backend call,
parameters that map onto the underlying API.

**Workflows (3)** are composite, and they exist for a specific reason: an agent
chaining raster and vector calls by hand costs three to five round trips and a
lot of tokens, and each hop is a chance to lose the thread. `flood_rainfall_analysis`
collapses that into one call with bounded concurrency.

The cost of a composite tool is that it makes decisions the caller cannot see. That
is an acceptable trade for a well-understood workflow and a bad one for a general
capability — which is why there are three workflow tools and not thirty.

### The tool count is load-bearing

`EXPECTED_TOOLS` in `.github/scripts/mcp_stdio_smoke.py` asserts the exact set of
eleven tool names, and CI drives that script through a real MCP stdio handshake
against both the installed console script and the Docker image. Adding, removing
or renaming a tool fails the build rather than drifting silently past the
documents that quote the list. Changing the set is a deliberate act, not an
accident.

The assertion deliberately lives in a standalone script rather than inline in
`ci.yml`: it is the artifact that turns "eleven tools" from a claim repeated in
four documents into something the build can falsify, and it is runnable by hand.

---

## Prompt tagging convention

```yaml
---
title: Flood Extent Mapping
domain: disaster-response
servers_required: [eo-mcp]
servers_optional: [stac-mcp]
complexity: basic    # basic (1-3 tools) | intermediate (4-6) | advanced (7+)
tool_calls: 3
demo_endpoint: api.cheias.pt
---
```

`skills_required` is omitted because no skills exist.

---

## Repository layout

Actual layout, not aspirational. There is no top-level `skills/`.

```
cartographer/
├── README.md                    # front door: what, try-it, tools, config
├── ARCHITECTURE.md              # this file: why
├── CONTRIBUTING.md              # dev workflow, what CI enforces
├── SECURITY.md                  # threat model, reporting
├── CHANGELOG.md
├── CLAUDE.md                    # agent-facing instructions, not user docs
├── LICENSE                      # Apache-2.0
├── .env.example
├── .github/
│   ├── workflows/               # ci.yml, release.yml (tag-triggered PyPI)
│   ├── scripts/                 # mcp_stdio_smoke.py — the 11-tool assertion
│   ├── ISSUE_TEMPLATE/
│   └── PULL_REQUEST_TEMPLATE.md
├── docs/
│   ├── WORKED-EXAMPLE.md        # real question, real calls, real output
│   ├── SELF-HOSTING.md          # point it at your own eoAPI stack
│   └── SIDEKICKS.md             # complementary MCP servers
├── servers/
│   └── eo-mcp/
│       ├── README.md            # package README + full tool reference
│       ├── pyproject.toml
│       ├── Dockerfile
│       ├── src/eo_mcp/
│       │   ├── server.py        # FastMCP entrypoint
│       │   ├── config.py        # pydantic-settings, validated at startup
│       │   └── tools/{raster,vector,workflows}.py
│       ├── tests/
│       └── docs/                # live-probed TiTiler + tipg API notes
└── prompts/
    └── disaster/flood-extent.md
```

### Which document owns what

Overlap between these files was a real problem; this is the split that resolves it.

| Document | Owns | Never contains |
|---|---|---|
| `README.md` | The pitch, install, configuration table, demo backend summary | Tool signatures |
| `servers/eo-mcp/README.md` | **Canonical tool reference** — every signature and parameter | The project pitch |
| `ARCHITECTURE.md` | Design rationale, taxonomy, layout | Instructions to run anything |
| `docs/WORKED-EXAMPLE.md` | One real end-to-end analysis with real output | Invented numbers |
| `docs/SELF-HOSTING.md` | The backend contract, config reference | An eoAPI install guide |
| `CONTRIBUTING.md` | Dev workflow, exactly what CI enforces | Usage docs |
| `CLAUDE.md` | Agent-facing operating instructions | Anything a human reader needs |
| `servers/eo-mcp/docs/*.md` | Live-probed API divergences | Anything not verified against the API |

If a fact appears in two of these, one of them is wrong. Prefer a link.
