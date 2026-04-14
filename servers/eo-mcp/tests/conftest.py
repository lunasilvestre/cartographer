from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastmcp import FastMCP

from eo_mcp.config import Settings
from eo_mcp.tools import raster, vector, workflows


@pytest.fixture
def settings_fx() -> Settings:
    return Settings(
        raster_url="https://fake.example/raster",
        stac_url="https://fake.example",
        vector_url="https://fake.example/vector",
        auth_token="",
    )


@pytest.fixture
def mcp_fx(settings_fx: Settings) -> FastMCP:
    mcp = FastMCP("test-eo-mcp")
    raster.register(mcp, settings_fx)
    return mcp


@pytest.fixture
def mcp_full_fx(settings_fx: Settings) -> FastMCP:
    """Full MCP with raster + vector + workflow tools registered."""
    mcp = FastMCP("test-eo-mcp-full")
    raster.register(mcp, settings_fx)
    vector.register(mcp, settings_fx)
    workflows.register(mcp, settings_fx)
    return mcp


@pytest.fixture(autouse=True)
def reset_raster_client():
    """Reset the lazy clients before and after each test."""
    raster._client = None
    vector._client = None
    yield
    raster._client = None
    vector._client = None


@pytest.fixture
def mock_httpx_request():
    """Patch httpx.AsyncClient.request with an AsyncMock; caller sets return_value."""
    mock = AsyncMock()
    # Default: a benign 200 response so accidental calls don't crash unrelated tests.
    default_resp = MagicMock()
    default_resp.status_code = 200
    default_resp.json.return_value = {}
    default_resp.text = ""
    default_resp.url = "https://fake.example/raster/"
    mock.return_value = default_resp
    with patch("httpx.AsyncClient.request", new=mock):
        yield mock
