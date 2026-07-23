from unittest import mock

from django.test import TestCase
from django.urls import reverse

from fuelroute.models import FuelStation
from fuelroute.services import geocoding, osrm, planner, stations


class RouteApiTests(TestCase):
    def setUp(self):
        stations.reset_cache()
        for i, (name, lon, price) in enumerate([
            ("A", -100.0, 3.00), ("B", -95.0, 2.50), ("C", -91.0, 3.20),
        ]):
            FuelStation.objects.create(
                opis_id=i + 1, name=name, address="", city="Town", state="NE",
                rack_id=1, retail_price=price, latitude=40.0, longitude=lon,
            )
        stations.reset_cache()

    def _fake_route(self):
        coords, cum, acc = [], [], 0.0
        for step in range(11):
            coords.append((-100.0 + step, 40.0))
            if step:
                acc += 53.06
            cum.append(acc)
        return osrm.Route(coordinates=coords, cumulative_miles=cum,
                          total_miles=cum[-1], duration_seconds=9 * 3600)

    def test_missing_params_returns_400(self):
        resp = self.client.get(reverse("fuelroute:route-plan"))
        self.assertEqual(resp.status_code, 400)

    def test_route_endpoint_returns_plan(self):
        gs = geocoding.GeoPoint(40.0, -100.0, "Denver")
        gf = geocoding.GeoPoint(40.0, -90.0, "Chicago")
        with mock.patch.object(geocoding, "geocode", side_effect=[gs, gf]), \
             mock.patch.object(osrm, "get_route", return_value=self._fake_route()):
            resp = self.client.get(reverse("fuelroute:route-plan"),
                                   {"start": "Denver, CO", "finish": "Chicago, IL"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("route", body)
        self.assertIn("fuel", body)
        self.assertGreater(body["fuel"]["total_cost_usd"], 0)
        self.assertEqual(body["map"]["geojson"]["type"], "FeatureCollection")
        # route line + at least one fuel-stop marker
        self.assertGreaterEqual(len(body["map"]["geojson"]["features"]), 2)

    def test_routing_failure_returns_502(self):
        gs = geocoding.GeoPoint(40.0, -100.0, "Denver")
        gf = geocoding.GeoPoint(40.0, -90.0, "Chicago")
        with mock.patch.object(geocoding, "geocode", side_effect=[gs, gf]), \
             mock.patch.object(osrm, "get_route",
                               side_effect=osrm.RoutingError("boom")):
            resp = self.client.get(reverse("fuelroute:route-plan"),
                                   {"start": "Denver, CO", "finish": "Chicago, IL"})
        self.assertEqual(resp.status_code, 502)
