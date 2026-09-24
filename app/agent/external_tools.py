"""Read-only tools that fetch data outside the document corpus."""
from __future__ import annotations

import json
import os
from datetime import date
from urllib.request import Request, urlopen
from urllib.parse import urlparse

OCEAN_SCHEDULE_PAGE = "https://yogaflowsf.com/yoga-on-ocean-avenue/"
_ALLOWED_HOSTS = {"yogaflowsf.com", "www.yogaflowsf.com"}


def parse_ocean_schedule(payload: object, requested_date: str) -> dict:
    """Normalize a provider JSON payload into the agent's schedule contract."""
    if not isinstance(payload, dict) or not isinstance(payload.get("classes"), list):
        raise ValueError("schedule provider response must contain a classes list")
    classes = []
    for item in payload["classes"]:
        if not isinstance(item, dict) or not item.get("name") or not item.get("start_time"):
            continue
        classes.append({
            "name": str(item["name"]),
            "start_time": str(item["start_time"]),
            "teacher": str(item["teacher"]) if item.get("teacher") else None,
            "booking_url": str(item["booking_url"]) if item.get("booking_url") else None,
        })
    return {"studio": "Ocean", "date": requested_date, "classes": classes}


def get_ocean_schedule(day: str | None = None) -> dict:
    """Get Yoga Flow SF Ocean Avenue classes for a date.

    The provider endpoint is configured separately because the public page
    delegates its schedule to a booking widget. No credentials are used.
    """
    requested_date = day or date.today().isoformat()
    endpoint = os.environ.get("YOGA_FLOW_OCEAN_SCHEDULE_URL")
    if not endpoint:
        raise RuntimeError("YOGA_FLOW_OCEAN_SCHEDULE_URL is not configured")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ValueError("schedule endpoint must use HTTPS and belong to yogaflowsf.com")
    request = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "rag-agent/0.1"})
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    return parse_ocean_schedule(payload, requested_date)
