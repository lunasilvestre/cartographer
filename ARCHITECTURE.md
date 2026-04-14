---
title: "Cartographer — Architecture"
category: project
status: active
created: 2026-04-14
last_touched: 2026-04-14
tags: [cartographer, eo-mcp, geo-viz, architecture, devseed]
---

# Cartographer Architecture

Spatial intelligence and storytelling harness. Focused on cloud-native EO analysis, visualization, and workflow composition. Separate from KAIROS (which is the `/workengine` harness on atlas).

## Core Taxonomy

| Type | What it is | Runtime? | Where it lives |
|------|-----------|----------|----------------|
| **MCP server** | Process that responds to tool calls via MCP protocol | Yes — needs a host | `servers/` |
| **Skill** | Static markdown that teaches Claude how to use tools | No — read at context load | `skills/` |
| **Prompt** | Curated workflow recipe that chains tools to answer a question | No — markdown template | `prompts/` |

### Rules
- A skill NEVER duplicates a tool. A tool NEVER contains rendering logic.
- If a capability needs runtime → it's a **tool** in an MCP server.
- If it's knowledge about how to use existing tools → it's a **skill**.
- If it's a worked example that chains tools → it's a **prompt**.

## Server Stack

| Server | Role | Compute | Deploy |
|--------|------|---------|--------|
| eo-mcp | Analyze (cloud raster/vector) | None (HTTP proxy to TiTiler/tipg) | Sliplane or atlas |
| stac-mcp (Wayfinder) | Discover | None (HTTP proxy to STAC API) | atlas (stdio) |
| gdal-mcp (JordanGunn) | Transform (local) | CPU-bound (rasterio/GDAL) | atlas only |
| duckdb-mcp | Query (analytical) | In-process DB | atlas only |

## Skill Stack

| Skill | Purpose | Status |
|-------|---------|--------|
| geo-viz | Teaches Claude to render eo-mcp tile URLs as MapLibre/deck.gl artifacts | Build after eo-mcp v0.1 |
| geo-auto | Data engineering workflow patterns (COG conversion, STAC publishing) | Future |
| geo-ml | Inference integration (Clay embeddings, classification) | Future |

## Prompt Tagging Convention

```yaml
---
title: Flood Extent Mapping
domain: disaster-response
servers_required: [eo-mcp]
servers_optional: [stac-mcp]
skills_required: [geo-viz]
complexity: basic    # basic (1-3 tools) | intermediate (4-6) | advanced (7+)
tool_calls: 3
demo_endpoint: api.cheias.pt/raster
---
```

## Repo Structure

```
cartographer/
├── README.md
├── CLAUDE.md
├── ARCHITECTURE.md
├── servers/
│   └── eo-mcp/
│       ├── pyproject.toml
│       ├── src/eo_mcp/
│       │   ├── server.py
│       │   ├── tools/raster.py
│       │   ├── tools/vector.py
│       │   ├── tools/workflows.py
│       │   └── config.py
│       ├── tests/
│       └── Dockerfile
├── skills/
│   ├── geo-viz/SKILL.md
│   ├── geo-auto/SKILL.md        # future
│   └── geo-ml/SKILL.md          # future
├── prompts/
│   ├── disaster/flood-extent.md
│   ├── environment/ndvi-anomaly.md
│   └── curation/collection-health.md
└── docs/
    ├── PREREQUISITES.md
    ├── SETUP-ATLAS.md
    ├── SETUP-SLIPLANE.md
    └── SIDEKICKS.md
```

## CLAUDE.md Scope

Defines for CC: available MCP servers + endpoints, available skills, prompt library location + tagging, taxonomy rules, default backend URLs (cheias.pt eoAPI).

