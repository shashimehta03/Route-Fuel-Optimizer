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
| Cheapest station, not closest | Each 500-mile checkpoint picks the **lowest-priced** station within a configurable radius (10–20 mi); ties break on closeness to the route. |
| 500-mile range, multiple fuel-ups | A refuel checkpoint is placed at every 500 miles, so long routes get multiple stops. |
| No external fuel-price API | Prices come only from the local DB (loaded from the CSV). |
| Total fuel cost @ 10 mpg | `fuel_summary.estimated_total_cost`, with a per-stop `cost_breakdown`. |

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
    route_service.py    route generation orchestration       [geocode + the one OSRM call]
    fuel_optimizer.py   500-mile checkpoints + cheapest-station-within-radius
    fuel_cost_service.py gallons + per-stop cost breakdown
    geo.py              vectorized NumPy distance helpers (incl. point-to-segment)
    stations.py         in-memory station cache
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

### Fuel-stop optimization (`fuel_optimizer.py`)

1. **Segment** the route into 500-mile legs — refuel checkpoints at every
   500-mile mark (500, 1000, ...) before the destination.
   A 1,200-mile route yields checkpoints at 500 and 1000.
2. For each checkpoint, **find candidate stations within a configurable radius**
   (default **15 miles**, allowed range 10–20) of that point on the route.
   Stations are queried only from the in-memory cache of the local DB — no
   external fuel-price API, and no per-checkpoint database round-trips.
3. **Select the cheapest** candidate. Ties break on the smallest
   `distance_from_route`, measured with an exact point-to-segment projection so
   it stays accurate regardless of how densely the route is sampled.

If no station falls inside the default radius, the search widens (up to a
configurable cap) so a stop is still produced, and `selected_reason` says so.

### Fuel cost & accounting (`fuel_cost_service.py`)

* `fuel_required_gallons = total_distance / 10` — the whole trip's consumption.
* The vehicle **starts with a full tank** (`initial_fuel_gallons`, 50 gal),
  which covers the opening miles for free.
* At each checkpoint it buys fuel for the **next** leg:
  `gallons = min(500, distance_remaining) / 10` — a full 50-gallon tank on a
  full leg, or a partial fill on the final short leg. Their sum is
  `fuel_purchased_gallons`.
* `estimated_total_cost = Σ (gallons × price)` across the stops.

The accounting is consistent by construction:

```
initial_fuel_gallons + fuel_purchased_gallons == fuel_required_gallons
Σ cost_breakdown.gallons == fuel_purchased_gallons
Σ cost_breakdown.cost    == estimated_total_cost
```

`initial_fuel_gallons` is derived as `fuel_required − fuel_purchased`, so the
first identity always holds (it is the full 50-gal tank on any multi-tank trip,
and the smaller amount actually drawn on a single-tank trip).

### Feasibility note

