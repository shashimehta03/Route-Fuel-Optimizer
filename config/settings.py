"""
Django settings for the Fuel Route API project.

Kept intentionally small: SQLite by default (no external services required),
DRF for the API layer, and a single project app ``fuelroute``.
"""
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Security -------------------------------------------------------------
# For a take-home / local demo we read from the environment but fall back to
# safe local defaults so the project runs with zero configuration.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-insecure-key-change-me-in-production",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# --- Applications ---------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "fuelroute",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database -------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

# --- Password validation --------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalization -------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static files ---------------------------------------------------------
STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# --- Fuel Route domain settings ------------------------------------------
# All tunable knobs for the routing/optimization live here so behaviour is
# easy to reason about and override via environment variables.
FUEL_ROUTE = {
    # Vehicle characteristics (from the assessment brief).
    "VEHICLE_RANGE_MILES": float(os.environ.get("VEHICLE_RANGE_MILES", 500)),
    "MILES_PER_GALLON": float(os.environ.get("MILES_PER_GALLON", 10)),
    # How far (miles) a station may sit from the route to still count as an
    # "on-route" fuel stop.
    "CORRIDOR_MILES": float(os.environ.get("CORRIDOR_MILES", 5)),
    # Search radius (miles) around each 500-mile refuel checkpoint when looking
    # for candidate stations. Assignment allows 10-20; default 15.
    "SEARCH_RADIUS_MILES": float(os.environ.get("SEARCH_RADIUS_MILES", 15)),
    # If no station falls within SEARCH_RADIUS_MILES of a checkpoint, widen the
    # search up to this cap so a stop can still be produced.
    "MAX_SEARCH_RADIUS_MILES": float(os.environ.get("MAX_SEARCH_RADIUS_MILES", 60)),
    # Free routing provider. OSRM public demo server needs no API key and
    # returns the full geometry + per-segment distances in a single call.
    "OSRM_BASE_URL": os.environ.get(
        "OSRM_BASE_URL", "https://router.project-osrm.org"
    ),
    # Free geocoder for the user-supplied start/finish strings (no key).
    "NOMINATIM_BASE_URL": os.environ.get(
        "NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org"
    ),
    "HTTP_TIMEOUT_SECONDS": float(os.environ.get("HTTP_TIMEOUT_SECONDS", 15)),
    "USER_AGENT": os.environ.get(
        "FUEL_ROUTE_USER_AGENT", "fuel-route-api/1.0 (assessment)"
    ),
}
