"""Read-only tools that fetch Yoga Flow SF schedules."""
from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, time, timedelta
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

OCEAN_MINDBODY_SCHEDULE_URL = "https://go.mindbodyonline.com/book/widgets/schedules/view/fc13411a494/schedule"
NOE_MINDBODY_SCHEDULE_URL = "https://go.mindbodyonline.com/book/widgets/schedules/view/aa147688666/schedule"
MINDBODY_NEXT_ACTION = "4f5d69414e1b758541ec223c15d6e1f87de21681"
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
PUBLIC_USER_AGENT = "Mozilla/5.0 (compatible; rag-agent/0.1)"
UPCOMING_CLASS_LIMIT = 6


def get_ocean_schedule(day: str | None = None) -> dict:
    """Get publicly listed Yoga Flow SF Ocean Avenue classes for a date.

    Args:
        day: Optional local calendar date in YYYY-MM-DD format. Defaults to today at the Ocean studio.
    """
    return _get_schedule("Ocean", OCEAN_MINDBODY_SCHEDULE_URL, day)


def get_noe_schedule(day: str | None = None) -> dict:
    """Get publicly listed Yoga Flow SF Noe Valley classes for a date.

    Args:
        day: Optional local calendar date in YYYY-MM-DD format. Defaults to today at the Noe studio.
    """
    return _get_schedule("Noe", NOE_MINDBODY_SCHEDULE_URL, day)


def get_upcoming_yoga_classes() -> dict:
    """Get the next upcoming Yoga Flow SF classes at both Ocean and Noe Valley.

    Use when the user asks for upcoming Yoga Flow classes without naming a studio.
    Returns the next few classes in time order, with the studio named on every class.
    """
    now = datetime.now(PACIFIC_TIME)
    schedules = [get_ocean_schedule(now.date().isoformat()), get_noe_schedule(now.date().isoformat())]
    classes = _select_upcoming_classes(schedules, now)
    if len(classes) < UPCOMING_CLASS_LIMIT:
        tomorrow = (now + timedelta(days=1)).date().isoformat()
        schedules.extend((get_ocean_schedule(tomorrow), get_noe_schedule(tomorrow)))
        classes = _select_upcoming_classes(schedules, now)
    return {
        "studios": ["Ocean", "Noe"],
        "as_of": now.isoformat(timespec="minutes"),
        "classes": classes[:UPCOMING_CLASS_LIMIT],
    }


def _get_schedule(studio: str, schedule_url: str, day: str | None) -> dict:
    """Fetch and normalize the public Mindbody schedule for one Yoga Flow studio."""
    requested_date = day or datetime.now(PACIFIC_TIME).date().isoformat()
    body, boundary = _mindbody_request_body(requested_date, _mindbody_action_state(schedule_url))
    request = Request(
        schedule_url,
        data=body,
        method="POST",
        headers={
            "Accept": "text/x-component",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Next-Action": MINDBODY_NEXT_ACTION,
            "Origin": "https://go.mindbodyonline.com",
            "Referer": schedule_url,
            "User-Agent": PUBLIC_USER_AGENT,
        },
    )
    with urlopen(request, timeout=15) as response:
        payload = response.read().decode("utf-8")
    return parse_mindbody_schedule(payload, requested_date, studio)


def _select_upcoming_classes(schedules: list[dict], now: datetime) -> list[dict]:
    """Combine studio schedules, remove elapsed classes, and sort by start time."""
    upcoming = []
    for schedule in schedules:
        studio = str(schedule["studio"]).removeprefix("Yoga Flow SF - ")
        for class_ in schedule["classes"]:
            if datetime.fromisoformat(class_["start_time"]) > now:
                upcoming.append({"studio": studio, **class_})
    return sorted(upcoming, key=lambda class_: class_["start_time"])


def _mindbody_action_state(schedule_url: str) -> str:
    """Read a public widget page to obtain its current server-action state."""
    request = Request(schedule_url, headers={"User-Agent": PUBLIC_USER_AGENT})
    with urlopen(request, timeout=15) as response:
        page = response.read().decode("utf-8")
    return _extract_action_state(page)


