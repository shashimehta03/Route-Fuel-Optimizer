from django.test import SimpleTestCase

from fuelroute.services import osrm


def _payload(coords, seg_meters, total_m, duration=3600.0):
    return {
        "code": "Ok",
        "routes": [{
            "distance": total_m,
            "duration": duration,
            "geometry": {"type": "LineString", "coordinates": coords},
            "legs": [{"annotation": {"distance": seg_meters}}],
        }],
        "waypoints": [],
    }


class ParseOsrmTests(SimpleTestCase):
    def test_cumulative_mileage_from_annotations(self):
        coords = [[-104.99, 39.74], [-100.0, 40.0], [-87.63, 41.88]]
        seg = [1609.344 * 100, 1609.344 * 200]  # 100 mi, then 200 mi
        route = osrm.parse_osrm_response(_payload(coords, seg, 1609.344 * 300))

        self.assertEqual(len(route.coordinates), 3)
        self.assertAlmostEqual(route.cumulative_miles[0], 0.0, places=3)
        self.assertAlmostEqual(route.cumulative_miles[1], 100.0, places=2)
        self.assertAlmostEqual(route.cumulative_miles[2], 300.0, places=2)
        self.assertAlmostEqual(route.total_miles, 300.0, places=2)

    def test_error_on_no_route(self):
        with self.assertRaises(osrm.RoutingError):
            osrm.parse_osrm_response({"code": "NoRoute", "routes": []})

    def test_falls_back_to_haversine_without_annotations(self):
        coords = [[-100.0, 40.0], [-100.0, 41.0]]  # ~69 miles north
        payload = {
            "code": "Ok",
            "routes": [{
                "distance": 0.0, "duration": 0.0,
                "geometry": {"type": "LineString", "coordinates": coords},
                "legs": [{}],
            }],
        }
        route = osrm.parse_osrm_response(payload)
        self.assertGreater(route.cumulative_miles[1], 60)
        self.assertLess(route.cumulative_miles[1], 75)
