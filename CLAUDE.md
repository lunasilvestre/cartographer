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


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
