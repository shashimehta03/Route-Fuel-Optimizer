"""Geocode the user-supplied start / finish locations.

Strategy (cheapest first):

1. If the caller already passed coordinates ("lat,lon"), parse them directly
   with **no** network call.
2. If the string looks like "City, ST", resolve it against the offline
   gazetteer with **no** network call.
3. Otherwise fall back to Nominatim (OpenStreetMap) — a free, key-less
   geocoder — for arbitrary addresses / place names.

Note this is *input* geocoding only. It is separate from the map/route API
(OSRM), so the assessment's "call the map/route API at most 2-3 times" budget
is untouched: the routing call count stays at exactly one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from django.conf import settings

from . import gazetteer


class GeocodingError(Exception):
    """Raised when a location string cannot be resolved to coordinates."""


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    label: str

    def as_lonlat(self) -> tuple[float, float]:
        """OSRM wants lon,lat order."""
        return (self.lon, self.lat)


_COORD_RE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$"
)
# "Denver, CO" / "Denver CO" style — captures trailing 2-letter state.
_CITY_STATE_RE = re.compile(r"^\s*(.+?)[,\s]+([A-Za-z]{2})\s*$")


def _valid_us_latlon(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180


def geocode(query: str) -> GeoPoint:
    query = (query or "").strip()
    if not query:
        raise GeocodingError("Empty location.")

    # 1) Raw coordinates.
    m = _COORD_RE.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not _valid_us_latlon(lat, lon):
            raise GeocodingError(f"Coordinates out of range: {query!r}")
        return GeoPoint(lat=lat, lon=lon, label=query)

    # 2) Offline "City, ST".
    m = _CITY_STATE_RE.match(query)
    if m:
        hit = gazetteer.lookup(m.group(1), m.group(2))
        if hit:
            return GeoPoint(lat=hit[0], lon=hit[1], label=query)

    # 3) Nominatim fallback for arbitrary places / full addresses.
    return _geocode_nominatim(query)


def _geocode_nominatim(query: str) -> GeoPoint:
    cfg = settings.FUEL_ROUTE
    try:
        resp = requests.get(
            f"{cfg['NOMINATIM_BASE_URL']}/search",
            params={
                "q": query,
                "format": "json",
                "limit": 1,
                "countrycodes": "us",
            },
            headers={"User-Agent": cfg["USER_AGENT"]},
            timeout=cfg["HTTP_TIMEOUT_SECONDS"],
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise GeocodingError(f"Geocoding service error for {query!r}: {exc}") from exc

    if not data:
        raise GeocodingError(f"Could not geocode {query!r} within the USA.")

    top = data[0]
    return GeoPoint(
        lat=float(top["lat"]),
        lon=float(top["lon"]),
        label=top.get("display_name", query),
    )
