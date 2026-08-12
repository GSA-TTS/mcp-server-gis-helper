"""
get_drawn_geometry tool
------------------------
Registers the gis_get_drawn_geometry tool on the MCP server.

Reads the geometry a user drew and saved on the interactive map for a given
draw session (created by gis_open_map). Returns a compact, validated GeoJSON
string ready to pass to the FEMA NFHL / USGS National Map MCP servers.

This tool is callable both from the Prefab map app (via a CallTool button) and
directly by the agent, so the workflow works even without the UI.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastmcp import FastMCP
from pydantic import Field

from ..models import WGS84
from ..utils import SESSIONS, summarize, to_compact_json


def register(mcp: FastMCP) -> None:
    """Register the get_drawn_geometry tool with the MCP server."""

    @mcp.tool(
        name="gis_get_drawn_geometry",
        annotations={
            "title": "Get Geometry Drawn on the Map",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def get_drawn_geometry(
        session_id: Annotated[Optional[str], Field(
            description=(
                "The draw session id returned by gis_open_map. Optional: if omitted "
                "(or if you don't have the exact id), the most recently drawn-and-saved "
                "geometry is returned. Prefer passing the exact session_id from the most "
                "recent gis_open_map result when you have it. Do NOT invent a value like "
                "'default' — leave it unset instead."
            ),
        )] = None,
    ) -> dict[str, Any]:
        """Retrieve the geometry a user drew and saved on the interactive map.

        Call this after gis_open_map once the user has drawn a shape and clicked
        "Save" on the map page. Geometry stored by the map is already validated and
        normalized (WGS84, closed rings). The returned "geometry" string can be
        passed directly to nfhl_screen_flood_zone, hydro_find_waterways, and other
        geometry-accepting GIS tools.

        Use when: "I drew a shape, get it", or wired to the "Load drawn geometry"
            button inside the gis_open_map app.

        session_id handling:
        - If you know the exact id from the latest gis_open_map result, pass it.
        - If you don't have it (e.g. a later turn), OMIT session_id entirely and the
          most recently saved geometry is returned. Never pass a placeholder like
          "default" — that will fail.

        Returns when a shape has been saved:
        {
            "ready": true,
            "session_id": str,        # the resolved session id
            "geometry": str,          # compact GeoJSON string, ready to paste downstream
            "summary": { geo_type, crs_epsg, vertex_count, part_count, bbox, ... }
        }

        Returns when nothing has been saved yet:
        {
            "ready": false,
            "message": "No geometry saved yet ..."
        }

        Error responses:
        - {"ready": false, "error": "Unknown or expired session ..."} — bad session_id.
        """
        # Treat missing / placeholder ids as "use the latest saved geometry".
        placeholder = session_id in (None, "", "default", "latest", "null", "none")

        if placeholder:
            session = SESSIONS.latest_with_geometry()
            if session is None:
                return {
                    "ready": False,
                    "message": (
                        "No geometry has been saved yet in any session. Open the map link "
                        "from gis_open_map, draw a shape, and click Save — then try again."
                    ),
                }
        else:
            session = SESSIONS.get(session_id)
            if session is None:
                # Fall back to the latest saved geometry rather than hard-failing,
                # since the id may be stale from an earlier turn.
                fallback = SESSIONS.latest_with_geometry()
                if fallback is None:
                    return {
                        "ready": False,
                        "error": (
                            f"Error: unknown or expired draw session '{session_id}', and no "
                            "other saved geometry exists. Run gis_open_map to start a new session."
                        ),
                    }
                session = fallback

        if session.geometry is None:
            return {
                "ready": False,
                "session_id": session.session_id,
                "message": (
                    "No geometry saved yet for this session. Open the map link from "
                    "gis_open_map, draw a shape, and click Save — then try again."
                ),
            }

        return {
            "ready": True,
            "session_id": session.session_id,
            "geometry": to_compact_json(session.geometry),
            "summary": summarize(session.geometry).to_dict(),
            "crs_epsg": WGS84,
        }
