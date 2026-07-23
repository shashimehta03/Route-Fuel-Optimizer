"""Tests for the fuel optimizer (checkpoints + station selection) and the
fuel cost service."""
from django.test import TestCase, override_settings

from fuelroute.models import FuelStation
from fuelroute.services import fuel_cost_service, fuel_optimizer, stations
from fuelroute.services.fuel_optimizer import (
    FuelStop, optimize_fuel_stops, point_at_mile, refuel_checkpoints,
)
from fuelroute.services.osrm import Route


class CheckpointTests(TestCase):
    def test_checkpoints_every_500_miles(self):
        self.assertEqual(refuel_checkpoints(1200, 500), [500, 1000])
        self.assertEqual(refuel_checkpoints(500, 500), [])      # one tank is enough
        self.assertEqual(refuel_checkpoints(499, 500), [])
        self.assertEqual(refuel_checkpoints(1600, 500), [500, 1000, 1500])

    def test_point_at_mile_interpolates(self):
        # Straight line, two vertices, 100 miles apart.
        route = Route(coordinates=[(-100.0, 40.0), (-100.0, 41.0)],
                      cumulative_miles=[0.0, 100.0],
                      total_miles=100.0, duration_seconds=0)
        lat, lon = point_at_mile(route, 50.0)
        self.assertAlmostEqual(lat, 40.5, places=6)
        self.assertAlmostEqual(lon, -100.0, places=6)


def _straight_route():
    """West->east near latitude 40; ~53 miles per 1-degree lon step."""
    coords, cum, acc = [], [], 0.0
    for step in range(25):                 # -100 .. -76  (~24 deg ~ 1273 mi)
        coords.append((-100.0 + step, 40.0))
        if step:
            acc += 53.06
        cum.append(acc)
    return Route(coordinates=coords, cumulative_miles=cum,
                 total_miles=cum[-1], duration_seconds=20 * 3600)


@override_settings()
class OptimizerSelectionTests(TestCase):
    def setUp(self):
        stations.reset_cache()

    def _station(self, name, lat, lon, price):
        return FuelStation.objects.create(
            opis_id=abs(hash(name)) % 100000, name=name, address="Hwy",
            city="Town", state="NE", rack_id=1, retail_price=price,
            latitude=lat, longitude=lon)

    @override_settings(FUEL_ROUTE={
        "VEHICLE_RANGE_MILES": 500, "MILES_PER_GALLON": 10,
        "CORRIDOR_MILES": 5, "SEARCH_RADIUS_MILES": 15,
        "MAX_SEARCH_RADIUS_MILES": 60, "OSRM_BASE_URL": "", "NOMINATIM_BASE_URL": "",
        "HTTP_TIMEOUT_SECONDS": 5, "USER_AGENT": "t",
    })
    def test_picks_cheapest_within_radius(self):
        route = _straight_route()
        # Checkpoint 1 is at mile 500 ~ lon -90.57 (step ~9.42). Put three
        # stations near there with different prices.
        cp_lon = -100.0 + 500 / 53.06
        self._station("Expensive", 40.0, cp_lon, 4.20)
        self._station("Cheapest", 40.05, cp_lon, 2.80)   # ~3.5 mi off route
        self._station("Mid", 40.0, cp_lon + 0.05, 3.50)
        stations.reset_cache()

        stops = optimize_fuel_stops(route)
        self.assertGreaterEqual(len(stops), 1)
        first = stops[0]
        self.assertEqual(first.station["name"], "Cheapest")
        self.assertEqual(first.fuel_price, 2.80)
        self.assertLessEqual(first.distance_from_route, 15)

    @override_settings(FUEL_ROUTE={
        "VEHICLE_RANGE_MILES": 500, "MILES_PER_GALLON": 10,
        "CORRIDOR_MILES": 5, "SEARCH_RADIUS_MILES": 15,
        "MAX_SEARCH_RADIUS_MILES": 60, "OSRM_BASE_URL": "", "NOMINATIM_BASE_URL": "",
        "HTTP_TIMEOUT_SECONDS": 5, "USER_AGENT": "t",
    })
    def test_every_checkpoint_yields_a_stop_even_with_no_nearby_station(self):
        # Regression: a checkpoint with no station inside the max radius must
        # still produce a stop (nearest-station fallback), so no 500-mile leg
        # goes unfuelled and the accounting stays consistent.
        route = _straight_route()                 # ~1273 mi -> checkpoints 500, 1000
        # Only one station, hundreds of miles from every checkpoint.
        self._station("Lonely", 40.0, -76.0, 3.00)   # near the very end
        stations.reset_cache()

        stops = optimize_fuel_stops(route)
        # Two checkpoints (500, 1000) -> two stops, numbered 1 and 2.
        self.assertEqual([s.stop_number for s in stops], [1, 2])
        self.assertTrue(any("Nearest available station" in s.selected_reason
                            for s in stops))

    @override_settings(FUEL_ROUTE={
        "VEHICLE_RANGE_MILES": 500, "MILES_PER_GALLON": 10,
        "CORRIDOR_MILES": 5, "SEARCH_RADIUS_MILES": 15,
        "MAX_SEARCH_RADIUS_MILES": 60, "OSRM_BASE_URL": "", "NOMINATIM_BASE_URL": "",
        "HTTP_TIMEOUT_SECONDS": 5, "USER_AGENT": "t",
    })
    def test_tie_breaks_on_distance_from_route(self):
        route = _straight_route()
        cp_lon = -100.0 + 500 / 53.06
        self._station("Far same price", 40.12, cp_lon, 3.00)   # ~8 mi off
        self._station("Near same price", 40.02, cp_lon, 3.00)  # ~1.4 mi off
        stations.reset_cache()

        stops = optimize_fuel_stops(route)
        self.assertEqual(stops[0].station["name"], "Near same price")


