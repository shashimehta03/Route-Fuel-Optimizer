"""In-memory, numpy-backed cache of geocoded fuel stations.

Loaded once from the database and reused across requests. ~7.5k rows is tiny,
so holding it in RAM lets us match the whole station set against a route with
vectorized numpy instead of per-station DB/geo work.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models import FuelStation


@dataclass
class StationStore:
    lats: np.ndarray          # (N,)
    lons: np.ndarray          # (N,)
    prices: np.ndarray        # (N,) retail $/gallon
    meta: list[dict]          # (N,) station detail dicts

    def __len__(self):
        return len(self.meta)


_store: StationStore | None = None


def get_store() -> StationStore:
    """Return the cached store, building it from the DB on first use."""
    global _store
    if _store is None:
        _store = _build_store()
    return _store


def reset_cache() -> None:
    """Drop the cache (used by tests after loading fixtures)."""
    global _store
    _store = None


def _build_store() -> StationStore:
    rows = (
        FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False)
        .values("id", "opis_id", "name", "address", "city", "state",
                "retail_price", "latitude", "longitude")
    )
    lats, lons, prices, meta = [], [], [], []
    for r in rows:
        lats.append(r["latitude"])
        lons.append(r["longitude"])
        prices.append(float(r["retail_price"]))
        meta.append({
            "id": r["id"],
            "opis_id": r["opis_id"],
            "name": r["name"],
            "address": r["address"],
            "city": r["city"],
            "state": r["state"],
            "price": float(r["retail_price"]),
            "latitude": r["latitude"],
            "longitude": r["longitude"],
        })
    return StationStore(
        lats=np.asarray(lats, dtype=float),
        lons=np.asarray(lons, dtype=float),
        prices=np.asarray(prices, dtype=float),
        meta=meta,
    )
