"""Server construction and entry point."""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version

from fastmcp import FastMCP

from .config import ConfigurationError, Settings
from .http import Backends, backends_lifespan
from .tools import raster, vector, workflows


def _package_version() -> str:
    try:
        return version("eo-mcp")
    except PackageNotFoundError:  # pragma: no cover - only when run from a bare tree
        return "0.0.0+unknown"


def build_server(settings: Settings | None = None) -> FastMCP:
    """Build the MCP server and the backend clients it owns.

    The HTTP clients live on a :class:`~eo_mcp.http.Backends` instance created
    here and closed by the server's lifespan, so their lifetime is the server's
    rather than the module's. Nothing is shared between two servers built in the
    same process, and there is no first-caller-wins ambiguity about which
    settings a client was built from.
    """
    settings = settings or Settings.load()
    backends = Backends(settings)

    mcp = FastMCP(
        "eo-mcp",
        # Without this FastMCP reports *its own* version as eo-mcp's, so every
        # client's server list shows a number that changes when an unrelated
        # dependency is bumped. Read from the installed distribution so it can
        # never drift from pyproject.toml.
        version=_package_version(),
        instructions=(
            "Raster analysis, vector queries, and geospatial workflows for "
            "cloud-native Earth Observation. Complements stac-mcp for discovery. "
            "Backend: TiTiler at " + settings.raster_url + ". "
            "Tools raise an error on failure rather than returning an error "
            "object, so a result you receive is a real result."
        ),
        lifespan=backends_lifespan(backends),
    )

    raster.register(mcp, backends)
    vector.register(mcp, backends)
    workflows.register(mcp, backends)
    return mcp


def main() -> None:
    try:
        mcp = build_server()
    except ConfigurationError as exc:
        # A misconfigured environment is the most likely first-run failure. Say
        # what is wrong in one readable block instead of a pydantic traceback.
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    mcp.run()


if __name__ == "__main__":
    main()
