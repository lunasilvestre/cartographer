from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastmcp import FastMCP

from eo_mcp.config import Settings
from eo_mcp.http import Backends
from eo_mcp.tools import raster, vector, workflows


@pytest.fixture
def settings_fx() -> Settings:
    """Settings for unit tests: fake hosts, no retries, no sleeping.

    Retries are off by default so that a test asserting on call counts or
    feeding a list of canned responses is not surprised by an extra attempt.
    The tests that exercise the retry policy build their own Settings.
    """
    return Settings(
        raster_url="https://fake.example/raster",
        stac_url="https://fake.example",
        vector_url="https://fake.example/vector",
        auth_token="",
        max_retries=0,
        retry_backoff=0.0,
    )


@pytest.fixture
async def backends_fx(settings_fx: Settings):
    """Backend clients scoped to one test, closed on teardown.

    This replaces the autouse fixture that used to reset two module-level
    client singletons. The clients now belong to this object, so there is
    nothing global to reset.
    """
    backends = Backends(settings_fx)
    yield backends
    await backends.aclose()


@pytest.fixture
def mcp_fx(backends_fx: Backends) -> FastMCP:
    mcp = FastMCP("test-eo-mcp")
    raster.register(mcp, backends_fx)
    return mcp


@pytest.fixture
def mcp_full_fx(backends_fx: Backends) -> FastMCP:
    """Full MCP with raster + vector + workflow tools registered."""
    mcp = FastMCP("test-eo-mcp-full")
    raster.register(mcp, backends_fx)
    vector.register(mcp, backends_fx)
    workflows.register(mcp, backends_fx)
    return mcp


@pytest.fixture
def mock_httpx_request():
    """Patch httpx.AsyncClient.request with an AsyncMock; caller sets return_value.

    The mock replaces the unbound method, so a side_effect router is called as
    ``(method, path, **kwargs)`` — no ``self``, and ``path`` is relative to the
    client's base URL.
    """
    mock = AsyncMock()
    # Default: a benign 200 response so accidental calls don't crash unrelated tests.
    default_resp = MagicMock()
    default_resp.status_code = 200
    default_resp.json.return_value = {}
    default_resp.text = ""
    default_resp.url = "https://fake.example/raster/"
    default_resp.headers = {}
    mock.return_value = default_resp
    with patch("httpx.AsyncClient.request", new=mock):
        yield mock


def make_response(request: httpx.Request, status: int, body=None, text: str | None = None,
                  headers: dict[str, str] | None = None) -> httpx.Response:
    """A real httpx.Response, so .text/.json()/.headers behave as in production."""
    if text is not None:
        return httpx.Response(status, text=text, headers=headers, request=request)
    return httpx.Response(status, json=body if body is not None else {}, headers=headers, request=request)


@pytest.fixture
def mock_httpx_send():
    """Patch httpx.AsyncClient.send, routing on the absolute request URL.

    The workflow tools talk to all three backends and the paths collide —
    ``/collections`` exists on both STAC and tipg — so a router keyed on the
    relative path cannot tell them apart. Patching one layer lower gives the
    router the full URL, and leaves the retry and timeout logic under test
    rather than mocked away.

    A side_effect router is called as ``(request, **kwargs)`` and should return
    a response built with :func:`make_response`, or raise an httpx exception.
    """
    mock = AsyncMock()
    mock.side_effect = lambda request, **kw: make_response(request, 200, {})
    with patch("httpx.AsyncClient.send", new=mock):
        yield mock