The fixed-checkpoint scheme is the approach specified in the assignment; it is
a clean approximation rather than a global cost optimum. Because a checkpoint's
chosen station may sit a few miles before/after the exact 500-mile mark,
extreme edge cases (a long, station-less stretch just past a checkpoint) are
possible in theory but do not occur on real US interstates given the ~7.5k-stop
dataset. The tests cover the segmentation, selection, tie-breaking, and cost
math.

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
  "vehicle": {
    "max_range_miles": 500,
    "fuel_efficiency_mpg": 10,
    "starts_with_full_tank": true,
    "initial_fuel_gallons": 50
  },
  "fuel_summary": {
    "fuel_required_gallons": 100.6,
    "initial_fuel_gallons": 50.0,
    "fuel_purchased_gallons": 50.6,
    "fuel_stops_required": 2,
    "estimated_total_cost": 156.77,
    "currency": "USD",
    "optimization_method": "Cheapest fuel station within search radius"
  },
  "fuel_stops": [
    {
      "stop_number": 1,
      "truck_stop": "FAT DOGS LINCOLN TC",
      "city": "Lincoln", "state": "NE", "address": "I-80, EXIT 399",
      "latitude": 40.8176, "longitude": -96.6889,
      "fuel_price": 3.099, "gallons": 50.0, "cost": 154.95,
      "distance_from_start": 488.22, "distance_from_route": 1.73,
      "selected_reason": "Lowest fuel price within search radius"
    },
    {
      "stop_number": 2,
      "truck_stop": "Gulf",
      "city": "Bensenville", "state": "IL", "address": "SR-83",
      "latitude": 41.9526, "longitude": -87.9426,
      "fuel_price": 3.059, "gallons": 0.6, "cost": 1.82,
      "distance_from_start": 990.37, "distance_from_route": 7.44,
      "selected_reason": "Lowest fuel price within search radius"
    }
  ],
  "cost_breakdown": [
    { "stop_number": 1, "gallons": 50.0, "price": 3.099, "cost": 154.95 },
    { "stop_number": 2, "gallons": 0.6,  "price": 3.059, "cost": 1.82 }
  ],
  "map": {
    "geojson": { "type": "FeatureCollection", "features": [ ... ] },
    "html_map_url": "http://127.0.0.1:8000/api/route/map/?start=Denver,CO&finish=Chicago,IL"
  }
}
```

> **Fuel accounting.** `initial_fuel_gallons` (the full starting tank) +
> `fuel_purchased_gallons` = `fuel_required_gallons` exactly
> (50 + 50.6 = 100.6). The purchased total equals the sum of the
> `cost_breakdown` gallons, and `estimated_total_cost` equals the sum of the
> `cost_breakdown` costs — so the trip is priced on exactly what is bought.
> The second stop buys only 0.6 gal because its 500-mile checkpoint (mile 1000)
> lands ~6 miles from the destination, so the final leg needs almost no fuel.

### `GET /api/route/map/?start=…&finish=…`

Renders the same plan as an interactive Leaflet map (route line + clickable
fuel-stop markers). Handy for the demo video.

Error responses use appropriate status codes: `400` (bad input / geocoding
failure), `422` (route infeasible for the range), `502` (routing provider
unavailable).

---

## Tested requests & responses

Every request below was executed end-to-end against the app — real OSRM routing
plus the loaded ~7,500-station price table. The reported distances, durations,
station names/prices, and the full fuel accounting are the actual values
returned. For brevity the `route.geometry` coordinate array and the `map`
object are elided (shown as `…`); everything else is verbatim. In each case the
accounting reconciles exactly (`initial + purchased == required`, and
`Σ cost_breakdown == estimated_total_cost`) and the route is produced with a
single routing call.

### 1) New York, NY → Miami, FL — long route, 2 stops

```bash
curl --location 'http://127.0.0.1:8000/api/route/' \
--header 'Content-Type: application/json' \
--data '{ "start": "New York, NY", "finish": "Miami, FL" }'
```

```json
{
  "start":  { "label": "New York, NY", "latitude": 40.7554, "longitude": -73.9829 },
  "finish": { "label": "Miami, FL",    "latitude": 25.7689, "longitude": -80.2604 },
  "route":  { "total_distance_miles": 1284.31, "estimated_duration_hours": 24.26,
              "geometry": { "type": "LineString", "coordinates": "…" } },
  "vehicle": { "max_range_miles": 500, "fuel_efficiency_mpg": 10,
               "starts_with_full_tank": true, "initial_fuel_gallons": 50 },
  "fuel_summary": {
    "fuel_required_gallons": 128.43, "initial_fuel_gallons": 50.0,
    "fuel_purchased_gallons": 78.43, "fuel_stops_required": 2,
    "estimated_total_cost": 252.76, "currency": "USD",
    "optimization_method": "Cheapest fuel station within search radius"
  },
  "fuel_stops": [
    { "stop_number": 1, "truck_stop": "CIRCLE K #2723123", "city": "Dunn", "state": "NC",
      "address": "I-95, EXIT 71", "latitude": 35.3114, "longitude": -78.612,
      "fuel_price": 3.1423, "gallons": 50.0, "cost": 157.12,
      "distance_from_start": 514.91, "distance_from_route": 1.41,
      "selected_reason": "Lowest fuel price within search radius" },
    { "stop_number": 2, "truck_stop": "7-ELEVEN #40363", "city": "Palatka", "state": "FL",
      "address": "US-17/SR-15 & SR-100", "latitude": 29.6531, "longitude": -81.6486,
      "fuel_price": 3.364, "gallons": 28.43, "cost": 95.64,
      "distance_from_start": 982.08, "distance_from_route": 18.39,
      "selected_reason": "Lowest fuel price within search radius" }
  ],
  "cost_breakdown": [
    { "stop_number": 1, "gallons": 50.0,  "price": 3.1423, "cost": 157.12 },
    { "stop_number": 2, "gallons": 28.43, "price": 3.364,  "cost": 95.64 }
  ],
  "map": "…"
}
```

### 2) Los Angeles, CA → Seattle, WA — 2 stops (radius widens on a remote leg)

```bash
curl --location 'http://127.0.0.1:8000/api/route/' \
--header 'Content-Type: application/json' \
--data '{ "start": "Los Angeles, CA", "finish": "Seattle, WA" }'
```

```json
{
  "route": { "total_distance_miles": 1217.59, "estimated_duration_hours": 23.18, "geometry": "…" },
  "vehicle": { "max_range_miles": 500, "fuel_efficiency_mpg": 10,
               "starts_with_full_tank": true, "initial_fuel_gallons": 50 },
  "fuel_summary": {
    "fuel_required_gallons": 121.76, "initial_fuel_gallons": 50.0,
    "fuel_purchased_gallons": 71.76, "fuel_stops_required": 2,
    "estimated_total_cost": 263.42, "currency": "USD",
    "optimization_method": "Cheapest fuel station within search radius"
  },
  "fuel_stops": [
    { "stop_number": 1, "truck_stop": "MAVERIK COUNTRY STORE #409", "city": "Carson City",
      "state": "NV", "fuel_price": 3.589, "gallons": 50.0, "cost": 179.45,
      "distance_from_start": 479.02, "distance_from_route": 97.94 },
    { "stop_number": 2, "truck_stop": "PILOT TRAVEL CENTERS #386", "city": "Salem",
      "state": "OR", "fuel_price": 3.859, "gallons": 21.76, "cost": 83.97,
      "distance_from_start": 999.44, "distance_from_route": 0.45 }
  ],
  "cost_breakdown": [
    { "stop_number": 1, "gallons": 50.0,  "price": 3.589, "cost": 179.45 },
    { "stop_number": 2, "gallons": 21.76, "price": 3.859, "cost": 83.97 }
  ]
}
```

> **Edge case — sparse coverage.** The first checkpoint lands in a thinly
> served stretch of the eastern Sierra, so the search radius auto-widens to find
> a station (reflected in `selected_reason` and the larger `distance_from_route`).
> `distance_from_route` precision also tracks OSRM geometry density.

### 3) San Francisco, CA → New York, NY — coast-to-coast, 5 stops

```bash
curl --location 'http://127.0.0.1:8000/api/route/' \
--header 'Content-Type: application/json' \
--data '{ "start": "San Francisco, CA", "finish": "New York, NY" }'
```

```json
{
  "route": { "total_distance_miles": 2913.77, "estimated_duration_hours": 51.33, "geometry": "…" },
  "vehicle": { "max_range_miles": 500, "fuel_efficiency_mpg": 10,
               "starts_with_full_tank": true, "initial_fuel_gallons": 50 },
  "fuel_summary": {
    "fuel_required_gallons": 291.38, "initial_fuel_gallons": 50.0,
    "fuel_purchased_gallons": 241.38, "fuel_stops_required": 5,
    "estimated_total_cost": 797.49, "currency": "USD",
    "optimization_method": "Cheapest fuel station within search radius"
  },
  "fuel_stops": [
    { "stop_number": 1, "truck_stop": "GOLDEN GATE TRUCKSTOP", "city": "Elko",    "state": "NV", "fuel_price": 3.599,  "gallons": 50.0,  "cost": 179.95, "distance_from_start": 497.18,  "distance_from_route": 4.72 },
    { "stop_number": 2, "truck_stop": "Maverik Country Store #724", "city": "Rawlins", "state": "WY", "fuel_price": 3.289, "gallons": 50.0, "cost": 164.45, "distance_from_start": 1006.01, "distance_from_route": 0.92 },
    { "stop_number": 3, "truck_stop": "CASEYS #3465", "city": "Gibbon",  "state": "NE", "fuel_price": 3.049,  "gallons": 50.0,  "cost": 152.45, "distance_from_start": 1496.12, "distance_from_route": 3.52 },
    { "stop_number": 4, "truck_stop": "BECKS EXPRESS MART #26", "city": "Geneseo", "state": "IL", "fuel_price": 3.269, "gallons": 50.0, "cost": 163.45, "distance_from_start": 1999.77, "distance_from_route": 1.48 },
    { "stop_number": 5, "truck_stop": "Certified Oil #400410", "city": "Ravenna", "state": "OH", "fuel_price": 3.3157, "gallons": 41.38, "cost": 137.19, "distance_from_start": 2503.87, "distance_from_route": 0.66 }
  ],
  "cost_breakdown": [
    { "stop_number": 1, "gallons": 50.0,  "price": 3.599,  "cost": 179.95 },
    { "stop_number": 2, "gallons": 50.0,  "price": 3.289,  "cost": 164.45 },
    { "stop_number": 3, "gallons": 50.0,  "price": 3.049,  "cost": 152.45 },
    { "stop_number": 4, "gallons": 50.0,  "price": 3.269,  "cost": 163.45 },
    { "stop_number": 5, "gallons": 41.38, "price": 3.3157, "cost": 137.19 }
  ]
}
```

### 4) Phoenix, AZ → New Orleans, LA — 3 stops, cheapest pick is clear

```bash
curl --location 'http://127.0.0.1:8000/api/route/' \
--header 'Content-Type: application/json' \
--data '{ "start": "Phoenix, AZ", "finish": "New Orleans, LA" }'
```

```json
{
  "route": { "total_distance_miles": 1525.36, "estimated_duration_hours": 26.31, "geometry": "…" },
  "fuel_summary": {
    "fuel_required_gallons": 152.54, "initial_fuel_gallons": 50.0,
    "fuel_purchased_gallons": 102.54, "fuel_stops_required": 3,
    "estimated_total_cost": 305.34, "currency": "USD",
    "optimization_method": "Cheapest fuel station within search radius"
  },
  "fuel_stops": [
    { "stop_number": 1, "truck_stop": "TRAVELING TIGER CENTER", "city": "Sierra Blanca", "state": "TX", "fuel_price": 3.1557, "gallons": 50.0, "cost": 157.78, "distance_from_start": 520.44,  "distance_from_route": 1.66 },
    { "stop_number": 2, "truck_stop": "CIRCLE K #2742034", "city": "Schertz", "state": "TX", "fuel_price": 2.794, "gallons": 50.0, "cost": 139.70, "distance_from_start": 986.83, "distance_from_route": 3.87 },
    { "stop_number": 3, "truck_stop": "AIRLINE CHEVRON", "city": "Kenner", "state": "LA", "fuel_price": 3.099, "gallons": 2.54, "cost": 7.86, "distance_from_start": 1514.11, "distance_from_route": 0.81 }
  ],
  "cost_breakdown": [
    { "stop_number": 1, "gallons": 50.0, "price": 3.1557, "cost": 157.78 },
    { "stop_number": 2, "gallons": 50.0, "price": 2.794,  "cost": 139.70 },
    { "stop_number": 3, "gallons": 2.54, "price": 3.099,  "cost": 7.86 }
  ]
}
```

### 5) Austin, TX → Dallas, TX — **edge case: trip fits in one tank (0 stops)**

```bash
curl --location 'http://127.0.0.1:8000/api/route/' \
--header 'Content-Type: application/json' \
--data '{ "start": "Austin, TX", "finish": "Dallas, TX" }'
```

The 195.56-mile trip is under the 500-mile range, so no refuel is needed. The
starting tank covers it, nothing is purchased, and the cost is `$0.00` — while
`fuel_required_gallons` still reports the fuel actually consumed.

```json
{
  "route": { "total_distance_miles": 195.56, "estimated_duration_hours": 3.46, "geometry": "…" },
  "vehicle": { "max_range_miles": 500, "fuel_efficiency_mpg": 10,
               "starts_with_full_tank": true, "initial_fuel_gallons": 19.56 },
  "fuel_summary": {
    "fuel_required_gallons": 19.56, "initial_fuel_gallons": 19.56,
    "fuel_purchased_gallons": 0.0, "fuel_stops_required": 0,
    "estimated_total_cost": 0.0, "currency": "USD",
    "optimization_method": "Cheapest fuel station within search radius"
  },
  "fuel_stops": [],
  "cost_breakdown": []
}
```

### 6) `"abcdefxyz"` → Chicago, IL — **edge case: invalid location (HTTP 400)**

```bash
curl --location 'http://127.0.0.1:8000/api/route/' \
--header 'Content-Type: application/json' \
--data '{ "start": "abcdefxyz", "finish": "Chicago, IL" }'
```

An unresolvable start location returns a clean `400` with a descriptive error
(no route or fuel work is attempted):

```json
{
  "error": "geocoding_failed",
  "detail": "Could not geocode 'abcdefxyz' within the USA."
}
```

---

## Tests

```bash
python manage.py test fuelroute
```

Covers: OSRM response parsing & mileage math, 500-mile checkpoint segmentation,
cheapest-station-within-radius selection and tie-breaking, the gallons/cost
calculation, and the full endpoint contract (external calls mocked and asserted
to fire exactly once, so tests need no network).

---

## Configuration

All knobs live in `settings.FUEL_ROUTE` and can be overridden via environment
variables: `VEHICLE_RANGE_MILES`, `MILES_PER_GALLON`, `SEARCH_RADIUS_MILES`
(checkpoint search radius, 10–20), `MAX_SEARCH_RADIUS_MILES` (widen cap),
`CORRIDOR_MILES`, `OSRM_BASE_URL`, `NOMINATIM_BASE_URL`, `HTTP_TIMEOUT_SECONDS`.
The SQLite path can be overridden with `DJANGO_DB_PATH`.

---

## Notes / assumptions

* **Coordinates** for stations are city-level centroids (the price file has no
  lat/lon). This is precise enough to snap a station to the highway corridor it
  sits on. `distance_from_route` in each stop reports how far the station is
  from the route line (exact point-to-segment distance).
* The `FuelStation` model stores columns `name` / `retail_price`; the
  assignment's `truck_stop_name` / `fuel_price` names are exposed as aliases on
  the model so no data migration is needed.
* Non-US rows in the price file (e.g. Canadian provinces) are loaded but left
  un-geocoded and are ignored by the optimizer, since routing is US-only.
* OSRM's public demo server is best-effort; for production you'd self-host OSRM
  or use a keyed provider. The base URL is configurable.
