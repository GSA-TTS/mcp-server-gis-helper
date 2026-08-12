"""
bridge.py
---------
The companion HTTP "map bridge" that powers the interactive drawing surface.

Prefab's Embed renders a *sandboxed* iframe whose content cannot write back into
Prefab client-side state. So the drawing map cannot live purely inside the MCP
app. Instead we serve a small Leaflet + Leaflet.draw page from this HTTP server;
the user draws in a browser tab and clicks "Save", which POSTs the resulting
GeoJSON back here keyed by a session id. The MCP tool `gis_get_drawn_geometry`
then reads the geometry out of the shared in-memory SessionStore.

Two ways this server is reached:

  - stdio MCP mode: MCP talks over stdio, so we start this Starlette app in a
    background daemon thread via `start_bridge_in_thread()` bound to localhost.
  - HTTP MCP mode: the same routes are mounted onto FastMCP's own Starlette app
    (see app.py) so a single port serves both MCP and the map bridge.
"""

from __future__ import annotations

import json
from importlib import resources

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from .utils import (
    SESSIONS,
    GeometryError,
    normalize_geojson,
    public_url,
    summarize,
    tile_attribution,
    tile_url,
)

_MAP_TEMPLATE_CACHE: str | None = None


def _load_map_template() -> str:
    """Load static/map.html once and cache it."""
    global _MAP_TEMPLATE_CACHE
    if _MAP_TEMPLATE_CACHE is None:
        _MAP_TEMPLATE_CACHE = (
            resources.files("gis_helper_mcp.static").joinpath("map.html").read_text(encoding="utf-8")
        )
    return _MAP_TEMPLATE_CACHE


def _render_map(session_id: str, center_lat: float | None, center_lon: float | None, zoom: int | None) -> str:
    """Fill the map template with per-session config injected as JSON."""
    config = {
        "sessionId": session_id,
        "postUrl": f"{public_url()}/session/{session_id}/geometry",
        "tileUrl": tile_url(),
        "tileAttribution": tile_attribution(),
        "centerLat": center_lat if center_lat is not None else 39.8283,
        "centerLon": center_lon if center_lon is not None else -98.5795,  # CONUS centroid
        "zoom": zoom if zoom is not None else (13 if center_lat is not None else 4),
    }
    return _load_map_template().replace(
        "__PREFAB_GIS_CONFIG__", json.dumps(config)
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "mcp-server-gis-helper"})


async def get_map(request: Request) -> HTMLResponse:
    """Serve the Leaflet drawing page for a session.

    Query params: session (required), and optional lat/lon/zoom overrides.
    If the session id is unknown, a fresh session is created on the fly so a
    directly-opened link still works.
    """
    session_id = request.query_params.get("session")
    if not session_id:
        return PlainTextResponse(
            "Missing 'session' query parameter. Open the map via the gis_open_map tool.",
            status_code=400,
        )
    session = SESSIONS.get(session_id)
    if session is None:
        return PlainTextResponse(
            "Unknown or expired draw session. Re-run gis_open_map to start a new one.",
            status_code=404,
        )

    def _float(name: str) -> float | None:
        val = request.query_params.get(name)
        try:
            return float(val) if val is not None else None
        except ValueError:
            return None

    center_lat = _float("lat") if _float("lat") is not None else session.center_lat
    center_lon = _float("lon") if _float("lon") is not None else session.center_lon
    zoom_raw = request.query_params.get("zoom")
    try:
        zoom = int(zoom_raw) if zoom_raw is not None else session.zoom
    except ValueError:
        zoom = session.zoom

    html = _render_map(session_id, center_lat, center_lon, zoom)
    return HTMLResponse(html)


async def post_geometry(request: Request) -> JSONResponse:
    """Receive drawn GeoJSON from the map page and store it on the session."""
    session_id = request.path_params["session_id"]
    if SESSIONS.get(session_id) is None:
        return JSONResponse(
            {"ok": False, "error": "Unknown or expired session."}, status_code=404
        )
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "Body must be JSON."}, status_code=400)

    geometry_in = payload.get("geometry", payload)
    try:
        normalized = normalize_geojson(geometry_in)
        summary = summarize(normalized)
    except GeometryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)

    SESSIONS.set_geometry(session_id, normalized)
    return JSONResponse({"ok": True, "summary": summary.to_dict()})


async def get_geometry(request: Request) -> JSONResponse:
    """Return the currently-stored geometry for a session (used for polling/debug)."""
    session_id = request.path_params["session_id"]
    session = SESSIONS.get(session_id)
    if session is None:
        return JSONResponse(
            {"ok": False, "error": "Unknown or expired session."}, status_code=404
        )
    if session.geometry is None:
        return JSONResponse({"ok": True, "geometry": None})
    return JSONResponse(
        {"ok": True, "geometry": session.geometry, "summary": summarize(session.geometry).to_dict()}
    )


# ---------------------------------------------------------------------------
# App assembly & runners
# ---------------------------------------------------------------------------

def bridge_routes() -> list[Route]:
    """Routes for the map bridge; mounted standalone or onto FastMCP's app."""
    return [
        Route("/health", health, methods=["GET"]),
        Route("/map", get_map, methods=["GET"]),
        Route("/session/{session_id}/geometry", post_geometry, methods=["POST"]),
        Route("/session/{session_id}/geometry", get_geometry, methods=["GET"]),
    ]


def build_bridge_app() -> Starlette:
    """Standalone Starlette app for stdio mode."""
    return Starlette(routes=bridge_routes())


def start_bridge_in_thread() -> None:
    """Start the map bridge on a localhost daemon thread (stdio MCP mode).

    Idempotent-ish: safe to call once at startup. Uses uvicorn programmatically.
    """
    import threading

    import uvicorn

    from .utils import map_host, map_port

    app = build_bridge_app()
    config = uvicorn.Config(app, host=map_host(), port=map_port(), log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="gis-helper-map-bridge", daemon=True)
    thread.start()
