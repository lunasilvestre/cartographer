"""Server construction: the tool surface, and what happens on a bad environment."""
from __future__ import annotations

import pytest

from eo_mcp.config import ConfigurationError, Settings
from eo_mcp.server import build_server, main

# The contract. CI asserts the same set from a clean install; this catches a
# rename before it ever reaches CI.
EXPECTED_TOOLS = {
    "get_raster_info",
    "get_tile_url",
    "get_zonal_statistics",
    "list_vector_collections",
    "get_vector_features",
    "get_vector_feature",
    "get_vector_tile_url",
    "get_collection_queryables",
    "flood_rainfall_analysis",
    "multi_layer_summary",
    "collection_health_check",
}


async def test_server_exposes_exactly_the_eleven_documented_tools():
    mcp = build_server(Settings(raster_url="https://fake.example/raster"))

    tools = await mcp.list_tools()

    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert len(EXPECTED_TOOLS) == 11


async def test_every_tool_has_a_description():
    """The docstring is the LLM-facing contract; an undocumented tool is unusable."""
    mcp = build_server(Settings())

    for tool in await mcp.list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 40, f"{tool.name} has a stub description"


async def test_instructions_name_the_configured_backend():
    mcp = build_server(Settings(raster_url="https://elsewhere.test/raster"))

    assert "https://elsewhere.test/raster" in (mcp.instructions or "")


async def test_build_server_reads_the_environment_when_given_no_settings(monkeypatch):
    monkeypatch.setenv("EO_MCP_RASTER_URL", "https://from-env.test/raster")

    mcp = build_server()

    assert "https://from-env.test/raster" in (mcp.instructions or "")


def test_build_server_rejects_a_bad_environment(monkeypatch):
    monkeypatch.setenv("EO_MCP_VECTOR_URL", "ftp://nope")

    with pytest.raises(ConfigurationError, match="EO_MCP_VECTOR_URL"):
        build_server()


def test_main_exits_with_a_readable_message_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setenv("EO_MCP_RASTER_URL", "")

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "eo-mcp configuration is invalid" in stderr
    assert "EO_MCP_RASTER_URL" in stderr
    assert "Traceback" not in stderr


async def test_server_lifespan_closes_the_backend_clients(monkeypatch):
    """Proof that the clients are tied to the server, not to module import."""
    import eo_mcp.http as http_module

    created: list = []
    original = http_module.Backends.__init__

    def _spy(self, settings):
        original(self, settings)
        created.append(self)

    monkeypatch.setattr(http_module.Backends, "__init__", _spy)

    mcp = build_server(Settings())
    backends = created[-1]
    clients = [await b.client() for b in backends.all()]

    async with mcp._lifespan_manager():
        assert not any(c.is_closed for c in clients)

    assert all(c.is_closed for c in clients)


def test_main_runs_the_server(monkeypatch):
    from unittest.mock import MagicMock

    from fastmcp import FastMCP as _FastMCP

    monkeypatch.setenv("EO_MCP_RASTER_URL", "https://fake.example/raster")
    run = MagicMock()
    monkeypatch.setattr(_FastMCP, "run", run)

    main()

    run.assert_called_once()


async def test_serverinfo_reports_the_package_version_not_fastmcp_s():
    """FastMCP defaults `version` to its own, so clients were shown fastmcp's.

    That number changes when an unrelated dependency is bumped — observed as
    3.2.4 from the lockfile and 3.4.5 from a fresh resolve, for a package that
    is 0.1.0.
    """
    from importlib.metadata import version as dist_version

    import fastmcp

    mcp = build_server(Settings())

    assert mcp.version == dist_version("eo-mcp")
    assert mcp.version != fastmcp.__version__


async def test_docstrings_do_not_promise_a_total():
    """No tool may advertise a `total` field, because the response has none.

    Distinguishes a claim from a warning rather than matching fixed phrases:
    every mention of "total" outside the item id must sit in a sentence that
    negates it. A future docstring saying "returns the total rainfall" fails
    here even though it shares no wording with the original mistake.

    Lives in the unit suite deliberately. It reads tool descriptions off a
    built server and needs no backend, and it was briefly stranded in the
    integration suite — where it was collected, passed locally on a full run,
    and never once executed in CI. A guard that cannot fail the build is
    decoration.
    """
    import re

    mcp = build_server(Settings())

    offenders = []
    for tool in await mcp.list_tools():
        # Collapse the docstring's own line wrapping first: splitting on
        # newlines would cut sentences in half and hide the negation from the
        # half that mentions "total".
        text = " ".join((tool.description or "").split())
        text = text.replace("precipitation-storm-total", "")
        for sentence in re.split(r"(?<=[.!?:])\s+", text):
            if not re.search(r"\btotals?\b", sentence, re.I):
                continue
            if not re.search(r"\b(no|not|never|cannot|does not)\b", sentence, re.I):
                offenders.append((tool.name, " ".join(sentence.split())))

    assert not offenders, f"tool descriptions assert a total: {offenders}"
