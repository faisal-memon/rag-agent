"""Read-only tool for the public SFUSD K-12 lunch menu."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Iterable
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from liteparse import LiteParse

SFUSD_MENUS_URL = "https://www.sfusd.edu/services/health-wellness/nutrition-school-meals/menus"
PUBLIC_USER_AGENT = "Mozilla/5.0 (compatible; rag-agent/0.1)"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
MONTH_NAMES = {name.upper(): number for number, name in enumerate(calendar.month_name) if name}
MENU_REFRESH_INTERVAL = timedelta(hours=6)
_menu_page_cache: tuple[datetime, str] | None = None
_menu_pdf_cache: dict[str, tuple[datetime, bytes]] = {}


@dataclass(frozen=True)
class _TextItem:
    text: str
    x: float
    y: float


def get_school_lunch(day: str | None = None) -> dict:
    """Get the publicly listed K-12 school lunch choices for a date.

    Args:
        day: Optional local calendar date in YYYY-MM-DD format. Defaults to today.
    """
    requested_day = _parse_day(day)
    menu = _parse_lunch_pdf(_download_menu_pdf(requested_day), requested_day)
    return menu


def _download_menu_pdf(requested_day: date) -> bytes:
    """Download the currently published LunchMaster menu for the requested month."""
    menu_url = _resolve_lunchmaster_menu_url(requested_day)
    now = datetime.now(timezone.utc)
    cached = _menu_pdf_cache.get(menu_url)
    if cached is not None and now - cached[0] < MENU_REFRESH_INTERVAL:
        return cached[1]
    request = Request(_google_drive_download_url(menu_url), headers={"User-Agent": PUBLIC_USER_AGENT})
    with urlopen(request, timeout=20) as response:
        pdf = response.read()
    _menu_pdf_cache[menu_url] = (now, pdf)
    return pdf


def _resolve_lunchmaster_menu_url(requested_day: date) -> str:
    """Find the public LunchMaster hot/cold PDF for a calendar month."""
    menu_page = _download_menu_page()
    month_name = calendar.month_name[requested_day.month]
    pattern = re.compile(
        r"Breakfast\s*&amp;\s*Lunch\s*\(Hot/Cold\)(?P<links>.*?)(?:</p>|<p>)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for row in pattern.finditer(menu_page):
        if "LunchMaster" not in menu_page[max(0, row.start() - 2_000) : row.end() + 2_000]:
            continue
        links = re.findall(r'<a\s+href="(?P<url>[^"]+)"[^>]*>\s*(?P<label>[^<]+)\s*</a>', row.group("links"), flags=re.IGNORECASE)
        for url, label in links:
            if label.strip().casefold() == month_name.casefold():
                return url
    raise ValueError(f"SFUSD has not published a LunchMaster hot/cold menu for {month_name}")


def _download_menu_page() -> str:
    """Refresh the public SFUSD menu index periodically."""
    global _menu_page_cache
    now = datetime.now(timezone.utc)
    if _menu_page_cache is not None and now - _menu_page_cache[0] < MENU_REFRESH_INTERVAL:
        return _menu_page_cache[1]
    request = Request(SFUSD_MENUS_URL, headers={"User-Agent": PUBLIC_USER_AGENT})
    with urlopen(request, timeout=20) as response:
        page = response.read().decode("utf-8")
    _menu_page_cache = (now, page)
    return page


def _google_drive_download_url(view_url: str) -> str:
    """Turn SFUSD's public Google Drive viewer link into a direct PDF download."""
    parsed = urlparse(view_url)
    match = re.search(r"/file/d/(?P<id>[^/]+)", parsed.path)
    file_id = match.group("id") if match else parse_qs(parsed.query).get("id", [None])[0]
    if file_id is None:
        raise ValueError("SFUSD menu link did not contain a Google Drive file ID")
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"


def _parse_day(day: str | None) -> date:
    if day is None:
        return datetime.now().astimezone().date()
    try:
        return date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError("day must use YYYY-MM-DD") from exc


def _parse_lunch_pdf(pdf: bytes, requested_day: date) -> dict:
    """OCR a menu PDF and normalize the K-12 lunch calendar for one date."""
    page_text, items = _extract_lunch_page(pdf)
    return parse_school_lunch(items, page_text, requested_day)


@lru_cache(maxsize=2)
def _extract_lunch_page(pdf: bytes) -> tuple[str, tuple[_TextItem, ...]]:
    """OCR each distinct menu PDF once, then reuse its extracted calendar."""
    parsed = LiteParse(ocr_enabled=True, output_format="text").parse(pdf)
    lunch_page = next((page for page in parsed.pages if "K-12 LUNCH" in page.text.upper()), None)
    if lunch_page is None:
        raise ValueError("School menu PDF did not include a K-12 lunch page")
    items = tuple(_TextItem(text=item.text, x=item.x, y=item.y) for item in lunch_page.text_items)
    return lunch_page.text, items


