import json
import unittest
from unittest.mock import patch
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.agent.external_tools.yoga_flow import (
    _extract_action_state,
    _mindbody_date_range,
    _mindbody_request_body,
    _select_upcoming_classes,
    parse_mindbody_schedule,
)
from app.agent.external_tools.school_lunch import (
    _TextItem,
    _google_drive_download_url,
    _resolve_lunchmaster_menu_url,
    get_school_lunch,
    parse_school_lunch,
)


class OceanScheduleTest(unittest.TestCase):
    def test_date_range_uses_pacific_calendar_day(self) -> None:
        self.assertEqual(
            ("2026-09-28T07:00:00.000Z", "2026-09-29T06:59:59.999Z"),
            _mindbody_date_range("2026-09-28"),
        )

    def test_request_body_contains_date_arguments(self) -> None:
        body, boundary = _mindbody_request_body("2026-09-28", '"public-action-state"')

        self.assertIn(boundary.encode(), body)
        self.assertIn(b'"fromDate":"2026-09-28T07:00:00.000Z"', body)
        self.assertIn(b'"toDate":"2026-09-29T06:59:59.999Z"', body)

    def test_parse_schedule_normalizes_class_records(self) -> None:
        record = {
            "id": "cinst_example",
            "name": "Slow Flow - Non-Heated",
            "startDateTime": "2026-09-28T16:00:00.0000000Z",
            "endDateTime": "2026-09-28T17:00:00.0000000Z",
            "bookable": True,
            "waitlistable": False,
            "staff": [{"displayLabel": "Avery"}],
        }
        payload = '0:["$@1"]\n5:' + json.dumps([record])

        schedule = parse_mindbody_schedule(payload, "2026-09-28")

        self.assertEqual("Yoga Flow SF - Ocean", schedule["studio"])
        self.assertEqual(
            [{
                "name": "Slow Flow - Non-Heated",
                "start_time": "2026-09-28T09:00-07:00",
                "end_time": "2026-09-28T10:00-07:00",
                "teacher": "Avery",
                "bookable": True,
                "waitlistable": False,
            }],
            schedule["classes"],
        )

    def test_parse_schedule_rejects_payload_without_classes(self) -> None:
        with self.assertRaisesRegex(ValueError, "did not include"):
            parse_mindbody_schedule('0:["$@1"]', date.today().isoformat())

    def test_extract_action_state_from_public_next_page(self) -> None:
        state = "a" * 1_000
        page = f'<script>self.__next_f.push([1,"3a:T3e8,{state}"])</script>'

        self.assertEqual(json.dumps(state), _extract_action_state(page))

    def test_select_upcoming_classes_combines_and_sorts_studios(self) -> None:
        schedules = [
            {"studio": "Yoga Flow SF - Ocean", "classes": [{"name": "Earlier", "start_time": "2026-09-28T08:00-07:00"}]},
            {"studio": "Yoga Flow SF - Noe", "classes": [{"name": "Later", "start_time": "2026-09-28T10:00-07:00"}]},
            {"studio": "Yoga Flow SF - Ocean", "classes": [{"name": "Next", "start_time": "2026-09-28T09:00-07:00"}]},
        ]

        classes = _select_upcoming_classes(schedules, datetime(2026, 9, 28, 8, 30, tzinfo=ZoneInfo("America/Los_Angeles")))

        self.assertEqual(["Next", "Later"], [class_["name"] for class_ in classes])
        self.assertEqual(["Ocean", "Noe"], [class_["studio"] for class_ in classes])


