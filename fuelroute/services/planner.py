"""Deprecated module.

The fuel-planning logic has been refactored into dedicated services:

* :mod:`fuelroute.services.route_service`      - route generation (one OSRM call)
* :mod:`fuelroute.services.fuel_optimizer`     - 500-mile checkpoint stop selection
* :mod:`fuelroute.services.fuel_cost_service`  - gallons + cost calculation

This file is intentionally left as a pointer and contains no logic.
"""
