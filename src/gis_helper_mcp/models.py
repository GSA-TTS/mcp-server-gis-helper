"""
models.py
---------
Lightweight dataclasses for the GIS Helper MCP server.

These carry structured results between the validation utilities, the map-bridge
HTTP layer, and the MCP tool layer. No third-party GIS dependencies are needed —
everything is plain GeoJSON in WGS84 (EPSG:4326) decimal degrees.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# WGS84 lon/lat. This is what GPS coordinates are in, and what the downstream
# GIS servers (FEMA NFHL, USGS National Map) require. We NEVER silently assume a
# different CRS.
WGS84 = 4326

# GeoJSON geometry types this server produces and validates. These are exactly
# the types the FEMA/USGS servers accept, plus the shared "BoundingBox"
# shorthand, so output is guaranteed pasteable into their tools.
SUPPORTED_GEOMETRY_TYPES = (
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
)
BBOX_TYPE = "BoundingBox"

# How long a draw session is kept in memory before it is eligible for cleanup.
SESSION_TTL_SECONDS = 60 * 60  # 1 hour


@dataclass
class GeometrySummary:
    """A compact, model-friendly description of a geometry (no raw coords dump)."""

    geo_type: str
    crs_epsg: int = WGS84
    vertex_count: int = 0
    bbox: list[float] | None = None  # [minLon, minLat, maxLon, maxLat]
    approx_area_sqkm: float | None = None  # polygons only
    approx_length_km: float | None = None  # lines only
    part_count: int = 1  # rings / lines / points depending on type

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "geo_type": self.geo_type,
            "crs_epsg": self.crs_epsg,
            "vertex_count": self.vertex_count,
            "part_count": self.part_count,
        }
        if self.bbox is not None:
            out["bbox"] = self.bbox
        if self.approx_area_sqkm is not None:
            out["approx_area_sqkm"] = self.approx_area_sqkm
        if self.approx_length_km is not None:
            out["approx_length_km"] = self.approx_length_km
        return out


@dataclass
class DrawSession:
    """A single map-drawing session held in memory by the bridge server."""

    session_id: str
    created_utc: float = field(default_factory=time.time)
    updated_utc: float = field(default_factory=time.time)
    # The most recent geometry the user saved from the map, as a GeoJSON dict.
    # None until the user draws and clicks "Save".
    geometry: dict[str, Any] | None = None
    # Optional initial map focus supplied when the session was created.
    center_lat: float | None = None
    center_lon: float | None = None
    zoom: int | None = None

    def is_expired(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return (now - self.updated_utc) > SESSION_TTL_SECONDS
