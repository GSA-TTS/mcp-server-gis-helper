# mcp-server-gis-helper

An MCP server that lets a user **draw GIS geometry on an interactive map** and
returns clean, validated **GeoJSON** — ready to hand off to other GIS MCP servers
such as [FEMA NFHL flood screening](../../fema/mcp-server-fema-nfhl) or
[USGS National Map hydrography](../../usgs/mcp-server-usgs-nationalmap).

It solves the "how do I generate a complex shape and pass it to the geometry-based
tools?" problem: instead of hand-typing coordinates, the user sketches a polygon,
line, or point on a Leaflet map and gets back a compact GeoJSON string.

This server **does not query** flood/waterway services itself. It only produces and
validates geometry; the agent passes that geometry to the downstream tools.

## Tools

| Tool | Purpose |
| --- | --- |
| `gis_open_map` | Interactive entry point. Returns a Prefab MCP App with a link to a browser drawing map and a "Load drawn geometry" button. Optional `lat`/`lon`/`zoom` center the map. |
| `gis_get_drawn_geometry` | Retrieves the shape the user drew and saved on the map for a given `session_id`. Returns a compact GeoJSON string. |
| `gis_validate_geometry` | Validates/normalizes an arbitrary GeoJSON string you already have (no map needed). Closes polygon rings, checks WGS84 ranges, unwraps Feature/FeatureCollection. |

All geometry is **WGS84 decimal degrees in GeoJSON `[lon, lat]` order**. Supported
types: `Point`, `MultiPoint`, `LineString`, `MultiLineString`, `Polygon`,
`MultiPolygon`, plus the `BoundingBox` shorthand
(`{"type": "BoundingBox", "bbox": [minLon, minLat, maxLon, maxLat]}`) — exactly what
the FEMA/USGS tools accept.

## How it works (the map bridge)

Prefab's `Embed` renders a *sandboxed* iframe whose content cannot write back into
Prefab state, so the drawing map cannot live purely inside the MCP app. Instead a
small **companion HTTP "map bridge"** (Starlette) serves the Leaflet + Leaflet.draw
page:

1. `gis_open_map` creates a **draw session** and returns a Prefab app with a link to
   `<public_url>/map?session=<id>`.
2. The user opens that link in a browser, draws one shape, and clicks **Save**. The
   page `POST`s the GeoJSON to `<public_url>/session/<id>/geometry`, where it's
   validated and stored in an in-memory session store.
3. `gis_get_drawn_geometry` reads the stored geometry out of the shared store and
   returns it as a compact GeoJSON string.
4. The agent passes that string to `nfhl_screen_flood_zone`, `hydro_find_waterways`,
   etc.

### Transports (mirrors the sibling GIS servers)

- **stdio** (local MCP clients like Claude Desktop/Code): MCP runs over stdio and the
  map bridge is started on a `localhost` daemon thread (default `127.0.0.1:8765`).
- **HTTP** (deployed / containerized): when `PORT` (or `DATABRICKS_APP_PORT`) is set,
  MCP is served at `/mcp` and the bridge routes (`/map`, `/session/{id}/geometry`,
  `/health`) are mounted onto the **same** port.

## Configuration (all optional)

| Env var | Default | Purpose |
| --- | --- | --- |
| `GIS_HELPER_MAP_HOST` | `127.0.0.1` | Bridge bind host (stdio mode) |
| `GIS_HELPER_MAP_PORT` | `8765` | Bridge port (stdio mode) |
| `GIS_HELPER_PUBLIC_URL` | `http://<host>:<port>` | Origin the browser uses to reach the bridge (set behind a proxy/container) |
| `GIS_HELPER_TILE_URL` | OpenStreetMap | Leaflet basemap tile URL template |
| `GIS_HELPER_TILE_ATTRIBUTION` | OSM | Tile attribution string |
| `PORT` / `DATABRICKS_APP_PORT` | — | If set, selects HTTP transport for MCP |

Copy `.env.example` to `.env` for local overrides.

## Running

```bash
uv sync

# stdio (local MCP client)
uv run python main.py

# HTTP (deployed)
PORT=8080 uv run python main.py
```

## Example workflow

```
User:  I want to screen a project area for FEMA flood zones.
Agent: (calls gis_open_map centered on the site)
User:  (draws a polygon on the map, clicks Save, clicks "Load drawn geometry")
Agent: (gis_get_drawn_geometry -> geometry string)
Agent: (passes geometry to nfhl_screen_flood_zone)
```

If the user already has coordinates, skip the map and use `gis_validate_geometry`
to clean them up before querying downstream.

## Project layout

```
src/gis_helper_mcp/
├── app.py            # FastMCP init, instructions, transport, bridge startup
├── models.py         # dataclasses (DrawSession, GeometrySummary) + constants
├── utils.py          # GeoJSON validation/normalization, summaries, session store, config
├── bridge.py         # Starlette map-bridge routes + runners
├── static/
│   └── map.html      # Leaflet + Leaflet.draw drawing page (CDN assets)
└── tools/
    ├── __init__.py           # register_tools(mcp)
    ├── open_map.py           # gis_open_map (Prefab app)
    ├── get_drawn_geometry.py # gis_get_drawn_geometry
    └── validate_geometry.py  # gis_validate_geometry
```