def parse_school_lunch(items: Iterable[_TextItem], page_text: str, requested_day: date) -> dict:
    """Normalize OCR text items from one K-12 lunch calendar page.

    This is deliberately independent of LiteParse so that fixtures can test the
    calendar interpretation without downloading or OCRing a PDF.
    """
    menu_month = _menu_month(page_text)
    if menu_month != requested_day.month:
        return _no_lunch(
            requested_day,
            f"The published menu covers {calendar.month_name[menu_month]}, not {requested_day.strftime('%B')}",
        )

    page_items = list(items)
    column_starts = _column_starts(page_items)
    anchors = _date_anchors(page_items, column_starts)
    target = next((anchor for anchor in anchors if anchor.day == requested_day.day), None)
    if target is None:
        return _no_lunch(requested_day, "No school lunch is listed for this date")

    choices = _choices_for_anchor(page_items, anchors, column_starts, target)
    if not choices or any(choice.upper() == "HOLIDAY" for choice in choices):
        return _no_lunch(requested_day, "No school lunch is listed for this date")
    return {
        "date": requested_day.isoformat(),
        "school_lunch_available": True,
        "choices": choices,
        "source": "SFUSD K-12 lunch menu",
    }


@dataclass(frozen=True)
class _DateAnchor:
    day: int
    column: int
    y: float
    leading_choice: str | None = None


def _menu_month(page_text: str) -> int:
    for name, number in MONTH_NAMES.items():
        if re.search(rf"\b{name}\b", page_text.upper()):
            return number
    raise ValueError("School lunch menu did not include a month")


def _column_starts(items: list[_TextItem]) -> list[float]:
    headers = {item.text.strip().casefold(): item.x for item in items if item.text.strip().casefold() in WEEKDAYS}
    if any(day not in headers for day in WEEKDAYS):
        raise ValueError("School lunch menu did not include weekday columns")
    return [headers[day] - 32 for day in WEEKDAYS]


def _date_anchors(items: list[_TextItem], column_starts: list[float]) -> list[_DateAnchor]:
    anchors: list[_DateAnchor] = []
    for item in items:
        if item.y < 80:
            continue
        column = _column_for_date_marker(item.x, column_starts)
        if column is None:
            continue
        match = re.match(r"^(?P<day>[1-9]|[12][0-9]|3[01])(?:\s+(?P<choice>.+))?$", item.text.strip())
        if match is None:
            continue
        anchors.append(
            _DateAnchor(
                day=int(match.group("day")),
                column=column,
                y=item.y,
                leading_choice=match.group("choice"),
            )
        )
    return sorted(anchors, key=lambda anchor: (anchor.y, anchor.column))


def _column_for_date_marker(x: float, column_starts: list[float]) -> int | None:
    for index, start in enumerate(column_starts):
        if start - 8 <= x <= start + 12:
            return index
    return None


def _choices_for_anchor(
    items: list[_TextItem], anchors: list[_DateAnchor], column_starts: list[float], target: _DateAnchor
) -> list[str]:
    row_starts = sorted({round(anchor.y, 1) for anchor in anchors})
    row_end = next((value for value in row_starts if value > target.y + 1), 410.0)
    left = column_starts[target.column]
    right = column_starts[target.column + 1] if target.column + 1 < len(column_starts) else float("inf")
    lines = [(target.y, target.leading_choice)] if target.leading_choice else []
    for item in items:
        if not (left + 20 <= item.x < right and target.y - 2 <= item.y < row_end - 2):
            continue
        text = _clean_ocr_text(item.text)
        if text:
            lines.append((item.y, text))
    return _join_wrapped_lines(lines)


def _clean_ocr_text(text: str) -> str:
    cleaned = text.strip()
    if re.fullmatch(r"(?:sig|slg|se|wl|pd|x2|=2|2|22)", cleaned, flags=re.IGNORECASE):
        return ""
    cleaned = re.sub(r"\s+(?:sig|slg|se|wl|pd|x2|=2|2|22)$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _join_wrapped_lines(lines: list[tuple[float, str | None]]) -> list[str]:
    choices: list[str] = []
    previous_y: float | None = None
    for y, text in sorted((line for line in lines if line[1]), key=lambda line: line[0]):
        assert text is not None
        if previous_y is not None and y - previous_y <= 12:
            choices[-1] = f"{choices[-1]} {text}"
        else:
            choices.append(text)
        previous_y = y
    return choices


def _no_lunch(requested_day: date, reason: str) -> dict:
    return {
        "date": requested_day.isoformat(),
        "school_lunch_available": False,
        "choices": [],
        "reason": reason,
        "source": "SFUSD K-12 lunch menu",
    }
