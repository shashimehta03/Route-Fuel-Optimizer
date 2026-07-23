"""Load the OPIS fuel-price CSV into the database, geocoding each station.

Geocoding happens **here, once**, using the offline gazetteer — never on the
request path. Re-running the command replaces the existing station table.

Usage:
    python manage.py load_stations                      # uses data/fuel-prices.csv
    python manage.py load_stations --csv path/to/file.csv
"""
from __future__ import annotations

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuelroute.models import FuelStation
from fuelroute.services import gazetteer, stations


class Command(BaseCommand):
    help = "Load and geocode fuel stations from the OPIS price CSV."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            default=str(settings.BASE_DIR / "data" / "fuel-prices.csv"),
            help="Path to the fuel-price CSV.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv"])
        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")

        objs = []
        skipped_price = 0
        ungeocoded = 0
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                price_raw = (row.get("Retail Price") or "").strip()
                try:
                    price = round(float(price_raw), 4)
                except ValueError:
                    skipped_price += 1
                    continue

                city = (row.get("City") or "").strip()
                state = (row.get("State") or "").strip().upper()
                coords = gazetteer.lookup(city, state)
                if coords is None:
                    ungeocoded += 1
                lat, lon = coords if coords else (None, None)

                def as_int(v):
                    try:
                        return int(str(v).strip())
                    except (TypeError, ValueError):
                        return None

                objs.append(FuelStation(
                    opis_id=as_int(row.get("OPIS Truckstop ID")) or 0,
                    name=(row.get("Truckstop Name") or "").strip()[:255],
                    address=(row.get("Address") or "").strip()[:255],
                    city=city[:128],
                    state=state[:2],
                    rack_id=as_int(row.get("Rack ID")),
                    retail_price=price,
                    latitude=lat,
                    longitude=lon,
                ))

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(objs, batch_size=1000)

        stations.reset_cache()

        geocoded = len(objs) - ungeocoded
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(objs)} stations "
            f"({geocoded} geocoded, {ungeocoded} without coordinates, "
            f"{skipped_price} rows skipped for bad price)."
        ))
