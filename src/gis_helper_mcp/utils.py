"""
utils.py
--------
Shared utilities for the GIS Helper MCP server:

  - GeoJSON validation & normalization (WGS84, supported types, ring closing)
  - Geometry summarization (bbox, vertex count, approx area/length)
  - An in-memory draw-session store shared by the MCP tools and the map bridge
  - Config helpers for the companion HTTP "map bridge" (host/port/public URL/tiles)

Only the Python standard library is used for validation and geometry math, so
these functions stay dependency-free and unit-testable in isolation from MCP.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import threading
from typing import Any

from .models import (
    BBOX_TYPE,
    SESSION_TTL_SECONDS,
    SUPPORTED_GEOMETRY_TYPES,
    WGS84,
    DrawSession,
    GeometrySummary,
)

# Mean Earth radius in km, used for rough haversine length/area estimates. These
# are approximations for human context, NOT survey-grade measurements.
_EARTH_RADIUS_KM = 6371.0088


class GeometryError(ValueError):
    """Raised when a GeoJSON geometry is invalid or unsupported."""


# ---------------------------------------------------------------------------
# Configuration (map bridge)
# ---------------------------------------------------------------------------

def _http_mcp_port() -> int | None:
    """Return the MCP HTTP port if the server is in HTTP mode, else None.

    In HTTP mode (PORT / DATABRICKS_APP_PORT set) MCP and the map bridge share a
    single port, so that port is what the browser must use to reach /map.
    """
    port_env = os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT")
    if not port_env:
        return None
    try:
        return int(port_env)
    except ValueError:
        return None


def map_host() -> str:
    return os.getenv("GIS_HELPER_MAP_HOST", "127.0.0.1")


def map_port() -> int:
    """Port the map bridge listens on.

    In HTTP mode the bridge is mounted onto the MCP app, so it shares the MCP
    PORT. In stdio mode it runs standalone on GIS_HELPER_MAP_PORT (default 8765).
    """
    http_port = _http_mcp_port()
    if http_port is not None:
        return http_port
    try:
        return int(os.getenv("GIS_HELPER_MAP_PORT", "8765"))
    except ValueError:
        return 8765


def public_url() -> str:
    """Base origin the browser uses to reach the map bridge.

    Precedence:
      1. GIS_HELPER_PUBLIC_URL (explicit override, e.g. behind a proxy/container)
      2. HTTP mode: http://localhost:<PORT> (MCP + bridge share the port)
      3. stdio mode: http://<GIS_HELPER_MAP_HOST>:<GIS_HELPER_MAP_PORT>

    In HTTP mode the server binds 0.0.0.0, but the browser link uses "localhost"
    since 0.0.0.0 is not a routable address from a browser.
    """
    explicit = os.getenv("GIS_HELPER_PUBLIC_URL")
    if explicit:
        return explicit.rstrip("/")

    http_port = _http_mcp_port()
    if http_port is not None:
        return f"http://localhost:{http_port}"

    return f"http://{map_host()}:{map_port()}"


def tile_url() -> str:
    return os.getenv(
        "GIS_HELPER_TILE_URL",
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    )


def tile_attribution() -> str:
    return os.getenv(
        "GIS_HELPER_TILE_ATTRIBUTION",
        "&copy; OpenStreetMap contributors",
    )


# ---------------------------------------------------------------------------
# Session store (in-memory, thread-safe; shared with the bridge server)
# ---------------------------------------------------------------------------

class SessionStore:
    """Thread-safe in-memory store of DrawSession objects keyed by id.

    Both the MCP tools (which may run on the async event loop) and the map
    bridge's HTTP handlers touch this store, so access is guarded by a lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, DrawSession] = {}
        # Track the most recently *created* session so callers (and the model)
        # can retrieve "the geometry I just drew" without knowing the id.
        self._latest_id: str | None = None

    def create(
        self,
        center_lat: float | None = None,
        center_lon: float | None = None,
        zoom: int | None = None,
    ) -> DrawSession:
        self._cleanup_expired()
        session_id = secrets.token_urlsafe(9)
        session = DrawSession(
            session_id=session_id,
            center_lat=center_lat,
            center_lon=center_lon,
            zoom=zoom,
        )
        with self._lock:
            self._sessions[session_id] = session
            self._latest_id = session_id
        return session

    def get(self, session_id: str) -> DrawSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def latest(self) -> DrawSession | None:
        """Return the most recently created session, if any still exists."""
        with self._lock:
            if self._latest_id is None:
                return None
            return self._sessions.get(self._latest_id)

    def latest_with_geometry(self) -> DrawSession | None:
        """Return the most recently *updated* session that has a saved geometry.

        Falls back across sessions so a bare retrieval finds whatever the user
        most recently drew, even if a newer empty session was opened afterward.
        """
        with self._lock:
            candidates = [s for s in self._sessions.values() if s.geometry is not None]
            if not candidates:
                return None
            return max(candidates, key=lambda s: s.updated_utc)

    def set_geometry(self, session_id: str, geometry: dict[str, Any]) -> bool:
        import time

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.geometry = geometry
            session.updated_utc = time.time()
            return True

    def _cleanup_expired(self) -> None:
        import time

        now = time.time()
        with self._lock:
            expired = [
                sid for sid, s in self._sessions.items() if (now - s.updated_utc) > SESSION_TTL_SECONDS
            ]
            for sid in expired:
                del self._sessions[sid]


