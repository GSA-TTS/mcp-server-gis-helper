"""
open_map tool
-------------
Registers the gis_open_map tool on the MCP server.

This is the interactive entry point. It creates a draw session and returns a
Prefab MCP App: a shell with a link that opens the Leaflet drawing page (served
by the map bridge) in a browser tab, plus a "Load drawn geometry" button that
calls gis_get_drawn_geometry and displays the resulting GeoJSON in the app.

Why a browser link instead of an inline map?
    Prefab's Embed renders a sandboxed iframe whose content cannot write back
    into Prefab state, so a drawn shape could not be captured. The map is served
    separately by the bridge and POSTs its result back to the server keyed by the
    session id; gis_get_drawn_geometry then reads it out of the shared store.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastmcp import FastMCP
from prefab_ui.actions import OpenLink, SetState
from prefab_ui.actions.mcp import CallTool
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Alert,
    AlertDescription,
    AlertTitle,
    Button,
    Card,
    CardContent,
    CardHeader,
    CardTitle,
    Code,
    Column,
    Muted,
    Row,
    Text,
)
from prefab_ui.components.control_flow import If
from prefab_ui.rx import Rx
from pydantic import Field

from ..utils import SESSIONS, public_url


def register(mcp: FastMCP) -> None:
    """Register the open_map tool with the MCP server."""

    @mcp.tool(
        name="gis_open_map",
        app=True,
        annotations={
            "title": "Open Interactive Map to Draw Geometry",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,  # creates a new draw session each call
            "openWorldHint": True,
        },
    )
    def open_map(
        lat: Annotated[Optional[float], Field(
            description="Optional initial map center latitude (WGS84 decimal degrees).",
            ge=-90.0,
            le=90.0,
        )] = None,
        lon: Annotated[Optional[float], Field(
            description="Optional initial map center longitude (WGS84 decimal degrees).",
            ge=-180.0,
            le=180.0,
        )] = None,
        zoom: Annotated[Optional[int], Field(
            description="Optional initial map zoom level (0 = world, 19 = building).",
            ge=0,
            le=19,
        )] = None,
    ) -> PrefabApp:
        """Open an interactive map so the user can draw GIS geometry, then capture it.

        Returns a Prefab app with (1) a link that opens a Leaflet drawing page in the
        browser and (2) a "Load drawn geometry" button. Workflow:
          1. Call this tool (optionally center it with lat/lon/zoom).
          2. The user clicks "Open drawing map", draws a polygon/line/point, clicks Save.
          3. The user clicks "Load drawn geometry" (or you call gis_get_drawn_geometry
             with the session_id) to retrieve a compact GeoJSON string.
          4. Pass that GeoJSON to nfhl_screen_flood_zone, hydro_find_waterways, etc.

        Use when: "let me draw a shape / area on a map", "I want to sketch a polygon".
        Don't use when: the user already has coordinates (use gis_validate_geometry).

        The returned app also surfaces the session_id in text so the agent can call
        gis_get_drawn_geometry directly if the user prefers not to click the button.
        """
        session = SESSIONS.create(center_lat=lat, center_lon=lon, zoom=zoom)

        params = [f"session={session.session_id}"]
        if lat is not None:
            params.append(f"lat={lat}")
        if lon is not None:
            params.append(f"lon={lon}")
        if zoom is not None:
            params.append(f"zoom={zoom}")
        map_link = f"{public_url()}/map?" + "&".join(params)

        geometry = Rx("geometry")
        ready = Rx("ready")
        message = Rx("message")

        with PrefabApp(
            title="GIS Helper — Draw Geometry",
            state={"geometry": "", "ready": False, "message": ""},
            css_class="p-6 max-w-2xl",
        ) as app:
            with Card():
                with CardHeader():
                    CardTitle("Draw geometry on a map")
                with CardContent():
                    with Column(gap=4):
                        Text(
                            "Open the drawing map, sketch a polygon, rectangle, line, or "
                            "point, then click Save on the map. Come back here and load it."
                        )
                        with Row(gap=2):
                            Button(
                                "Open drawing map",
                                variant="default",
                                on_click=OpenLink(map_link),
                            )
                            Button(
                                "Load drawn geometry",
                                variant="outline",
                                on_click=CallTool(
                                    "gis_get_drawn_geometry",
                                    arguments={"session_id": session.session_id},
                                    on_success=[
                                        SetState("ready", "{{ $result.ready }}"),
                                        SetState("geometry", "{{ $result.geometry }}"),
                                        SetState("message", "{{ $result.message }}"),
                                    ],
                                ),
                            )
                        Muted(f"Session: {session.session_id}")

                        with If(ready):
                            with Alert(variant="success", icon="circle-check"):
                                AlertTitle("Geometry loaded")
                                AlertDescription(
                                    "Copy the GeoJSON below, or ask the assistant to "
                                    "screen it for flood zones or waterways."
                                )
                            Code(geometry)
                        with If("{{ !ready && message }}"):
                            Muted(message)

        return app
