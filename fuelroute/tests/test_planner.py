import math
import random
from unittest import mock

from django.test import TestCase

from fuelroute.models import FuelStation
from fuelroute.services import geocoding, osrm, planner, stations
from fuelroute.services.planner import FuelPoint, _optimize


def _fp(mile, price, is_origin=False, is_destination=False):
    station = None if is_destination else {
        "name": f"S@{mile}", "address": "", "city": "X", "state": "XX",
        "opis_id": 0, "price": price, "latitude": 40.0, "longitude": -100.0,
    }
    return FuelPoint(route_mile=float(mile), price=price, station=station,
                     is_origin=is_origin, is_destination=is_destination)


def _brute_force_min_cost(miles, prices, tank_range, mpg):
    """Exact integer-fuel DP optimum, used to validate the greedy.

    State: (point index, integer fuel level in miles). Buys integer miles of
    fuel. price[-1] == inf means fuel cannot be bought at the destination.
    """
    n = len(miles)
    R = int(tank_range)
    INF = math.inf
    # dp[f] = min cost to be at current point with fuel f (before buying).
    dp = [INF] * (R + 1)
    dp[0] = 0.0
    for i in range(n - 1):
        gap = int(miles[i + 1] - miles[i])
        ndp = [INF] * (R + 1)
        for f in range(R + 1):
            if dp[f] == INF:
                continue
            # Buy b miles of fuel (b=0 if price is inf/destination).
            max_buy = 0 if math.isinf(prices[i]) else R - f
            for b in range(0, max_buy + 1):
                nf = f + b
                if nf < gap:
                    continue
                cost = dp[f] + (b / mpg) * prices[i]
                arrive = nf - gap
                if cost < ndp[arrive]:
                    ndp[arrive] = cost
        dp = ndp
    return min(dp)


class OptimizeTests(TestCase):
    def test_matches_bruteforce_on_random_instances(self):
        rng = random.Random(42)
        mpg = 1.0            # 1 mile per gallon -> tank == range in gallons
        tank_range = 20
        for _ in range(200):
            k = rng.randint(1, 6)          # intermediate stations
            miles = sorted(rng.sample(range(1, 40), k))
            total = miles[-1] + rng.randint(1, tank_range)
            prices = [round(rng.uniform(2.0, 5.0), 2) for _ in miles]

            # points: origin(0) + stations + destination(total)
            pts = [_fp(0, round(rng.uniform(2.0, 5.0), 2), is_origin=True)]
            pts += [_fp(m, p) for m, p in zip(miles, prices)]
            pts.append(_fp(total, math.inf, is_destination=True))

            all_miles = [p.route_mile for p in pts]
            all_prices = [p.price for p in pts]

            # Skip instances the greedy would (correctly) call infeasible.
            gaps = [all_miles[i + 1] - all_miles[i] for i in range(len(all_miles) - 1)]
            if any(g > tank_range for g in gaps):
                with self.assertRaises(planner.PlanningError):
                    _optimize(pts, mpg=mpg, tank_range=tank_range)
                continue

            _, greedy_cost = _optimize(pts, mpg=mpg, tank_range=tank_range)
            dp_cost = _brute_force_min_cost(all_miles, all_prices, tank_range, mpg)
            self.assertAlmostEqual(greedy_cost, dp_cost, places=6,
                                   msg=f"miles={all_miles} prices={all_prices}")

    def test_prefers_cheaper_reachable_station(self):
        # Origin expensive; a cheap station is within range -> buy minimum at
        # origin, fill the rest cheap.
        pts = [
            _fp(0, 5.00, is_origin=True),
            _fp(100, 2.00),
            _fp(400, 9.00),
            _fp(500, math.inf, is_destination=True),
        ]
        stops, cost = _optimize(pts, mpg=10, tank_range=500)
        # 500 mi -> 50 gal. Buy 10 gal @5 (reach mile100), 40 gal @2 (finish).
        self.assertAlmostEqual(cost, 10 * 5.0 + 40 * 2.0, places=4)
        self.assertEqual([s.route_mile for s in stops], [0.0, 100.0])

    def test_infeasible_gap_raises(self):
        pts = [
            _fp(0, 3.0, is_origin=True),
            _fp(700, math.inf, is_destination=True),   # 700 mi > 500 range
        ]
        with self.assertRaises(planner.PlanningError):
            _optimize(pts, mpg=10, tank_range=500)


class PlanTripEndToEndTests(TestCase):
    def setUp(self):
        stations.reset_cache()
        # A straight west->east corridor near latitude 40. Place stations right
        # on the route with varying prices.
        specs = [
            ("Cheap Start", 40.0, -100.0, 3.00),
            ("Mid Pricey", 40.0, -97.0, 4.50),
            ("Mid Cheap", 40.0, -95.0, 2.50),
            ("Near End", 40.0, -91.0, 3.20),
        ]
        for i, (name, lat, lon, price) in enumerate(specs):
            FuelStation.objects.create(
                opis_id=i + 1, name=name, address="Hwy", city="Town",
                state="NE", rack_id=1, retail_price=price,
                latitude=lat, longitude=lon,
            )
        stations.reset_cache()

    def _fake_route(self):
        # ~10 vertices from lon -100 to -90 at lat 40. At lat 40, 1 deg lon is
        # ~53.06 miles, so total ~530 miles -> forces at least one mid stop.
        coords, cum, acc = [], [], 0.0
        prev = None
        for step in range(11):
            lon = -100.0 + step  # -100 .. -90
            lat = 40.0
            coords.append((lon, lat))
            if prev is not None:
                acc += 53.06
            cum.append(acc)
            prev = (lon, lat)
        return osrm.Route(coordinates=coords, cumulative_miles=cum,
                          total_miles=cum[-1], duration_seconds=9 * 3600)

    def test_plan_trip_produces_optimal_stops(self):
        gp_start = geocoding.GeoPoint(40.0, -100.0, "Start")
        gp_finish = geocoding.GeoPoint(40.0, -90.0, "Finish")
        with mock.patch.object(geocoding, "geocode",
                               side_effect=[gp_start, gp_finish]), \
             mock.patch.object(osrm, "get_route", return_value=self._fake_route()):
            plan = planner.plan_trip("Start", "Finish")

        self.assertGreater(plan.total_distance_miles, 500)
        self.assertAlmostEqual(plan.total_gallons,
                               plan.total_distance_miles / 10, places=2)
        self.assertGreater(plan.total_fuel_cost, 0)
        self.assertGreaterEqual(len(plan.fuel_stops), 1)
        # The expensive mid station ($4.50) should never be chosen over the
        # cheaper $2.50 option within range.
        chosen = {s.station["name"] for s in plan.fuel_stops}
        self.assertNotIn("Mid Pricey", chosen)
