# Cartographer

Spatial intelligence harness for cloud-native Earth Observation.

## What's here

- `servers/eo-mcp/` — MCP server for TiTiler raster analysis + tipg vector queries
- `skills/` — Claude skills for geo-visualization and workflows
- `prompts/` — Curated workflow recipes tagged by skill + complexity
- `docs/` — Setup guides and sidekick server install instructions

## MCP Servers

### eo-mcp (this repo)
- **Raster tools:** get_raster_info, get_tile_url, get_zonal_statistics
- **Vector tools:** list_vector_collections, get_vector_features, get_vector_feature, get_vector_tile_url, get_collection_queryables
- **Workflow tools:** flood_rainfall_analysis, multi_layer_summary, collection_health_check
- **Backends:** TiTiler at $EO_MCP_RASTER_URL, tipg at $EO_MCP_VECTOR_URL (defaults under https://api.cheias.pt)
- **Run:** `cd servers/eo-mcp && python -m eo_mcp.server`

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
pytest -m "not slow"        # unit tests only
pytest                       # all tests including live integration
ruff check src/ tests/
coverage run -m pytest && coverage report -m
```

## Backends

- STAC: https://api.cheias.pt
- Raster (TiTiler): https://api.cheias.pt/raster
- Collections: copernicus-dem-30, sentinel-2-l2a, emsr864-observed-event-a, soil-moisture-anomaly, spi-1, spi-3, spi-6, sm-composite, precipitation-anomaly
