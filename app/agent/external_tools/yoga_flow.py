"""Read-only tools that fetch data outside the document corpus."""
from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, time, timedelta
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

MINDBODY_SCHEDULE_URL = "https://go.mindbodyonline.com/book/widgets/schedules/view/fc13411a494/schedule"
NOE_MINDBODY_SCHEDULE_URL = "https://go.mindbodyonline.com/book/widgets/schedules/view/aa147688666/schedule"
MINDBODY_NEXT_ACTION = "4f5d69414e1b758541ec223c15d6e1f87de21681"
MINDBODY_ACTION_STATE = "\"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\""
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")


def get_ocean_schedule(day: str | None = None) -> dict:
    """Get publicly listed Yoga Flow SF Ocean Avenue classes for a date.

    Args:
        day: Optional local calendar date in YYYY-MM-DD format. Defaults to today at the Ocean studio.
    """
    return _get_schedule("Ocean", MINDBODY_SCHEDULE_URL, MINDBODY_ACTION_STATE, day)


def get_noe_schedule(day: str | None = None) -> dict:
    """Get publicly listed Yoga Flow SF Noe Valley classes for a date.

    Args:
        day: Optional local calendar date in YYYY-MM-DD format. Defaults to today at the Noe studio.
    """
    return _get_schedule("Noe", NOE_MINDBODY_SCHEDULE_URL, _noe_action_state(), day)


def _get_schedule(studio: str, schedule_url: str, action_state: str, day: str | None) -> dict:
    """Fetch and normalize the public Mindbody schedule for one Yoga Flow studio."""
    requested_date = day or datetime.now(PACIFIC_TIME).date().isoformat()
    body, boundary = _mindbody_request_body(requested_date, action_state)
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
            "User-Agent": "Mozilla/5.0 (compatible; rag-agent/0.1)",
        },
    )
    with urlopen(request, timeout=15) as response:
        payload = response.read().decode("utf-8")
    return parse_mindbody_schedule(payload, requested_date, studio)


def _noe_action_state() -> str:
    """Read Noe's public page to obtain the current action state without cookies."""
    request = Request(NOE_MINDBODY_SCHEDULE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; rag-agent/0.1)"})
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
    raise ValueError("Mindbody Noe schedule page did not include a usable action state")



def _mindbody_date_range(requested_date: str) -> tuple[str, str]:
    """Return the UTC range representing one calendar day at the Ocean studio."""
    try:
        local_day = date.fromisoformat(requested_date)
    except ValueError as exc:
        raise ValueError("day must use YYYY-MM-DD") from exc

    start = datetime.combine(local_day, time.min, tzinfo=PACIFIC_TIME)
    end = start + timedelta(days=1) - timedelta(milliseconds=1)
    return (_utc_timestamp(start), _utc_timestamp(end))


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(ZoneInfo("UTC")).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mindbody_request_body(requested_date: str, action_state: str = MINDBODY_ACTION_STATE) -> tuple[bytes, str]:
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
