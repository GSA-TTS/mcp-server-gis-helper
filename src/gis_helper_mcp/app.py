import os

from dotenv import load_dotenv
from fastmcp import FastMCP

from gis_helper_mcp.bridge import bridge_routes, start_bridge_in_thread
from gis_helper_mcp.tools import register_tools

load_dotenv()  # loads .env before any code reads env vars

# Initialize FastMCP server
mcp = FastMCP(
    "gis_helper_mcp",
    instructions=(
        "Helps a user turn a hand-drawn map shape into a validated GeoJSON geometry "
        "that can be handed to other GIS MCP servers (e.g. FEMA NFHL flood screening "
        "via nfhl_screen_flood_zone, or USGS National Map hydrography via "
        "hydro_find_waterways). This server does NOT query those services itself — it "
        "only produces and validates geometry.\n\n"
        "TOOL SELECTION GUIDE:\n"
        "- User wants to draw a shape/area on a map -> gis_open_map (returns an "
        "interactive app with a link to a Leaflet drawing page + a load button)\n"
        "- Retrieve the shape the user drew and saved on the map -> gis_get_drawn_geometry "
        "(pass the exact session_id from the latest gis_open_map result if you have it; "
        "otherwise OMIT session_id and it returns the most recently saved geometry — never "
        "pass a placeholder like 'default')\n"
        "- User already has coordinates/GeoJSON to check or clean -> gis_validate_geometry\n\n"
        "WORKFLOW (interactive):\n"
        "1. Call gis_open_map. 2. User opens the map link, draws one shape, clicks Save. "
        "3. Call gis_get_drawn_geometry (with the session_id if known, else no arguments). "
        "4. Take the returned 'geometry' string and pass it as the 'geometry' "
        "argument to the target GIS tool.\n\n"
        "CONVENTIONS:\n"
        "- All coordinates are WGS84 decimal degrees in GeoJSON [lon, lat] order.\n"
        "- Supported geometry types: Point, MultiPoint, LineString, MultiLineString, "
        'Polygon, MultiPolygon, plus BoundingBox shorthand {"type": "BoundingBox", '
        '"bbox": [minLon, minLat, maxLon, maxLat]} — exactly what the FEMA/USGS tools accept.\n'
        "- Tool output 'geometry' is a compact single-line GeoJSON string, pasteable directly "
        "into geometry-accepting tools; polygon rings are auto-closed.\n"
        "- No authentication required. The interactive map is served by a companion HTTP "
        "'map bridge' on localhost (stdio mode) or the same port as MCP (HTTP mode).\n"
        "CONFIG: GIS_HELPER_MAP_PORT, GIS_HELPER_PUBLIC_URL, and GIS_HELPER_TILE_URL are optional."
    ),
)

# Register custom tools
register_tools(mcp)


if __name__ == "__main__":
    port_env = os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT")
    if port_env:
        # HTTP MCP mode: serve MCP and the map bridge from a single port by mounting
        # the bridge routes onto FastMCP's own Starlette app.
        app = mcp.http_app(path="/mcp")
        for route in bridge_routes():
            app.router.routes.append(route)
        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=int(port_env))
    else:
        # stdio MCP mode: MCP talks over stdio, so start the map bridge on a
        # localhost daemon thread for the browser drawing round-trip.
        start_bridge_in_thread()
        mcp.run(transport="stdio")
