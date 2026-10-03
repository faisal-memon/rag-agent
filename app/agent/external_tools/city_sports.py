"""Read-only City Sports Club schedules and facility hours."""
from __future__ import annotations

import re
from datetime import date, datetime
from html.parser import HTMLParser
from html import unescape
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CITY_SPORTS_SCHEDULE_URL = "https://citysportsfitness.com/Pages/ClassSchedulePrintVersion.aspx?clubid=914"
PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
_WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


class _ScheduleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_schedule = False
        self.in_hours = False
        self.in_row = False
        self.in_cell = False
        self.current: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []
        self.hours: list[str] = []
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table" and attrs_dict.get("id") == "tblSchedule":
            self.in_schedule = True
        if tag == "table" and self.in_hours:
            self.in_hours = True
        if tag == "tr" and self.in_schedule:
            self.in_row, self.row = True, []
        if tag in {"td", "th"} and self.in_row:
            self.in_cell, self.current = True, []
        if tag == "br" and self.in_cell:
            self.current.append("\n")
        self._tag_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.in_cell:
            self.row.append(re.sub(r"[ \t\r\f\v]+", " ", "".join(self.current)).strip())
            self.in_cell = False
        if tag == "tr" and self.in_row:
            self.rows.append(self.row)
            self.in_row = False
        if tag == "table" and self.in_schedule:
            self.in_schedule = False
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current.append(data)


def get_city_sports_schedule(day: str | None = None) -> dict:
    """Get City Sports Club 20th Avenue classes and opening hours for a date.

    Args:
        day: Optional local calendar date in YYYY-MM-DD format. Defaults to today in San Francisco.
    """
    requested = date.fromisoformat(day) if day else datetime.now(PACIFIC_TIME).date()
    request = Request(CITY_SPORTS_SCHEDULE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; rag-agent/0.1)"})
    with urlopen(request, timeout=15) as response:
        html = response.read().decode("utf-8")
    return parse_city_sports_schedule(html, requested)


def parse_city_sports_schedule(html: str, requested: date) -> dict:
    parser = _ScheduleParser()
    parser.feed(html)
    weekday = (requested.weekday() + 1) % 7
    classes = []
    for row in parser.rows:
        if len(row) < 8 or not re.fullmatch(r"\d{1,2}:\d{2} [AP]M", row[0], re.I):
            continue
        for cell in re.split(r"\s*\n\s*", row[weekday + 1]):
            match = re.match(r"(?P<name>.+?)\s*\((?P<instructor>[^()]*)\)$", cell)
            if not match or not match.group("name"):
                continue
            classes.append({"name": match.group("name").strip(), "time": row[0], "instructor": match.group("instructor").strip()})
    return {
        "club": "City Sports Club - San Francisco 20th Avenue",
        "date": requested.isoformat(),
        "weekday": _WEEKDAYS[weekday],
        "classes": classes,
        "hours": _hours_for_weekday(html, weekday),
        "source": CITY_SPORTS_SCHEDULE_URL,
    }


def _hours_for_weekday(html: str, weekday: int) -> str | None:
    """Extract the location hours for the requested weekday from the page."""
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    ranges = {
        label.lower(): value
        for label, value in re.findall(
            r"(Monday\s*-\s*Thursday|Friday|Saturday\s*-\s*Sunday)\s+"
            r"(\d{1,2}:\d{2}\s*[ap]m\s*-\s*\d{1,2}:\d{2}\s*[ap]m)",
            text,
            re.IGNORECASE,
        )
    }
    if weekday == 5:
        return ranges.get("friday")
    if weekday in {0, 6}:
        return ranges.get("saturday - sunday")
    return ranges.get("monday - thursday")
