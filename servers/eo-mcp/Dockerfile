# Build stage: resolve and install into a self-contained virtualenv.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from uv.lock in their own layer, so editing src/ rebuilds
# only the project wheel rather than re-resolving the whole tree. --frozen fails
# the build if uv.lock has drifted from pyproject.toml instead of silently
# resolving something the lockfile never pinned.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-install-project --no-editable

COPY src/ src/
# --no-editable matters: uv installs the root project editable by default, which
# leaves a .pth pointing at /app/src. That path does not exist in the runtime
# stage (only the venv is copied over), so an editable install builds a green
# image that cannot import eo_mcp.
RUN uv sync --frozen --no-editable

# Runtime stage: just the interpreter and the venv — no uv, no build tooling.
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/lunasilvestre/cartographer"
LABEL org.opencontainers.image.description="MCP server for cloud-native EO raster analysis (TiTiler), vector queries (tipg), and geospatial workflows"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# The server is a pure stdio client of remote HTTP backends; it never needs to
# write to disk or bind a port, so it has no reason to run as root.
RUN useradd --create-home --uid 10001 app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

# Speaks MCP over stdio. An MCP client launches this image as its command
# (docker run --rm -i ...) rather than running it as a long-lived service —
# with no client attached to stdin the process correctly blocks waiting for a
# JSON-RPC handshake.
CMD ["eo-mcp"]
