# eo-mcp

## What it is

`eo-mcp` is a thin MCP server that proxies TiTiler raster-analysis endpoints as LLM-callable tools. It does zero raster processing — all compute runs server-side on TiTiler. It complements `stac-mcp` (STAC discovery): use stac-mcp to find items, then eo-mcp to analyze them.

## Tools

- **`get_raster_info`** — Returns band count, data type, CRS, bounds, nodata value, and pixel dimensions for a raster asset.
- **`get_tile_url`** — Generates a TileJSON URL and tile URL template for rendering a raster asset on a map, with optional color formula, colormap, rescale, and band selection.
- **`get_zonal_statistics`** — Computes raster statistics (mean, min, max, std, count, histogram) for a GeoJSON polygon AOI. Supports categorical (land-cover) rasters.

## Install

```bash
cd servers/eo-mcp
uv venv --python 3.13
uv pip install -e '.[dev]'
```

## Run

```bash
uv run python -m eo_mcp.server
```

Environment variables (all optional, defaults shown):

| Variable | Default | Description |
|---|---|---|
| `EO_MCP_RASTER_URL` | `https://api.cheias.pt/raster` | TiTiler base URL |
| `EO_MCP_STAC_URL` | `https://api.cheias.pt` | STAC API base URL |
| `EO_MCP_AUTH_TOKEN` | _(empty)_ | Bearer token for authenticated backends |

## MCP client config

Add to `claude_desktop_config.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "eo-mcp": {
      "command": "python",
      "args": ["-m", "eo_mcp.server"],
      "env": {
        "EO_MCP_RASTER_URL": "https://api.cheias.pt/raster",
        "EO_MCP_STAC_URL": "https://api.cheias.pt",
        "EO_MCP_AUTH_TOKEN": ""
      }
    }
  }
}
```

## Testing

```bash
# Unit tests only (no network)
uv run pytest -m "not slow"

# All tests including live integration against cheias.pt
uv run pytest

# Lint
uv run ruff check src/ tests/

# Coverage
uv run coverage run -m pytest -m "not slow" && uv run coverage report -m
```

## Backends

- STAC API: https://api.cheias.pt
- Raster (TiTiler): https://api.cheias.pt/raster

### Collections

Raster collections (asset key `data` on all):
`precipitation-daily`, `soil-moisture-daily`, `ivt`, `mslp`, `precondition`, `satellite-ir`, `sst-anomaly`, `wind-u`

Vector-only collections (no raster assets):
`flood-extent-emsr861`, `flood-extent-emsr864`
