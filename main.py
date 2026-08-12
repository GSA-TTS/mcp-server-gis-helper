"""Local dev entry point for the GIS Helper MCP server.

Delegates to the packaged app so `python main.py` and
`python -m gis_helper_mcp.app` behave identically.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from gis_helper_mcp.app import mcp  # noqa: E402
from gis_helper_mcp.bridge import bridge_routes, start_bridge_in_thread  # noqa: E402


def main() -> None:
    port_env = os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT")
    if port_env:
        app = mcp.http_app(path="/mcp")
        for route in bridge_routes():
            app.router.routes.append(route)
        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=int(port_env))
    else:
        start_bridge_in_thread()
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
