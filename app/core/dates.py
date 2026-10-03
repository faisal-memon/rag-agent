"""Timezone-aware natural-language date resolution shared by public tools."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

import dateparser

PACIFIC_TIME = ZoneInfo("America/Los_Angeles")


def resolve_date(value: str | None, *, now: datetime | None = None, timezone: ZoneInfo = PACIFIC_TIME) -> date:
    """Resolve a user date phrase relative to a timezone-aware current date.

    Empty values mean today. Ambiguous or unparseable phrases raise ValueError.
    """
    reference = (now or datetime.now(timezone)).astimezone(timezone)
    phrase = (value or "today").strip()
    if not phrase:
        return reference.date()
    weekday_match = re.fullmatch(r"(?:(next)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+(next\s+week))?", phrase, re.IGNORECASE)
    if weekday_match:
        weekday = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].index(weekday_match.group(2).lower())
        days = (weekday - reference.weekday()) % 7
        if days == 0 or weekday_match.group(1) or weekday_match.group(3):
            days += 7
        return reference.date() + timedelta(days=days)

    parsed = dateparser.parse(
        phrase,
        settings={
            "RELATIVE_BASE": reference,
            "TIMEZONE": str(timezone),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "STRICT_PARSING": True,
        },
        languages=["en"],
    )
    if parsed is None:
        raise ValueError(f"Could not resolve date phrase: {value}")
    return parsed.astimezone(timezone).date()
