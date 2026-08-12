"""
validate_geometry tool
-----------------------
Registers the gis_validate_geometry tool on the MCP server.

Validates and normalizes an arbitrary GeoJSON string the caller already has
(e.g. pasted coordinates, output from another tool), and returns a clean,
compact GeoJSON string ready to pass to the FEMA NFHL or USGS National Map MCP
servers. This is the non-interactive path — no map required.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ..models import WGS84
from ..utils import (
    GeometryError,
    normalize_geojson,
    parse_geojson,
    summarize,
    to_compact_json,
)


def register(mcp: FastMCP) -> None:
    """Register the validate_geometry tool with the MCP server."""

    @mcp.tool(
        name="gis_validate_geometry",
        annotations={
            "title": "Validate & Normalize GeoJSON Geometry",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def validate_geometry(
        geometry: Annotated[str, Field(
            description=(
                "GeoJSON geometry as a JSON string. Supported types: Point, MultiPoint, "
                "LineString, MultiLineString, Polygon, MultiPolygon. Also accepts a "
                'BoundingBox shorthand: {"type": "BoundingBox", "bbox": [minLon, minLat, '
                "maxLon, maxLat]}. A GeoJSON Feature or single-feature FeatureCollection is "
                "also accepted and unwrapped to a bare geometry. All coordinates must be "
                "WGS84 decimal degrees in [lon, lat] order."
            ),
            min_length=2,
        )],
    ) -> dict[str, Any]:
        """Validate and normalize a GeoJSON geometry string for use with GIS servers.

        Checks that the geometry is a supported type with valid WGS84 [lon, lat]
        coordinates, unwraps Feature/FeatureCollection wrappers, and closes any
        unclosed polygon rings. Returns a compact, single-line GeoJSON string that
        can be passed directly to the FEMA NFHL (nfhl_screen_flood_zone) or USGS
        National Map (hydro_find_waterways, etc.) tools.

        Use when: you already have coordinates/GeoJSON and want to confirm it's
            valid and correctly formatted before querying another GIS server.
        Don't use when: you want to draw a shape interactively (use gis_open_map).

        Returns on success:
        {
            "valid": true,
            "geometry": str,          # compact GeoJSON string, ready to paste downstream
            "summary": {              # geo_type, crs_epsg, vertex_count, part_count,
                ...                   # bbox [minLon,minLat,maxLon,maxLat], approx area/length
            }
        }

        Error responses:
        - {"valid": false, "error": "..."} — invalid JSON, unsupported type,
          out-of-range coordinate (e.g. swapped lat/lon), or malformed BoundingBox.
        """
        try:
            parsed = parse_geojson(geometry)
            normalized = normalize_geojson(parsed)
        except GeometryError as exc:
            return {"valid": False, "error": f"Error: {exc}"}

        return {
            "valid": True,
            "geometry": to_compact_json(normalized),
            "summary": summarize(normalized).to_dict(),
            "crs_epsg": WGS84,
        }
