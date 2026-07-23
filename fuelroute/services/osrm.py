"""Thin client around the OSRM public routing API.

We make **exactly one** HTTP call per route request. That single call returns
everything the planner needs:

* the full route geometry (GeoJSON ``LineString`` coordinates), and
* per-segment distances (``annotation=distance``) so we can compute the
  cumulative mileage of every vertex along the route without any further
  calls.

OSRM's public demo server (``router.project-osrm.org``) requires no API key.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings

METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    """Raised when the routing provider fails or returns no route."""


@dataclass
class Route:
    # [(lon, lat), ...] geometry vertices, in order from start to finish.
    coordinates: list[tuple[float, float]]
    # cumulative_miles[i] = distance along the route to coordinates[i].
    cumulative_miles: list[float]
    total_miles: float
    duration_seconds: float


def get_route(start_lonlat: tuple[float, float], finish_lonlat: tuple[float, float]) -> Route:
    cfg = settings.FUEL_ROUTE
    coords = f"{start_lonlat[0]},{start_lonlat[1]};{finish_lonlat[0]},{finish_lonlat[1]}"
    url = f"{cfg['OSRM_BASE_URL']}/route/v1/driving/{coords}"
    try:
        resp = requests.get(
            url,
            params={
                "overview": "full",
                "geometries": "geojson",
                "annotations": "distance",
            },
            headers={"User-Agent": cfg["USER_AGENT"]},
            timeout=cfg["HTTP_TIMEOUT_SECONDS"],
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise RoutingError(f"Routing provider error: {exc}") from exc

    return parse_osrm_response(payload)


def parse_osrm_response(payload: dict) -> Route:
    """Turn a raw OSRM JSON payload into a :class:`Route`.

    Kept separate from the HTTP call so it can be unit-tested against a
    recorded fixture with no network access.
    """
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError(f"No route found (OSRM code={payload.get('code')!r}).")

    route = payload["routes"][0]
    geometry = route["geometry"]["coordinates"]  # [[lon, lat], ...]
    coordinates = [(float(lon), float(lat)) for lon, lat in geometry]

    # Concatenate per-leg segment distances (meters). For a 2-waypoint route
    # there is a single leg, but we handle N legs defensively.
    segment_meters: list[float] = []
    for leg in route.get("legs", []):
        segment_meters.extend(leg.get("annotation", {}).get("distance", []) or [])

    cumulative_miles = _cumulative_miles(coordinates, segment_meters)

    total_miles = route.get("distance", 0.0) / METERS_PER_MILE
    # Prefer the summed geometry distance if annotation was present (they agree
    # to rounding); fall back to the route-level distance otherwise.
    if cumulative_miles:
        total_miles = max(total_miles, cumulative_miles[-1])

    return Route(
        coordinates=coordinates,
        cumulative_miles=cumulative_miles,
        total_miles=round(total_miles, 3),
        duration_seconds=route.get("duration", 0.0),
    )


def _cumulative_miles(coordinates, segment_meters) -> list[float]:
    n = len(coordinates)
    cumulative = [0.0] * n
    if len(segment_meters) >= n - 1 and n > 1:
        acc = 0.0
        for i in range(1, n):
            acc += segment_meters[i - 1] / METERS_PER_MILE
            cumulative[i] = acc
    elif n > 1:
        # No annotation data: fall back to great-circle distance between
        # consecutive vertices so the planner still has mileage to work with.
        from .geo import haversine_miles

        acc = 0.0
        for i in range(1, n):
            lon0, lat0 = coordinates[i - 1]
            lon1, lat1 = coordinates[i]
            acc += haversine_miles(lat0, lon0, lat1, lon1)
            cumulative[i] = acc
    return cumulative
