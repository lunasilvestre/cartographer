from fastmcp import FastMCP

from .config import Settings
from .tools import raster, vector, workflows


def build_server() -> FastMCP:
    settings = Settings()
    mcp = FastMCP(
        "eo-mcp",
        instructions=(
            "Raster analysis, vector queries, and geospatial workflows for "
            "cloud-native Earth Observation. Complements stac-mcp for discovery. "
            "Backend: TiTiler at " + settings.raster_url + "."
        ),
    )
    raster.register(mcp, settings)
    vector.register(mcp, settings)
    workflows.register(mcp, settings)
    return mcp


def main() -> None:
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
