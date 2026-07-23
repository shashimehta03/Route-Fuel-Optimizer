"""API views for the Fuel Route planner."""
from __future__ import annotations

from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from .serializers import RouteRequestSerializer
from .services import osrm
from .services.geocoding import GeocodingError
from .services.planner import PlanningError, TripPlan, plan_trip


def _geojson(plan: TripPlan) -> dict:
    """FeatureCollection: the route line + a marker per fuel stop."""
    features = [{
        "type": "Feature",
        "properties": {"kind": "route",
                       "distance_miles": plan.total_distance_miles},
        "geometry": {"type": "LineString", "coordinates": plan.route_coordinates},
    }]
    for i, stop in enumerate(plan.fuel_stops, start=1):
        s = stop.station
        features.append({
            "type": "Feature",
            "properties": {
                "kind": "fuel_stop",
                "order": i,
                "name": s["name"],
                "city": s["city"],
                "state": s["state"],
                "price_per_gallon": stop.price,
                "gallons": stop.gallons,
                "cost": stop.cost,
                "route_mile": stop.route_mile,
            },
            "geometry": {"type": "Point",
                         "coordinates": [s["longitude"], s["latitude"]]},
        })
    return {"type": "FeatureCollection", "features": features}


def _serialize(plan: TripPlan, request: Request) -> dict:
    cfg = settings.FUEL_ROUTE
    stops = [{
        "order": i,
        "station": {
            "name": s.station["name"],
            "address": s.station["address"],
            "city": s.station["city"],
            "state": s.station["state"],
            "opis_id": s.station["opis_id"],
            "latitude": s.station["latitude"],
            "longitude": s.station["longitude"],
        },
        "route_mile": s.route_mile,
        "detour_miles": s.detour_miles,
        "price_per_gallon": s.price,
        "gallons_purchased": s.gallons,
        "cost": s.cost,
    } for i, s in enumerate(plan.fuel_stops, start=1)]

    map_url = (
        f"{reverse('fuelroute:route-map')}"
        f"?start={request.query_params.get('start', plan.start['query'])}"
        f"&finish={request.query_params.get('finish', plan.finish['query'])}"
    )

    return {
        "start": plan.start,
        "finish": plan.finish,
        "route": {
            "total_distance_miles": plan.total_distance_miles,
            "estimated_duration_hours": plan.duration_hours,
            "geometry": {
                "type": "LineString",
                "coordinates": plan.route_coordinates,
            },
        },
        "fuel": {
            "vehicle_range_miles": cfg["VEHICLE_RANGE_MILES"],
            "miles_per_gallon": cfg["MILES_PER_GALLON"],
            "total_gallons": plan.total_gallons,
            "total_cost_usd": plan.total_fuel_cost,
            "number_of_stops": len(stops),
            "stops": stops,
        },
        "map": {
            "geojson": _geojson(plan),
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
        plan = plan_trip(serializer.validated_data["start"],
                         serializer.validated_data["finish"])
    except GeocodingError as exc:
        return Response({"error": "geocoding_failed", "detail": str(exc)},
                        status=status.HTTP_400_BAD_REQUEST)
    except osrm.RoutingError as exc:
        return Response({"error": "routing_failed", "detail": str(exc)},
                        status=status.HTTP_502_BAD_GATEWAY)
    except PlanningError as exc:
        return Response({"error": "planning_failed", "detail": str(exc)},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    return Response(_serialize(plan, request))


def route_map(request):
    """Render an interactive Leaflet map of the plan (handy for the demo)."""
    start = request.GET.get("start")
    finish = request.GET.get("finish")
    if not start or not finish:
        return render(request, "fuelroute/map.html",
                      {"error": "Provide ?start= and ?finish= query params."})
    try:
        plan = plan_trip(start, finish)
    except (GeocodingError, osrm.RoutingError, PlanningError) as exc:
        return render(request, "fuelroute/map.html", {"error": str(exc)})

    return render(request, "fuelroute/map.html", {
        "geojson": _geojson(plan),
        "summary": {
            "start": plan.start["label"],
            "finish": plan.finish["label"],
            "distance": plan.total_distance_miles,
            "gallons": plan.total_gallons,
            "cost": plan.total_fuel_cost,
            "stops": len(plan.fuel_stops),
        },
    })
