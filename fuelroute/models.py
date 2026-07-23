from django.db import models


class FuelStation(models.Model):
    """A truck-stop fuel station loaded from the OPIS price file.

    Latitude / longitude are resolved once, at load time, from the station's
    city + state using an offline gazetteer (the ``zipcodes`` package). This
    means the request path never has to geocode stations and never calls an
    external service for them.
    """

    opis_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=128, db_index=True)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=7, decimal_places=4)

    # Resolved at load time. Nullable so a station that cannot be geocoded is
    # still recorded (it is simply skipped by the planner).
    latitude = models.FloatField(null=True, blank=True, db_index=True)
    longitude = models.FloatField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["latitude", "longitude"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) @ ${self.retail_price}"
