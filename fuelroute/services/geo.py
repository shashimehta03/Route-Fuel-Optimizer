"""Small vectorized geo helpers (numpy) used by the planner."""
from __future__ import annotations

import numpy as np

EARTH_RADIUS_MILES = 3958.7613


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in miles between two scalar points."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return float(2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a)))


def nearest_vertex_miles(
    station_lats: np.ndarray,
    station_lons: np.ndarray,
    route_lats: np.ndarray,
    route_lons: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """For each station, find the nearest route vertex.

    Returns ``(min_distance_miles, nearest_index)`` arrays, one entry per
    station. Computed with an equirectangular approximation, which is accurate
    at the few-mile scale of the on-route corridor and lets us vectorize the
    whole station set against the whole route in one shot.
    """
    # Reference latitude for the local flat-earth projection.
    lat0 = np.radians(route_lats.mean())
    cos_lat0 = np.cos(lat0)

    # Project to a local planar frame in miles.
    def to_xy(lats, lons):
        x = np.radians(lons) * cos_lat0 * EARTH_RADIUS_MILES
        y = np.radians(lats) * EARTH_RADIUS_MILES
        return x, y

    rx, ry = to_xy(route_lats, route_lons)          # (V,)
    sx, sy = to_xy(station_lats, station_lons)      # (S,)

    # Distance from every station to every vertex: (S, V).
    dx = sx[:, None] - rx[None, :]
    dy = sy[:, None] - ry[None, :]
    d2 = dx * dx + dy * dy

    nearest_idx = np.argmin(d2, axis=1)
    min_dist = np.sqrt(d2[np.arange(d2.shape[0]), nearest_idx])
    return min_dist, nearest_idx


def project_to_route(
    station_lat: float,
    station_lon: float,
    route_lats: np.ndarray,
    route_lons: np.ndarray,
    cumulative_miles: np.ndarray,
) -> tuple[float, float]:
    """Project a station onto the route polyline.

    Returns ``(distance_from_route_miles, distance_along_route_miles)`` using an
    exact point-to-segment distance, so it stays accurate even when the route
    geometry is coarsely sampled (unlike nearest-vertex).
    """
    lat0 = np.radians(route_lats.mean())
    cos_lat0 = np.cos(lat0)

    def to_xy(lats, lons):
        x = np.radians(lons) * cos_lat0 * EARTH_RADIUS_MILES
        y = np.radians(lats) * EARTH_RADIUS_MILES
        return x, y

    rx, ry = to_xy(route_lats, route_lons)                  # (V,)
    px, py = to_xy(np.array([station_lat]), np.array([station_lon]))
    px, py = float(px[0]), float(py[0])

    ax, ay = rx[:-1], ry[:-1]                               # segment starts
    bx, by = rx[1:], ry[1:]                                 # segment ends
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    seg_len2 = np.where(seg_len2 == 0, 1e-12, seg_len2)

    t = ((px - ax) * dx + (py - ay) * dy) / seg_len2
    t = np.clip(t, 0.0, 1.0)
    cxp = ax + t * dx
    cyp = ay + t * dy
    dist = np.sqrt((px - cxp) ** 2 + (py - cyp) ** 2)       # (V-1,)

    k = int(np.argmin(dist))
    cum = cumulative_miles
    mile = cum[k] + t[k] * (cum[k + 1] - cum[k])
    return float(dist[k]), float(mile)