class SchoolLunchTest(unittest.TestCase):
    def test_resolves_requested_month_from_lunchmaster_menu_row(self) -> None:
        menu_page = """
        <p><strong>Breakfast &amp; Lunch (Hot/Cold)</strong>
          <a href="https://drive.google.com/file/d/september/view">September</a> |
          <a href="https://drive.google.com/file/d/october/view">October</a>:
          available at sites labeled \"The LunchMaster\".</p>
        <p><strong>Breakfast &amp; Lunch (Hot/Cold)</strong>
          <a href="https://drive.google.com/file/d/other-october/view">October</a>:
          available at other schools.</p>
        """

        with patch(
            "app.agent.external_tools.school_lunch._download_menu_page", return_value=menu_page
        ):
            self.assertEqual(
                "https://drive.google.com/file/d/october/view",
                _resolve_lunchmaster_menu_url(date(2026, 10, 1)),
            )

    def test_builds_direct_download_url_from_google_drive_viewer_link(self) -> None:
        self.assertEqual(
            "https://drive.usercontent.google.com/download?id=october&export=download&confirm=t",
            _google_drive_download_url("https://drive.google.com/file/d/october/view?usp=drive_link"),
        )

    def test_reports_when_the_requested_month_has_not_been_published(self) -> None:
        menu_page = """
        <p><strong>Breakfast &amp; Lunch (Hot/Cold)</strong>
          <a href="https://drive.google.com/file/d/october/view">October</a>:
          available at sites labeled \"The LunchMaster\".</p>
        """

        with patch(
            "app.agent.external_tools.school_lunch._download_menu_page", return_value=menu_page
        ):
            lunch = get_school_lunch("2026-11-01")

        self.assertFalse(lunch["school_lunch_available"])
        self.assertIn("has not published", lunch["reason"])

    def test_parses_lunch_choices_for_a_school_day(self) -> None:
        lunch = parse_school_lunch(_school_lunch_items(), "K-12 LUNCH (HOT/COLD) SEPTEMBER", date(2026, 9, 28))

        self.assertEqual(
            {
                "date": "2026-09-28",
                "school_lunch_available": True,
                "choices": ["BBQ Cheeseburger (Beef)", "Veggie Burger", "Turkey & Cheese Sandwich"],
                "source": "SFUSD K-12 lunch menu",
            },
            lunch,
        )

    def test_reports_holiday_without_lunch(self) -> None:
        lunch = parse_school_lunch(_school_lunch_items(), "K-12 LUNCH (HOT/COLD) SEPTEMBER", date(2026, 9, 7))

        self.assertFalse(lunch["school_lunch_available"])
        self.assertEqual([], lunch["choices"])
        self.assertIn("No school lunch", lunch["reason"])

    def test_reports_when_the_menu_does_not_cover_requested_month(self) -> None:
        lunch = parse_school_lunch(_school_lunch_items(), "K-12 LUNCH (HOT/COLD) SEPTEMBER", date(2026, 10, 1))

        self.assertFalse(lunch["school_lunch_available"])
        self.assertIn("covers September", lunch["reason"])


def _school_lunch_items() -> list[_TextItem]:
    """Captured LiteParse geometry for representative public menu cells."""
    return [
        _TextItem("SEPTEMBER", 407, 41),
        _TextItem("Monday", 70, 71),
        _TextItem("Tuesday", 245, 71),
        _TextItem("Wednesday", 418, 71),
        _TextItem("Thursday", 600, 71),
        _TextItem("Friday", 769, 71),
        _TextItem("7", 38, 151),
        _TextItem("HOLIDAY", 69, 151),
        _TextItem("28", 38, 347),
        _TextItem("BBQ Cheeseburger (Beef)", 69, 347),
        _TextItem("Veggie Burger", 69, 364),
        _TextItem("x2", 140, 366),
        _TextItem("Turkey & Cheese Sandwich", 69, 380),
        _TextItem("29", 216, 347),
        _TextItem("Pesto Chicken Hoagie", 244, 347),
    ]

class CitySportsScheduleTest(unittest.TestCase):
    def test_parses_classes_and_hours_for_requested_day(self) -> None:
        from app.agent.external_tools.city_sports import _parse_city_sports_schedule

        html = """
        <table id="tblSchedule"><tr><th>Time</th><th>Sunday</th><th>Monday</th><th>Tuesday</th><th>Wednesday</th><th>Thursday</th><th>Friday</th><th>Saturday</th></tr>
        <tr><td><h5>09:45 AM</h5></td><td></td><td></td><td></td><td></td><td></td><td><strong><a>Zumba® Class</a></strong> (Cindy)<br /><strong><a>Cycle</a></strong> (Stephanie)</td><td></td></tr></table>
        <table class="standardTableCompact"><tr><th>Location Hours:</th></tr>
        <tr><th>Monday - Thursday</th><td>5:00am - 11:00pm</td></tr>
        <tr><th>Friday</th><td>5:00am - 10:00pm</td></tr>
        <tr><th>Saturday - Sunday</th><td>8:00am - 8:00pm</td></tr></table>
        """
        result = _parse_city_sports_schedule(html, date(2026, 10, 2))
        self.assertEqual("Friday", result["weekday"])
        self.assertEqual("5:00am - 10:00pm", result["hours"])
        self.assertEqual(["Zumba® Class", "Cycle"], [item["name"] for item in result["classes"]])
