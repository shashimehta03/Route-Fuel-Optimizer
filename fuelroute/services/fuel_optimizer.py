"""Fuel-stop optimization.

Strategy (exactly as specified by the assignment):

1. **Segment** the route into 500-mile legs and place a refuel checkpoint at
   every 500-mile mark (500, 1000, ...) before the destination.
2. For each checkpoint, **find stations within a configurable radius** (10-20
   miles) of that point on the route — queried only from the local DB (held in
   an in-memory NumPy cache).
3. **Select the cheapest** candidate. Ties are broken by the smallest distance
   from the route.

All station lookups are vectorized against the cached station set, so no
per-checkpoint database round-trips and no external calls are made.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

import numpy as np
from django.conf import settings

from . import stations
from .geo import EARTH_RADIUS_MILES, project_to_route
from .osrm import Route


@dataclass
class FuelStop:
    stop_number: int
    checkpoint_mile: float          # the 500-mile checkpoint this stop serves
    station: dict                   # cached station meta
    distance_from_start: float      # station's position along the route (mi)
    distance_from_route: float      # station's straight-line offset from route
    fuel_price: float
    selected_reason: str


def refuel_checkpoints(total_distance: float, max_range: float) -> list[float]:
    """Mile markers where a refuel is required: 500, 1000, ... < total."""
    checkpoints = []
    mile = max_range
    while mile < total_distance:
        checkpoints.append(round(mile, 2))
        mile += max_range
    return checkpoints


def point_at_mile(route: Route, mile: float) -> tuple[float, float]:
    """Interpolate the (lat, lon) of a point ``mile`` miles along the route."""
    cum = route.cumulative_miles
    coords = route.coordinates
    idx = bisect.bisect_left(cum, mile)
    if idx <= 0:
        lon, lat = coords[0]
        return lat, lon
    if idx >= len(cum):
        lon, lat = coords[-1]
        return lat, lon
    lo, hi = cum[idx - 1], cum[idx]
    frac = 0.0 if hi == lo else (mile - lo) / (hi - lo)
    lon0, lat0 = coords[idx - 1]
    lon1, lat1 = coords[idx]
    return lat0 + frac * (lat1 - lat0), lon0 + frac * (lon1 - lon0)


def _haversine_to_all(lat0, lon0, lats, lons) -> np.ndarray:
    """Vectorized great-circle distance (miles) from one point to many."""
    lat0r, lon0r = np.radians(lat0), np.radians(lon0)
    latr, lonr = np.radians(lats), np.radians(lons)
    dlat = latr - lat0r
    dlon = lonr - lon0r
    a = np.sin(dlat / 2) ** 2 + np.cos(lat0r) * np.cos(latr) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def optimize_fuel_stops(route: Route) -> list[FuelStop]:
    cfg = settings.FUEL_ROUTE
    max_range = cfg["VEHICLE_RANGE_MILES"]
    radius = cfg["SEARCH_RADIUS_MILES"]
    max_radius = cfg["MAX_SEARCH_RADIUS_MILES"]

    store = stations.get_store()
    checkpoints = refuel_checkpoints(route.total_miles, max_range)
    if not checkpoints or len(store) == 0:
        return []

    # Route geometry as arrays, for computing a station's offset from the route
    # and its position (mile) along it.
    route_lons = np.array([c[0] for c in route.coordinates])
    route_lats = np.array([c[1] for c in route.coordinates])
    cum = np.array(route.cumulative_miles)

    stops: list[FuelStop] = []
    for i, mile in enumerate(checkpoints, start=1):
        cp_lat, cp_lon = point_at_mile(route, mile)

        # Distance from the checkpoint to every station (vectorized).
        cp_dist = _haversine_to_all(cp_lat, cp_lon, store.lats, store.lons)

        # Grow the radius only if nothing is found within the configured one.
        used_radius = radius
        candidates = np.nonzero(cp_dist <= used_radius)[0]
        while candidates.size == 0 and used_radius < max_radius:
            used_radius = min(used_radius * 2, max_radius)
            candidates = np.nonzero(cp_dist <= used_radius)[0]

        # If still nothing within the max radius, fall back to the single
        # nearest station. Every checkpoint MUST yield a stop, otherwise a
        # 500-mile leg would go unfuelled and the trip's fuel accounting
        # (initial + purchased == required) would break.
        used_fallback = candidates.size == 0
        if used_fallback:
            candidates = np.array([int(np.argmin(cp_dist))])

        # Cheapest price; tie -> closest to the route line.
        cand_prices = store.prices[candidates]
        min_price = cand_prices.min()
        tied = candidates[np.isclose(cand_prices, min_price)]

        # Offset from the route + position along it for the tied candidates,
        # using exact point-to-segment projection. Tie -> closest to route.
        best_idx = None
        best_route_dist = None
        best_mile = None
        for si in tied:
            d_route, mile_along = project_to_route(
                store.lats[si], store.lons[si], route_lats, route_lons, cum
            )
            if best_route_dist is None or d_route < best_route_dist:
                best_route_dist = d_route
                best_idx = int(si)
                best_mile = mile_along

        reason = "Lowest fuel price within search radius"
        if used_fallback:
            reason = (
                "Nearest available station (no station within the "
                f"{max_radius:.0f}-mile search radius)"
            )
        elif used_radius > radius:
            reason = (
                f"Lowest fuel price within widened {used_radius:.0f}-mile radius "
                "(no station inside the default radius)"
            )

        stops.append(FuelStop(
            stop_number=i,
            checkpoint_mile=mile,
            station=store.meta[best_idx],
            distance_from_start=round(best_mile, 2),
            distance_from_route=round(best_route_dist, 2),
            fuel_price=round(float(store.prices[best_idx]), 4),
            selected_reason=reason,
        ))

    return stops
