"""API views for the Fuel Route planner.

The route generation itself is untouched (``route_service`` -> ``osrm``, one
call). This layer adds fuel-stop optimization and cost calculation and shapes
the response.
"""
from __future__ import annotations

from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from .serializers import RouteRequestSerializer
from .services import fuel_cost_service, fuel_optimizer, osrm, route_service
from .services.fuel_optimizer import FuelStop
from .services.geocoding import GeocodingError


def _fuel_stops_payload(stops: list[FuelStop], costing) -> list[dict]:
    out = []
    for s in stops:
        gallons, cost = costing.per_stop.get(s.stop_number, (0.0, 0.0))
        st = s.station
        out.append({
            "stop_number": s.stop_number,
            "truck_stop": st["name"],
            "city": st["city"],
            "state": st["state"],
            "address": st["address"],
            "latitude": st["latitude"],
            "longitude": st["longitude"],
            "fuel_price": s.fuel_price,
            "gallons": gallons,
            "cost": cost,
            "distance_from_start": s.distance_from_start,
            "distance_from_route": s.distance_from_route,
            "selected_reason": s.selected_reason,
        })
    return out


def _geojson(route, stops: list[FuelStop]) -> dict:
    features = [{
        "type": "Feature",
        "properties": {"kind": "route",
                       "distance_miles": round(route.total_miles, 2)},
        "geometry": {"type": "LineString", "coordinates": route.coordinates},
    }]
    for s in stops:
        st = s.station
        features.append({
            "type": "Feature",
            "properties": {
                "kind": "fuel_stop",
                "stop_number": s.stop_number,
                "truck_stop": st["name"],
                "city": st["city"],
                "state": st["state"],
                "fuel_price": s.fuel_price,
                "distance_from_start": s.distance_from_start,
            },
            "geometry": {"type": "Point",
                         "coordinates": [st["longitude"], st["latitude"]]},
        })
    return {"type": "FeatureCollection", "features": features}


def _build_response(result: route_service.RouteResult, request: Request) -> dict:
    cfg = settings.FUEL_ROUTE
    route = result.route

    # Fuel optimization (DB-only station queries) + cost calculation.
    stops = fuel_optimizer.optimize_fuel_stops(route)
    costing = fuel_cost_service.calculate(route.total_miles, stops)

    map_url = (
        f"{reverse('fuelroute:route-map')}"
        f"?start={request.query_params.get('start', result.start.label)}"
        f"&finish={request.query_params.get('finish', result.finish.label)}"
    )

    return {
        # --- existing route-generation fields (unchanged) ----------------
        "start": {"query": result.start.label, "label": result.start.label,
                  "latitude": result.start.lat, "longitude": result.start.lon},
        "finish": {"query": result.finish.label, "label": result.finish.label,
                   "latitude": result.finish.lat, "longitude": result.finish.lon},
        "route": {
            "total_distance_miles": round(route.total_miles, 2),
            "estimated_duration_hours": round(route.duration_seconds / 3600.0, 2),
            "geometry": {"type": "LineString", "coordinates": route.coordinates},
        },
        # --- new: fuel optimization + cost -------------------------------
        "vehicle": {
            "max_range_miles": cfg["VEHICLE_RANGE_MILES"],
            "fuel_efficiency_mpg": cfg["MILES_PER_GALLON"],
        },
        "fuel_summary": {
            "fuel_required_gallons": costing.fuel_required_gallons,
            "fuel_stops_required": costing.fuel_stops_required,
            "estimated_total_cost": costing.estimated_total_cost,
        },
        "fuel_stops": _fuel_stops_payload(stops, costing),
        "cost_breakdown": [
            {"stop_number": c.stop_number, "gallons": c.gallons,
             "price": c.price, "cost": c.cost}
            for c in costing.cost_breakdown
        ],
        # --- retained bonus: renderable map ------------------------------
        "map": {
            "geojson": _geojson(route, stops),
            "html_map_url": request.build_absolute_uri(map_url),
        },
    }


@api_view(["GET", "POST"])
def route_plan(request: Request):
    """Plan a fuel-optimal route between two US locations.

    GET  /api/route/?start=Denver,CO&finish=Chicago,IL
    POST /api/route/   {"start": "...", "finish": "..."}
    """
    data = request.data if request.method == "POST" else request.query_params
    serializer = RouteRequestSerializer(data=data)
    serializer.is_valid(raise_exception=True)

    try:
        result = route_service.generate_route(
            serializer.validated_data["start"],
            serializer.validated_data["finish"],
        )
    except GeocodingError as exc:
        return Response({"error": "geocoding_failed", "detail": str(exc)},
                        status=status.HTTP_400_BAD_REQUEST)
    except osrm.RoutingError as exc:
        return Response({"error": "routing_failed", "detail": str(exc)},
                        status=status.HTTP_502_BAD_GATEWAY)

    return Response(_build_response(result, request))


def route_map(request):
    """Render an interactive Leaflet map of the plan (handy for the demo)."""
    start = request.GET.get("start")
    finish = request.GET.get("finish")
    if not start or not finish:
        return render(request, "fuelroute/map.html",
                      {"error": "Provide ?start= and ?finish= query params."})
    try:
        result = route_service.generate_route(start, finish)
    except (GeocodingError, osrm.RoutingError) as exc:
        return render(request, "fuelroute/map.html", {"error": str(exc)})

    route = result.route
    stops = fuel_optimizer.optimize_fuel_stops(route)
    costing = fuel_cost_service.calculate(route.total_miles, stops)
    return render(request, "fuelroute/map.html", {
        "geojson": _geojson(route, stops),
        "summary": {
            "start": result.start.label,
            "finish": result.finish.label,
            "distance": round(route.total_miles, 2),
            "gallons": costing.fuel_required_gallons,
            "cost": costing.estimated_total_cost,
            "stops": len(stops),
        },
    })
