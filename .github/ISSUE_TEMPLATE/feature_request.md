---
name: Feature request
about: Suggest a capability or an improvement
title: ''
labels: enhancement
assignees: ''
---

## What are you trying to do?

<!-- The question you want an agent to be able to answer, or the workflow you
     want to run. Describing the goal rather than the implementation usually
     leads somewhere better. -->

## What is missing today

<!-- What you tried, and where it stopped. -->

## Before you file — two constraints worth knowing

**The tool set is fixed at eleven tools.** The names are asserted in CI and
quoted across several documents, so adding one is a deliberate decision rather
than a routine change. Adding or extending a *parameter* on an existing tool is
much easier, and often does the same job. If your idea fits an existing tool,
say which.

**The server performs no geospatial computation.** It is a proxy: it builds
URLs, validates parameters and forwards requests. Anything needing `numpy`,
`rasterio` or `shapely` belongs in a backend or in a different MCP server such
as [gdal-mcp](../../docs/SIDEKICKS.md). See [ARCHITECTURE.md](../../ARCHITECTURE.md).

Neither is a rejection — they just shape what a good proposal looks like.

- [ ] This can be done by adding or extending a parameter on an existing tool
- [ ] This needs a new tool
- [ ] This is a documentation change
- [ ] Not sure

## Anything else

<!-- Relevant API endpoints, links to backend documentation, prior art in other
     MCP servers. -->
