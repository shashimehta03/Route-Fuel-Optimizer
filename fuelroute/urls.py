from django.urls import path

from . import views

app_name = "fuelroute"

urlpatterns = [
    path("route/", views.route_plan, name="route-plan"),
    path("route/map/", views.route_map, name="route-map"),
]
