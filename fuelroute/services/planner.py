"""Core route + fuel-optimization logic.

Public entry point: :func:`plan_trip(start, finish)`.

Pipeline (one OSRM call, zero station geocoding at request time):

1. Geocode the start / finish strings (offline when possible).
2. One OSRM call -> route geometry + cumulative mileage.
3. Match cached stations to the route corridor (vectorized numpy).
4. Choose cost-optimal fuel stops with the classic gas-station greedy.
5. Assemble the response: stops, total fuel cost, and route GeoJSON.

Fuel-cost model (documented in the README):
    The trip consumes ``total_miles / MPG`` gallons. We price every one of
    those gallons at the station where it is optimally bought, subject to the
    500-mile range between fill-ups. The origin is treated as a departure
    fill-up priced at the nearest station, so the reported figure is the true
    cost of all fuel the journey burns (not just incremental top-ups).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from django.conf import settings

from . import geocoding, osrm, stations
from .geo import nearest_vertex_miles


class PlanningError(Exception):
    """User-facing planning failure (bad input, infeasible route, ...)."""


@dataclass
class FuelPoint:
    """A place where fuel can be bought, positioned along the route."""
    route_mile: float
    price: float
    station: dict | None          # None only for the destination sentinel
    detour_miles: float = 0.0
    is_origin: bool = False
    is_destination: bool = False


@dataclass
class Stop:
    station: dict
    route_mile: float
    detour_miles: float
    price: float
    gallons: float
    cost: float


@dataclass
class TripPlan:
    start: dict
    finish: dict
    total_distance_miles: float
    duration_hours: float
    total_gallons: float
    total_fuel_cost: float
    fuel_stops: list[Stop] = field(default_factory=list)
    route_coordinates: list[tuple[float, float]] = field(default_factory=list)


def plan_trip(start_query: str, finish_query: str) -> TripPlan:
    cfg = settings.FUEL_ROUTE
    mpg = cfg["MILES_PER_GALLON"]
    tank_range = cfg["VEHICLE_RANGE_MILES"]

    # 1) Geocode inputs (offline where possible).
    start = geocoding.geocode(start_query)
    finish = geocoding.geocode(finish_query)

    # 2) Single routing call.
    route = osrm.get_route(start.as_lonlat(), finish.as_lonlat())
    if not route.coordinates:
        raise PlanningError("Routing provider returned an empty route.")

    # 3) Match stations to the route corridor.
    on_route = _match_stations(route, cfg["CORRIDOR_MILES"])

    # 4) Build fuel points (origin + on-route + destination sentinel).
    origin_price = _origin_price(start, on_route)
    points = _build_fuel_points(route.total_miles, on_route, origin_price)

    # 5) Optimize.
    stops, total_cost = _optimize(points, mpg=mpg, tank_range=tank_range)

    total_gallons = route.total_miles / mpg
    return TripPlan(
        start={"query": start_query, "label": start.label,
               "latitude": start.lat, "longitude": start.lon},
        finish={"query": finish_query, "label": finish.label,
                "latitude": finish.lat, "longitude": finish.lon},
        total_distance_miles=round(route.total_miles, 2),
        duration_hours=round(route.duration_seconds / 3600.0, 2),
        total_gallons=round(total_gallons, 2),
        total_fuel_cost=round(total_cost, 2),
        fuel_stops=stops,
        route_coordinates=route.coordinates,
    )


# --------------------------------------------------------------------------
# Step 3: station <-> route matching
# --------------------------------------------------------------------------
def _match_stations(route: osrm.Route, corridor_miles: float) -> list[FuelPoint]:
    store = stations.get_store()
    if len(store) == 0:
        return []

    route_lons = np.array([c[0] for c in route.coordinates])
    route_lats = np.array([c[1] for c in route.coordinates])
    cum = np.array(route.cumulative_miles)

    # Bounding-box pre-filter: only stations near the route's box are worth
    # the full distance computation. Margin ~ corridor converted to degrees.
    margin = corridor_miles / 69.0 + 0.05
    in_box = (
        (store.lats >= route_lats.min() - margin)
        & (store.lats <= route_lats.max() + margin)
        & (store.lons >= route_lons.min() - margin)
        & (store.lons <= route_lons.max() + margin)
    )
    idx = np.nonzero(in_box)[0]
    if idx.size == 0:
        return []

    dist, nearest = nearest_vertex_miles(
        store.lats[idx], store.lons[idx], route_lats, route_lons
    )
    keep = dist <= corridor_miles

    points: list[FuelPoint] = []
    for local_i, station_i in enumerate(idx):
        if not keep[local_i]:
            continue
        points.append(FuelPoint(
            route_mile=float(cum[nearest[local_i]]),
            price=store.prices[station_i],
            station=store.meta[station_i],
            detour_miles=round(float(dist[local_i]), 2),
        ))

    points.sort(key=lambda p: p.route_mile)
    return points


def _origin_price(start: geocoding.GeoPoint, on_route: list[FuelPoint]) -> float:
    """Price for the departure fill-up at the origin.

    Uses the nearest on-route station to the start; if none matched, falls
    back to the global-cheapest so a plan can still be produced.
    """
    if on_route:
        # on_route is sorted by route mile; the first is nearest the start.
        return on_route[0].price
    store = stations.get_store()
    if len(store):
        return float(store.prices.min())
    raise PlanningError("No fuel-station price data is loaded.")


def _build_fuel_points(total_miles, on_route, origin_price) -> list[FuelPoint]:
    points = [FuelPoint(route_mile=0.0, price=origin_price,
                        station=on_route[0].station if on_route else None,
                        detour_miles=0.0, is_origin=True)]
    for p in on_route:
        if p.route_mile <= 0.0:
            continue  # already represented by the origin fill-up
        points.append(p)
    points.append(FuelPoint(route_mile=float(total_miles), price=math.inf,
                            station=None, is_destination=True))
    return points


# --------------------------------------------------------------------------
# Step 5: cost-optimal fueling (classic gas-station greedy — provably optimal)
# --------------------------------------------------------------------------
def _optimize(points: list[FuelPoint], *, mpg: float, tank_range: float):
    """Return (stops, total_cost).

    Greedy rule at the current fuel point (price ``p``):
      * If a strictly-cheaper reachable point exists, buy just enough to reach
        the nearest such point.
      * Else if the destination is reachable, buy just enough to finish.
      * Else fill the tank and drive to the cheapest reachable point.
    Fuel is tracked in miles-of-range; a full tank == ``tank_range`` miles.
    """
    n = len(points)
    dest = n - 1
    tank = 0.0                 # miles of range currently in the tank
    total_cost = 0.0
    bought: dict[int, float] = {}   # point index -> gallons purchased

    def buy(idx, miles_of_fuel):
        nonlocal tank, total_cost
        gallons = miles_of_fuel / mpg
        total_cost += gallons * points[idx].price
        bought[idx] = bought.get(idx, 0.0) + gallons
        tank += miles_of_fuel

    i = 0
    guard = 0
    while i != dest:
        guard += 1
        if guard > n + 5:
            raise PlanningError("Planner failed to converge (internal error).")

        here = points[i].route_mile
        # Reachable points ahead within the current tank range.
        reachable = [
            j for j in range(i + 1, n)
            if points[j].route_mile - here <= tank_range + 1e-6
        ]
        if not reachable:
            gap = points[i + 1].route_mile - here
            raise PlanningError(
                f"No fuel station within {tank_range:.0f} miles at route mile "
                f"{here:.0f} (next option is {gap:.0f} miles away). "
                "Route is infeasible for the given range."
            )

        cheaper = [j for j in reachable if points[j].price < points[i].price]
        if cheaper:
            target = min(cheaper, key=lambda j: points[j].route_mile)
            need = (points[target].route_mile - here) - tank
            if need > 1e-9:
                buy(i, need)
            tank -= points[target].route_mile - here
            i = target
        elif dest in reachable:
            need = (points[dest].route_mile - here) - tank
            if need > 1e-9:
                buy(i, need)
            tank -= points[dest].route_mile - here
            i = dest
        else:
            buy(i, tank_range - tank)          # fill up
            target = min(reachable, key=lambda j: points[j].price)
            tank -= points[target].route_mile - here
            i = target

    stops = []
    for idx, gallons in sorted(bought.items()):
        p = points[idx]
        if p.station is None:      # never happens (destination isn't bought)
            continue
        stops.append(Stop(
            station=p.station,
            route_mile=round(p.route_mile, 2),
            detour_miles=p.detour_miles,
            price=round(p.price, 4),
            gallons=round(gallons, 2),
            cost=round(gallons * p.price, 2),
        ))
    return stops, total_cost
