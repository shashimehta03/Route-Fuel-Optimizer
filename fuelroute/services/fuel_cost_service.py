"""Fuel quantity and cost calculation.

Accounting model (fully explicit, so the response is internally consistent):

* The trip *consumes* ``total_distance / MPG`` gallons — ``fuel_required_gallons``.
* The vehicle **starts with a full tank** of ``max_range / MPG`` gallons
  (50 gal). That starting fuel covers the first miles for free — no purchase.
* At each 500-mile checkpoint it buys the fuel for the **next** leg:
  ``gallons = min(range, distance_remaining) / MPG``. The sum of those buys is
  ``fuel_purchased_gallons``.
* ``estimated_total_cost`` is the sum of ``gallons * price`` across the stops,
  so the cost always matches the gallons actually purchased.

The two quantities reconcile exactly::

    initial_fuel_gallons + fuel_purchased_gallons == fuel_required_gallons

``initial_fuel_gallons`` is derived as ``fuel_required - fuel_purchased`` so the
identity holds by construction (it equals the full 50-gal tank on any trip
longer than one tank, and the smaller amount actually used on a short trip).
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from .fuel_optimizer import FuelStop


@dataclass
class CostLine:
    stop_number: int
    gallons: float
    price: float
    cost: float


@dataclass
class FuelCosting:
    fuel_required_gallons: float
    fuel_purchased_gallons: float
    initial_fuel_gallons: float
    tank_capacity_gallons: float
    fuel_stops_required: int
    estimated_total_cost: float
    cost_breakdown: list[CostLine]
    # stop_number -> (gallons, cost), so the view can enrich each fuel stop.
    per_stop: dict[int, tuple[float, float]]


def calculate(total_distance: float, stops: list[FuelStop]) -> FuelCosting:
    cfg = settings.FUEL_ROUTE
    mpg = cfg["MILES_PER_GALLON"]
    max_range = cfg["VEHICLE_RANGE_MILES"]

    tank_capacity = max_range / mpg          # full tank, e.g. 50 gal
    fuel_required = total_distance / mpg

    breakdown: list[CostLine] = []
    per_stop: dict[int, tuple[float, float]] = {}
    total_cost = 0.0
    purchased_gallons = 0.0
    for stop in stops:
        remaining = total_distance - stop.checkpoint_mile
        leg_miles = min(max_range, max(remaining, 0.0))
        gallons = leg_miles / mpg
        cost = gallons * stop.fuel_price
        total_cost += cost
        purchased_gallons += gallons
        per_stop[stop.stop_number] = (round(gallons, 2), round(cost, 2))
        breakdown.append(CostLine(
            stop_number=stop.stop_number,
            gallons=round(gallons, 2),
            price=round(stop.fuel_price, 4),
            cost=round(cost, 2),
        ))

    # Whatever isn't purchased came from the starting tank. Deriving it this way
    # makes  initial + purchased == required  true by construction.
    initial_fuel = fuel_required - purchased_gallons
    # It can never exceed a physical full tank.
    initial_fuel = min(initial_fuel, tank_capacity)

    return FuelCosting(
        fuel_required_gallons=round(fuel_required, 2),
        fuel_purchased_gallons=round(purchased_gallons, 2),
        initial_fuel_gallons=round(initial_fuel, 2),
        tank_capacity_gallons=round(tank_capacity, 2),
        fuel_stops_required=len(stops),
        estimated_total_cost=round(total_cost, 2),
        cost_breakdown=breakdown,
        per_stop=per_stop,
    )