def _extract_action_state(page: str) -> str:
    """Extract a long public server-action value from a Next.js page response."""
    chunks = []
    for match in re.finditer(r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)</script>', page):
        chunks.append(json.loads(f'"{match.group(1)}"'))
    flight = "".join(chunks)
    for match in re.finditer(r'(?P<record>[0-9a-z]+):T(?P<length>[0-9a-f]+),', flight):
        length = int(match.group("length"), 16)
        state_start = match.end()
        state = flight[state_start : state_start + length]
        if length >= 1_000 and re.fullmatch(r"[A-Za-z0-9+/=]+", state):
            return json.dumps(state)
    raise ValueError("Mindbody schedule page did not include a usable action state")


def _mindbody_date_range(requested_date: str) -> tuple[str, str]:
    """Return the UTC range representing one calendar day at a Yoga Flow studio."""
    try:
        local_day = date.fromisoformat(requested_date)
    except ValueError as exc:
        raise ValueError("day must use YYYY-MM-DD") from exc

    start = datetime.combine(local_day, time.min, tzinfo=PACIFIC_TIME)
    end = start + timedelta(days=1) - timedelta(milliseconds=1)
    return (_utc_timestamp(start), _utc_timestamp(end))


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(ZoneInfo("UTC")).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mindbody_request_body(requested_date: str, action_state: str) -> tuple[bytes, str]:
    """Build the multipart body expected by Mindbody's public schedule widget."""
    from_date, to_date = _mindbody_date_range(requested_date)
    boundary = f"----rag-agent-{secrets.token_hex(12)}"
    arguments = {"fromDate": from_date, "toDate": to_date}
    fields = (
        ("1", action_state),
        ("0", json.dumps(["$@1", arguments], separators=(",", ":"))),
    )
    lines: list[str] = []
    for name, value in fields:
        lines.extend((f"--{boundary}", f'Content-Disposition: form-data; name="{name}"', "", value))
    lines.extend((f"--{boundary}--", ""))
    return "\r\n".join(lines).encode(), boundary


def parse_mindbody_schedule(payload: str, requested_date: str, studio: str = "Ocean") -> dict:
    """Normalize a Mindbody Next.js Flight response into the agent's schedule contract."""
    classes = []
    offset = 0
    while True:
        match = re.search(r'"id"\s*:\s*"cinst_', payload[offset:])
        if match is None:
            break
        marker = offset + match.start()
        start = payload.rfind("{", 0, marker)
        item, offset = _decode_json_object(payload, start)
        if not item.get("name") or not item.get("startDateTime"):
            continue
        staff = item.get("staff") if isinstance(item.get("staff"), list) else []
        teacher = next(
            (person.get("displayLabel") for person in staff if isinstance(person, dict) and person.get("displayLabel")),
            None,
        )
        classes.append(
            {
                "name": str(item["name"]),
                "start_time": _pacific_timestamp(str(item["startDateTime"])),
                "end_time": _pacific_timestamp(str(item["endDateTime"])) if item.get("endDateTime") else None,
                "teacher": str(teacher) if teacher else None,
                "bookable": bool(item.get("bookable")),
                "waitlistable": bool(item.get("waitlistable")),
            }
        )
    if not classes:
        raise ValueError("Mindbody schedule response did not include class records")
    return {"studio": f"Yoga Flow SF - {studio}", "date": requested_date, "classes": classes}


def _pacific_timestamp(timestamp: str) -> str:
    """Convert Mindbody's UTC timestamp to the studio's local time."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(PACIFIC_TIME).isoformat(
        timespec="minutes"
    )


def _decode_json_object(payload: str, start: int) -> tuple[dict, int]:
    """Decode one balanced JSON object embedded in a Flight response."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(payload)):
        character = payload[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(payload[start : index + 1])
                if not isinstance(value, dict):
                    raise ValueError("Mindbody class record was not an object")
                return value, index + 1
    raise ValueError("Mindbody schedule response contained an incomplete class record")
