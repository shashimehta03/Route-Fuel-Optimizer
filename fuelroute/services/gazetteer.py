"""Offline (city, state) -> (latitude, longitude) lookup.

Built from the ``zipcodes`` package, which bundles the full US zip-code table
(~42k rows) with coordinates. We aggregate every zip to a city centroid, so a
lookup needs **no network access** and is effectively instant.

This is used in two places:

* ``load_stations`` management command — to geocode all ~8k stations once.
* input geocoding fallback — when a user passes "City, ST" we can resolve it
  without hitting an external geocoder at all.

Coverage against the assessment's price file is 100% of US stations
(non-US rows, e.g. Canadian provinces, are intentionally not matched).
"""
from __future__ import annotations

import re
from functools import lru_cache
from statistics import mean

import zipcodes


def _norm(value: str) -> str:
    """Uppercase and strip everything but letters/digits.

    Lets "De Forest" match "DeForest", "St. Louis" match "St Louis", etc.
    """
    return re.sub(r"[^A-Z0-9]", "", value.upper())


@lru_cache(maxsize=1)
def _index() -> dict[tuple[str, str], tuple[float, float]]:
    """Build and cache the (normalized_city, state) -> centroid index."""
    buckets: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in zipcodes.list_all():
        try:
            lat = float(row["lat"])
            lon = float(row["long"])
        except (TypeError, ValueError, KeyError):
            continue
        key = (_norm(row["city"]), row["state"])
        buckets.setdefault(key, []).append((lat, lon))

    return {
        key: (mean(p[0] for p in pts), mean(p[1] for p in pts))
        for key, pts in buckets.items()
    }


def lookup(city: str, state: str) -> tuple[float, float] | None:
    """Return the (lat, lon) centroid for a US city/state, or None."""
    if not city or not state:
        return None
    return _index().get((_norm(city), state.strip().upper()))
