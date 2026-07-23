# Fuel Route API

A Django REST API that, given a **start** and **finish** location in the USA,
returns:

* the driving **route** (map geometry) between them,
* the **cost-optimal fuel stops** along the way for a vehicle with a **500-mile
  range** at **10 miles per gallon**, and
* the **total money spent on fuel** for the trip.

Fuel prices come from the provided OPIS truck-stop price file
(`data/fuel-prices.csv`, ~8,150 stations).

---

## Highlights (how the requirements are met)

| Requirement | How it's handled |
|---|---|
| Latest stable Django | Django 6.0.7 (`requirements.txt`). |
| Free map / routing API | [OSRM public server](https://router.project-osrm.org) — no API key. |
| **One** call to the routing API | Exactly **one** OSRM call per request. It returns the full geometry **and** per-segment distances, so no follow-up calls are needed. |
| Fast responses | Stations are geocoded **once** at load time and matched in-memory with vectorized NumPy. Compute time is **~16 ms** across all 7,500+ stations; the only network wait is the single OSRM call. |
| 500-mile range, multiple fuel-ups | The optimizer inserts as many stops as the distance requires, never exceeding 500 miles between fill-ups. |
| Total fuel cost @ 10 mpg | Returned as `fuel.total_cost_usd`. |

---

## Architecture

```
config/                 Django project (settings, urls, wsgi/asgi)
fuelroute/
  models.py             FuelStation (name, city, state, price, lat, lon)
  services/
    gazetteer.py        offline (city, state) -> (lat, lon)  [no network]
    geocoding.py        geocode the start/finish inputs      [offline first, Nominatim fallback]
    osrm.py             OSRM client + response parser        [the single routing call]
    geo.py              vectorized NumPy distance helpers
    stations.py         in-memory station cache
    planner.py          the core: match stations to route + optimal fueling
  management/commands/
    load_stations.py    load + geocode the CSV (run once)
  views.py              DRF endpoints
  templates/            Leaflet map page for the demo
  tests/                unit + integration tests
data/fuel-prices.csv    the OPIS price file
```

### Why geocoding is offline

The price file has street addresses but no coordinates. Geocoding 8,150
stations live would be slow and rate-limited. Instead, `load_stations`
resolves each station's `(city, state)` to a centroid using the bundled
`zipcodes` dataset — **100% coverage of the US stations**, zero network calls,
runs in ~3 seconds. The request path therefore never geocodes stations.

### The single routing call

`osrm.get_route` requests `overview=full&geometries=geojson&annotations=distance`.
That one response contains every route vertex and the distance of every
segment between vertices, which is enough to compute the cumulative mileage of
any point on the route — no additional calls.

### Matching stations to the route

The route's bounding box pre-filters candidate stations; the survivors are
compared to every route vertex with a vectorized equirectangular projection.
Stations within the corridor (default **5 miles**, configurable) become
candidate fuel stops, each tagged with its mile-marker along the route.

### The fuel-cost model & optimizer

**Model.** The trip burns `distance / 10` gallons. Every one of those gallons
is priced at the station where it is *optimally* bought, subject to the
500-mile range between fill-ups. The origin is treated as a departure fill-up
priced at the nearest on-route station, so the reported figure is the **true
cost of all fuel the journey burns** (not just incremental top-ups). The
destination can be reached with an empty tank.

**Algorithm.** The classic gas-station greedy, which is provably optimal for
this problem. At the current stop (price `p`):

1. If a strictly cheaper station is reachable within range → buy just enough to
   reach the nearest such station.
2. Else if the destination is reachable → buy just enough to finish.
3. Else → fill the tank and drive to the cheapest reachable station.

The test suite verifies this greedy against a brute-force dynamic-programming
optimum over 200 randomized instances.

---

## Setup

Requires **Python 3.12+** (Django 6.0).

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py load_stations      # loads + geocodes data/fuel-prices.csv
python manage.py runserver
```

---

## API

### `GET /api/route/`  ·  `POST /api/route/`

| Param | Example | Notes |
|---|---|---|
| `start`  | `Denver, CO` | `City, ST`, `lat,lon`, or a free-form address |
| `finish` | `Chicago, IL` | same |

**GET**

```
GET /api/route/?start=Denver,CO&finish=Chicago,IL
```

**POST**

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Denver, CO", "finish": "Chicago, IL"}'
```

**Response (abridged, real output for Denver → Chicago):**

```json
{
  "start":  { "label": "Denver, CO", "latitude": 39.74, "longitude": -104.99 },
  "finish": { "label": "Chicago, IL", "latitude": 41.88, "longitude": -87.63 },
  "route": {
    "total_distance_miles": 1005.96,
    "estimated_duration_hours": 17.82,
    "geometry": { "type": "LineString", "coordinates": [[-104.99, 39.74], ...] }
  },
  "fuel": {
    "vehicle_range_miles": 500,
    "miles_per_gallon": 10,
    "total_gallons": 100.6,
    "total_cost_usd": 305.07,
    "number_of_stops": 3,
    "stops": [
      { "order": 1, "route_mile": 0,   "price_per_gallon": 3.299,
        "gallons_purchased": 21.0, "cost": 69.27,
        "station": { "name": "CIRCLE K #2744095", "city": "Denver",   "state": "CO" } },
      { "order": 2, "route_mile": 210, "price_per_gallon": 3.014,
        "gallons_purchased": 32.2, "cost": 97.02,
        "station": { "name": "FATDOGS OGALLALA", "city": "Ogallala", "state": "NE" } },
      { "order": 3, "route_mile": 532, "price_per_gallon": 2.9273,
        "gallons_purchased": 47.4, "cost": 138.79,
        "station": { "name": "QUIKTRIP #598",    "city": "Omaha",    "state": "NE" } }
    ]
  },
  "map": {
    "geojson": { "type": "FeatureCollection", "features": [ ... ] },
    "html_map_url": "http://127.0.0.1:8000/api/route/map/?start=Denver,CO&finish=Chicago,IL"
  }
}
```

### `GET /api/route/map/?start=…&finish=…`

Renders the same plan as an interactive Leaflet map (route line + clickable
fuel-stop markers). Handy for the demo video.

Error responses use appropriate status codes: `400` (bad input / geocoding
failure), `422` (route infeasible for the range), `502` (routing provider
unavailable).

---

## Tests

```bash
python manage.py test fuelroute
```

Covers: OSRM response parsing & mileage math, the optimizer vs. a brute-force
DP optimum, an infeasible-range case, and the full endpoint (external calls
mocked, so tests need no network).

---

## Configuration

All knobs live in `settings.FUEL_ROUTE` and can be overridden via environment
variables: `VEHICLE_RANGE_MILES`, `MILES_PER_GALLON`, `CORRIDOR_MILES`,
`OSRM_BASE_URL`, `NOMINATIM_BASE_URL`, `HTTP_TIMEOUT_SECONDS`. The SQLite path
can be overridden with `DJANGO_DB_PATH`.
```

---

## Notes / assumptions

* **Coordinates** for stations are city-level centroids (the price file has no
  lat/lon). This is precise enough to snap a station to the highway corridor it
  sits on. `detour_miles` in each stop reports how far the station is from the
  route line.
* Non-US rows in the price file (e.g. Canadian provinces) are loaded but left
  un-geocoded and are ignored by the planner, since routing is US-only.
* OSRM's public demo server is best-effort; for production you'd self-host OSRM
  or use a keyed provider. The base URL is configurable.
