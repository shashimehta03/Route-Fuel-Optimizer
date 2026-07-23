"""DRF serializers for request validation and response shaping."""
from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Validates the two inputs the API accepts.

    ``start`` / ``finish`` may be any of:
      * "City, ST"        (resolved offline)
      * "lat,lon"         (used directly, no geocoding)
      * a free-form place / address (resolved via Nominatim)
    """
    start = serializers.CharField(max_length=255, help_text="Start location (USA).")
    finish = serializers.CharField(max_length=255, help_text="Finish location (USA).")

    def validate_start(self, value):
        return value.strip()

    def validate_finish(self, value):
        return value.strip()


class _StationSerializer(serializers.Serializer):
    name = serializers.CharField()
    address = serializers.CharField(allow_blank=True)
    city = serializers.CharField()
    state = serializers.CharField()
    opis_id = serializers.IntegerField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()


class FuelStopSerializer(serializers.Serializer):
    station = _StationSerializer()
    route_mile = serializers.FloatField()
    detour_miles = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    cost = serializers.FloatField()
