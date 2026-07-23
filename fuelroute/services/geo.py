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
