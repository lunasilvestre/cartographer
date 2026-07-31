FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/lunasilvestre/cartographer"
LABEL org.opencontainers.image.description="MCP server for cloud-native EO raster analysis (TiTiler), vector queries (tipg), and geospatial workflows"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

RUN pip install --no-cache-dir .

# Runs the MCP server over stdio. For an MCP client, mount this image as the
# command rather than running it as a long-lived service.
CMD ["eo-mcp"]
