"""Route generation orchestration.

This is a thin wrapper around the *existing, unchanged* route generation:

* :mod:`fuelroute.services.geocoding` resolves the start / finish strings, and
* :mod:`fuelroute.services.osrm` makes the **single** routing API call.

Keeping this in one place means the fuel-optimization layer depends on a small,
stable interface and never has to know how the route was produced. No routing
call is added here — there is still exactly one OSRM request per plan.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import geocoding, osrm


@dataclass
class RouteResult:
    start: geocoding.GeoPoint
    finish: geocoding.GeoPoint
    route: osrm.Route            # coordinates + cumulative_miles + totals


def generate_route(start_query: str, finish_query: str) -> RouteResult:
    """Geocode the endpoints and fetch the driving route (one OSRM call)."""
    start = geocoding.geocode(start_query)
    finish = geocoding.geocode(finish_query)
    route = osrm.get_route(start.as_lonlat(), finish.as_lonlat())
    if not route.coordinates:
        raise osrm.RoutingError("Routing provider returned an empty route.")
    return RouteResult(start=start, finish=finish, route=route)