# A single process-wide store instance shared by app.py, the tools, and bridge.py.
SESSIONS = SessionStore()


# ---------------------------------------------------------------------------
# GeoJSON validation & normalization
# ---------------------------------------------------------------------------

def parse_geojson(raw: str) -> dict[str, Any]:
    """Parse a GeoJSON string into a dict, raising GeometryError on bad JSON."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeometryError(f"input is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise GeometryError("GeoJSON must be a JSON object with a 'type' field.")
    return obj


def _lonlat_in_range(lon: float, lat: float) -> bool:
    return -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0


def _check_position(pos: Any) -> tuple[float, float]:
    """Validate a single [lon, lat] position and return it as floats."""
    if not isinstance(pos, (list, tuple)) or len(pos) < 2:
        raise GeometryError(f"coordinate must be a [lon, lat] pair, got: {pos!r}")
    try:
        lon = float(pos[0])
        lat = float(pos[1])
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"coordinate values must be numbers, got: {pos!r}") from exc
    if not _lonlat_in_range(lon, lat):
        raise GeometryError(
            f"coordinate out of WGS84 range: lon={lon}, lat={lat} "
            "(expected lon in [-180, 180], lat in [-90, 90]). "
            "GeoJSON order is [lon, lat] — check for swapped values."
        )
    return lon, lat


def _iter_positions(coords: Any):
    """Yield every [lon,lat] position in an arbitrarily nested coordinate array."""
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and all(isinstance(c, (int, float)) for c in coords[:2])
    ):
        yield coords
        return
    if isinstance(coords, (list, tuple)):
        for item in coords:
            yield from _iter_positions(item)


def normalize_geojson(obj: dict[str, Any]) -> dict[str, Any]:
    """Validate a GeoJSON geometry and return a normalized copy.

    Accepts the geometry types the downstream GIS servers support
    (Point, MultiPoint, LineString, MultiLineString, Polygon, MultiPolygon)
    plus the shared BoundingBox shorthand:
        {"type": "BoundingBox", "bbox": [minLon, minLat, maxLon, maxLat]}

    Also accepts a GeoJSON Feature or FeatureCollection with a single geometry
    (as produced by Leaflet's toGeoJSON) and unwraps it to a bare geometry.

    Normalization performed:
      - unwraps Feature / single-geometry FeatureCollection
      - validates every coordinate is a numeric WGS84 [lon, lat]
      - closes unclosed polygon/multipolygon rings

    Raises GeometryError with an actionable message on any problem.
    """
    obj = _unwrap_feature(obj)
    geo_type = obj.get("type")
    if not geo_type:
        raise GeometryError("GeoJSON object is missing a 'type' field.")

    # ---- BoundingBox shorthand ---------------------------------------
    if geo_type == BBOX_TYPE:
        bbox = obj.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise GeometryError(
                "BoundingBox requires a 'bbox' array of [minLon, minLat, maxLon, maxLat]."
            )
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
        _check_position([min_lon, min_lat])
        _check_position([max_lon, max_lat])
        if min_lon > max_lon or min_lat > max_lat:
            raise GeometryError(
                "BoundingBox min values must be <= max values "
                "([minLon, minLat, maxLon, maxLat])."
            )
        return {"type": BBOX_TYPE, "bbox": [min_lon, min_lat, max_lon, max_lat]}

    if geo_type not in SUPPORTED_GEOMETRY_TYPES:
        raise GeometryError(
            f"unsupported geometry type '{geo_type}'. Supported: "
            f"{', '.join(SUPPORTED_GEOMETRY_TYPES)} or {BBOX_TYPE}."
        )

    coords = obj.get("coordinates")
    if coords is None:
        raise GeometryError(f"geometry of type '{geo_type}' is missing 'coordinates'.")

    # Validate every position is a numeric WGS84 pair.
    positions = list(_iter_positions(coords))
    if not positions:
        raise GeometryError(f"geometry of type '{geo_type}' has no coordinates.")
    for pos in positions:
        _check_position(pos)

    normalized_coords = _normalize_coords(geo_type, coords)
    return {"type": geo_type, "coordinates": normalized_coords}


def _unwrap_feature(obj: dict[str, Any]) -> dict[str, Any]:
    """Reduce a Feature / single-geometry FeatureCollection to a bare geometry."""
    geo_type = obj.get("type")
    if geo_type == "Feature":
        geom = obj.get("geometry")
        if not isinstance(geom, dict):
            raise GeometryError("Feature is missing a 'geometry' object.")
        return geom
    if geo_type == "FeatureCollection":
        features = obj.get("features")
        if not isinstance(features, list) or not features:
            raise GeometryError("FeatureCollection has no features.")
        if len(features) > 1:
            raise GeometryError(
                "FeatureCollection contains multiple features; draw or supply a single "
                "geometry (Point, LineString, or Polygon)."
            )
        geom = features[0].get("geometry")
        if not isinstance(geom, dict):
            raise GeometryError("FeatureCollection feature is missing a 'geometry' object.")
        return geom
    return obj


def _close_ring(ring: list[list[float]]) -> list[list[float]]:
    """Ensure a polygon ring's first and last positions are identical."""
    if len(ring) >= 1 and ring[0] != ring[-1]:
        return list(ring) + [list(ring[0])]
    return ring


def _normalize_coords(geo_type: str, coords: Any) -> Any:
    """Return coordinates with polygon rings closed; other types pass through."""
    if geo_type == "Polygon":
        return [_close_ring(ring) for ring in coords]
    if geo_type == "MultiPolygon":
        return [[_close_ring(ring) for ring in polygon] for polygon in coords]
    return coords


# ---------------------------------------------------------------------------
# Geometry summarization
# ---------------------------------------------------------------------------

def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _line_length_km(line: list[list[float]]) -> float:
    total = 0.0
    for i in range(len(line) - 1):
        total += _haversine_km(line[i][0], line[i][1], line[i + 1][0], line[i + 1][1])
    return total


def _ring_area_sqkm(ring: list[list[float]]) -> float:
    """Approximate spherical polygon-ring area in km^2 (unsigned)."""
    if len(ring) < 4:
        return 0.0
    total = 0.0
    for i in range(len(ring) - 1):
        lon1, lat1 = math.radians(ring[i][0]), math.radians(ring[i][1])
        lon2, lat2 = math.radians(ring[i + 1][0]), math.radians(ring[i + 1][1])
        total += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(total * _EARTH_RADIUS_KM * _EARTH_RADIUS_KM / 2.0)


def summarize(geometry: dict[str, Any]) -> GeometrySummary:
    """Compute a GeometrySummary for an already-normalized geometry."""
    geo_type = geometry["type"]

    if geo_type == BBOX_TYPE:
        min_lon, min_lat, max_lon, max_lat = geometry["bbox"]
        area = _ring_area_sqkm(
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]
        )
        return GeometrySummary(
            geo_type=BBOX_TYPE,
            vertex_count=4,
            bbox=[min_lon, min_lat, max_lon, max_lat],
            approx_area_sqkm=round(area, 4),
            part_count=1,
        )

    coords = geometry["coordinates"]
    positions = list(_iter_positions(coords))
    lons = [p[0] for p in positions]
    lats = [p[1] for p in positions]
    bbox = [min(lons), min(lats), max(lons), max(lats)]

    summary = GeometrySummary(
        geo_type=geo_type,
        vertex_count=len(positions),
        bbox=[round(v, 7) for v in bbox],
    )

    if geo_type == "LineString":
        summary.part_count = 1
        summary.approx_length_km = round(_line_length_km(coords), 4)
    elif geo_type == "MultiLineString":
        summary.part_count = len(coords)
        summary.approx_length_km = round(sum(_line_length_km(l) for l in coords), 4)
    elif geo_type == "Polygon":
        summary.part_count = len(coords)
        outer = _ring_area_sqkm(coords[0]) if coords else 0.0
        holes = sum(_ring_area_sqkm(r) for r in coords[1:])
        summary.approx_area_sqkm = round(max(outer - holes, 0.0), 4)
    elif geo_type == "MultiPolygon":
        summary.part_count = len(coords)
        area = 0.0
        for polygon in coords:
            outer = _ring_area_sqkm(polygon[0]) if polygon else 0.0
            holes = sum(_ring_area_sqkm(r) for r in polygon[1:])
            area += max(outer - holes, 0.0)
        summary.approx_area_sqkm = round(area, 4)
    elif geo_type == "MultiPoint":
        summary.part_count = len(positions)
    else:  # Point
        summary.part_count = 1

    return summary


def to_compact_json(geometry: dict[str, Any]) -> str:
    """Serialize a geometry to a compact single-line JSON string (no spaces)."""
    return json.dumps(geometry, separators=(",", ":"))