class CostServiceTests(TestCase):
    def _stop(self, n, checkpoint, price):
        return FuelStop(stop_number=n, checkpoint_mile=checkpoint,
                        station={"name": "S"}, distance_from_start=checkpoint,
                        distance_from_route=1.0, fuel_price=price,
                        selected_reason="test")

    @override_settings(FUEL_ROUTE={
        "VEHICLE_RANGE_MILES": 500, "MILES_PER_GALLON": 10,
        "CORRIDOR_MILES": 5, "SEARCH_RADIUS_MILES": 15,
        "MAX_SEARCH_RADIUS_MILES": 60, "OSRM_BASE_URL": "", "NOMINATIM_BASE_URL": "",
        "HTTP_TIMEOUT_SECONDS": 5, "USER_AGENT": "t",
    })
    def test_gallons_and_cost(self):
        # 1200-mile trip, stops at 500 and 1000.
        stops = [self._stop(1, 500, 3.12), self._stop(2, 1000, 3.19)]
        costing = fuel_cost_service.calculate(1200, stops)

        self.assertEqual(costing.fuel_required_gallons, 120.0)   # 1200/10
        self.assertEqual(costing.fuel_stops_required, 2)
        # Stop 1 serves a full 500-mi leg -> 50 gal @3.12 = 156.0
        self.assertEqual(costing.cost_breakdown[0].gallons, 50.0)
        self.assertEqual(costing.cost_breakdown[0].cost, 156.0)
        # Stop 2 serves the final 200-mi leg -> 20 gal @3.19 = 63.8
        self.assertEqual(costing.cost_breakdown[1].gallons, 20.0)
        self.assertEqual(costing.cost_breakdown[1].cost, 63.8)
        self.assertEqual(costing.estimated_total_cost, 156.0 + 63.8)

        # Explicit accounting: 50-gal starting tank + 70 gal purchased = 120.
        self.assertEqual(costing.fuel_purchased_gallons, 70.0)
        self.assertEqual(costing.initial_fuel_gallons, 50.0)
        self.assertAlmostEqual(
            costing.initial_fuel_gallons + costing.fuel_purchased_gallons,
            costing.fuel_required_gallons, places=6)
        # Cost matches the gallons actually purchased.
        self.assertAlmostEqual(
            sum(c.cost for c in costing.cost_breakdown),
            costing.estimated_total_cost, places=6)
        self.assertAlmostEqual(
            sum(c.gallons for c in costing.cost_breakdown),
            costing.fuel_purchased_gallons, places=6)

    @override_settings(FUEL_ROUTE={
        "VEHICLE_RANGE_MILES": 500, "MILES_PER_GALLON": 10,
        "CORRIDOR_MILES": 5, "SEARCH_RADIUS_MILES": 15,
        "MAX_SEARCH_RADIUS_MILES": 60, "OSRM_BASE_URL": "", "NOMINATIM_BASE_URL": "",
        "HTTP_TIMEOUT_SECONDS": 5, "USER_AGENT": "t",
    })
    def test_short_trip_needs_no_stops(self):
        costing = fuel_cost_service.calculate(400, [])
        self.assertEqual(costing.fuel_stops_required, 0)
        self.assertEqual(costing.estimated_total_cost, 0.0)
        self.assertEqual(costing.fuel_required_gallons, 40.0)
        # Nothing purchased; the 40 gal all came from the starting tank.
        self.assertEqual(costing.fuel_purchased_gallons, 0.0)
        self.assertEqual(costing.initial_fuel_gallons, 40.0)
        self.assertAlmostEqual(
            costing.initial_fuel_gallons + costing.fuel_purchased_gallons,
            costing.fuel_required_gallons, places=6)
