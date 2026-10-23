"""Read-only weather forecasts from Open-Meteo."""
from __future__ import annotations

import json
from datetime import date, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
SAN_FRANCISCO = (37.7749, -122.4194)
_WEATHER_CODES = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "depositing rime fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "slight rain", 63: "rain", 65: "heavy rain", 71: "slight snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "heavy rain showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def get_weather(day: str | None = None) -> dict:
    """Get the San Francisco forecast for a date.

    Args:
        day: Optional local calendar date in YYYY-MM-DD format. Defaults to today in Pacific time.
    """
    requested = date.fromisoformat(day) if day else datetime.now(PACIFIC_TIME).date()
    params = urlencode({
        "latitude": SAN_FRANCISCO[0], "longitude": SAN_FRANCISCO[1], "timezone": "America/Los_Angeles",
        "start_date": requested.isoformat(), "end_date": requested.isoformat(),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,sunrise,sunset",
        "temperature_unit": "fahrenheit", "precipitation_unit": "inch",
    })
    request = Request(f"{OPEN_METEO_URL}?{params}", headers={"User-Agent": "rag-agent/0.1"})
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    daily = payload.get("daily") or {}
    if not daily.get("time"):
        raise ValueError("Weather service returned no forecast for the requested date")
    return {
        "location": "San Francisco, CA",
        "date": daily["time"][0],
        "condition": _WEATHER_CODES.get(daily["weather_code"][0], "unknown conditions"),
        "temperature_high_f": daily["temperature_2m_max"][0],
        "temperature_low_f": daily["temperature_2m_min"][0],
        "precipitation_probability_percent": daily["precipitation_probability_max"][0],
        "precipitation_inches": daily["precipitation_sum"][0],
        "sunrise": daily["sunrise"][0],
        "sunset": daily["sunset"][0],
        "source": "Open-Meteo",
    }
