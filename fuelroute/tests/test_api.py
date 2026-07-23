"""End-to-end endpoint tests for the new response contract.

External calls (geocoding + OSRM) are mocked so the tests need no network and
assert that the routing layer is called exactly once.
"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from fuelroute.models import FuelStation
from fuelroute.services import geocoding, osrm, route_service, stations


def _fake_route():
    coords, cum, acc = [], [], 0.0
    for step in range(25):                 # ~1273-mile straight route
        coords.append((-100.0 + step, 40.0))
        if step:
            acc += 53.06
        cum.append(acc)
    return osrm.Route(coordinates=coords, cumulative_miles=cum,
                      total_miles=cum[-1], duration_seconds=20 * 3600)


class RouteApiContractTests(TestCase):
    def setUp(self):
        stations.reset_cache()
        # A line of cheap stations roughly every ~1 degree so each 500-mile
        # checkpoint has a candidate within radius.
        for step in range(25):
            FuelStation.objects.create(
                opis_id=step + 1, name=f"Stop {step}", address="Hwy",
                city="Town", state="NE", rack_id=1,
                retail_price=3.00 + (step % 3) * 0.10,
                latitude=40.0, longitude=-100.0 + step)
        stations.reset_cache()

    def _get(self):
        gs = geocoding.GeoPoint(40.0, -100.0, "Denver, CO")
        gf = geocoding.GeoPoint(40.0, -76.0, "Somewhere, PA")
        with mock.patch.object(geocoding, "geocode", side_effect=[gs, gf]), \
             mock.patch.object(osrm, "get_route",
                               return_value=_fake_route()) as m:
            resp = self.client.get(reverse("fuelroute:route-plan"),
                                   {"start": "Denver, CO", "finish": "Somewhere, PA"})
        return resp, m

    def test_missing_params_returns_400(self):
        self.assertEqual(
            self.client.get(reverse("fuelroute:route-plan")).status_code, 400)

    def test_response_has_all_required_sections(self):
        resp, routing_mock = self._get()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()

        # Only ONE routing call.
        self.assertEqual(routing_mock.call_count, 1)

        # Existing route-generation fields preserved.
        for key in ("start", "finish", "route"):
            self.assertIn(key, body)
        self.assertIn("total_distance_miles", body["route"])
        self.assertIn("estimated_duration_hours", body["route"])
        self.assertEqual(body["route"]["geometry"]["type"], "LineString")

        # New sections.
        self.assertEqual(body["vehicle"],
                         {"max_range_miles": 500, "fuel_efficiency_mpg": 10})
        fs = body["fuel_summary"]
        self.assertIn("fuel_required_gallons", fs)
        self.assertIn("fuel_stops_required", fs)
        self.assertIn("estimated_total_cost", fs)

        # A ~1273-mile route needs stops at 500 and 1000 -> 2 stops.
        self.assertEqual(fs["fuel_stops_required"], 2)
        self.assertEqual(len(body["fuel_stops"]), 2)
        self.assertEqual(len(body["cost_breakdown"]), 2)

        stop = body["fuel_stops"][0]
        for key in ("stop_number", "truck_stop", "city", "state", "address",
                    "latitude", "longitude", "fuel_price",
                    "distance_from_start", "distance_from_route",
                    "selected_reason"):
            self.assertIn(key, stop)

        line = body["cost_breakdown"][0]
        for key in ("stop_number", "gallons", "price", "cost"):
            self.assertIn(key, line)

        # fuel_required_gallons == total_distance / 10
        self.assertAlmostEqual(
            fs["fuel_required_gallons"],
            body["route"]["total_distance_miles"] / 10, places=2)

    def test_routing_failure_returns_502(self):
        gs = geocoding.GeoPoint(40.0, -100.0, "Denver, CO")
        gf = geocoding.GeoPoint(40.0, -76.0, "Somewhere, PA")
        with mock.patch.object(geocoding, "geocode", side_effect=[gs, gf]), \
             mock.patch.object(osrm, "get_route",
                               side_effect=osrm.RoutingError("boom")):
            resp = self.client.get(reverse("fuelroute:route-plan"),
                                   {"start": "Denver, CO", "finish": "Somewhere, PA"})
        self.assertEqual(resp.status_code, 502)
