"""Read-only weather forecasts from Open-Meteo."""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.agent.config import get_api_settings

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
REQUEST_TIMEOUT_SECONDS = 2
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 0.25
SAN_FRANCISCO = (37.7749, -122.4194)
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
_WEATHER_CODES = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "depositing rime fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "slight rain", 63: "rain", 65: "heavy rain", 71: "slight snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "heavy rain showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def get_weather(day: str | None = None, location: str | None = None) -> dict:
    """Get the daily summary and upcoming hourly forecast for a date and place.

    The hourly forecast contains the place's local timestamps, conditions, temperature,
    precipitation chance, and amount. For today, past hours are omitted. An omitted
    location uses the configured home coordinates.

    Args:
        day: Optional local calendar date in YYYY-MM-DD format. Defaults to today at the place.
        location: Optional city or place name. Defaults to the configured home location.
    """
    selected_location = (location or "").strip()
    if selected_location:
        place = _geocode_location(selected_location)
        latitude = place["latitude"]
        longitude = place["longitude"]
        location_name = _format_location(place)
    else:
        settings = get_api_settings()
        if settings.home_latitude is not None and settings.home_longitude is not None:
            latitude = settings.home_latitude
            longitude = settings.home_longitude
            location_name = "Home"
        else:
            latitude, longitude = SAN FRANCISCO
            location_name = "San Francisco, CA"

    params: dict[str, str | float] = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "auto",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,sunrise,sunset",
        "hourly": "weather_code,temperature_2m,precipitation_probability,precipitation",
        "temperature_unit": "fahrenheit", "precipitation_unit": "inch",
    }
    if day:
        requested = date.fromisoformat(day)
        params["start_date"] = requested.isoformat()
        params["end_date"] = requested.isoformat()
    else:
        params["forecast_days"] = 1
    request = Request(
        f"{OPEN_METEO_URL}?{urlencode(params)}",
        headers={"User-Agent": "rag-agent/0.1"},
    )
    payload = _fetch_forecast(request)
    daily = payload.get("daily") or {}
    if not daily.get("time"):
        raise ValueError("Weather service returned no forecast for the requested date")
    place_timezone = ZoneInfo(payload.get("timezone") or "UTC")
    now = datetime.now(place_timezone)
    requested = date.fromisoformat(day) if day else date.fromisoformat(daily["time"][0])
    hourly = payload.get("hourly") or {}
    hourly_times = hourly.get("time") or []
    hourly_indexes = range(len(hourly_times))
    if requested == now.date():
        current_hour = now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
        hourly_indexes = [index for index, hour in enumerate(hourly_times) if hour >= current_hour]
    hourly_forecast = []
    for index in hourly_indexes:
        forecast_time = hourly_times[index]
        hourly_forecast.append({
            "time": forecast_time,
            "condition": _WEATHER_CODES.get(_hourly_value(hourly, "weather_code", index), "unknown conditions"),
            "temperature_f": _hourly_value(hourly, "temperature_2m", index),
            "precipitation_probability_percent": _hourly_value(hourly, "precipitation_probability", index),
            "precipitation_inches": _hourly_value(hourly, "precipitation", index),
        })
    return {
        "location": location_name,
        "date": daily["time"][0],
        "condition": _WEATHER_CODES.get(daily["weather_code"][0], "unknown conditions"),
        "temperature_high_f": daily["temperature_2m_max"][0],
        "temperature_low_f": daily["temperature_2m_min"][0],
        "precipitation_probability_percent": daily["precipitation_probability_max"][0],
        "precipitation_inches": daily["precipitation_sum"][0],
        "sunrise": daily["sunrise"][0],
        "sunset": daily["sunset"][0],
        "hourly_forecast": hourly_forecast,
        "source": "Open-Meteo",
    }


def _geocode_location(location: str) -> dict[str, Any]:
    """Resolve a user-provided place name to Open-Meteo forecast coordinates."""
    params = urlencode({"name": location, "count": 10, "language": "en", "format": "json"})
    request = Request(
        f"{OPEN_METEO_GEOCODING_URL}?{params}",
        headers={"User-Agent": "rag-agent/0.1"},
    )
    payload = _fetch_forecast(request)
    results = payload.get("results") or []
    if not results:
        raise ValueError(f"Could not find a weather location matching {location!r}")
    return results[0]


def _format_location(place: dict[str, Any]) -> str:
    """Return a concise city, region, and country label for a geocoded place."""
    parts = [place.get("name"), place.get("admin1"), place.get("country")]
    return ", ".join(dict.fromkeys(part for part in parts if part))


def _fetch_forecast(request: Request) -> dict:
    """Retry transient network and server failures with a bounded attempt count."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP_STATUSES or attempt == MAX_ATTEMPTS:
                raise
            error: Exception = exc
        except (ConnectionError, URLError, TimeoutError) as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            error = exc

        logger.warning(
            "Open-Meteo request failed on attempt %d/%d; retrying: %s",
            attempt,
            MAX_ATTEMPTS,
            error,
        )
        time.sleep(RETRY_DELAY_SECONDS * attempt)

    raise RuntimeError("Open-Meteo request failed without an error")


def _hourly_value(hourly: dict[str, Any], key: str, index: int) -> Any:
    values = hourly.get(key) or []
    return values[index] if index < len(values) else None
