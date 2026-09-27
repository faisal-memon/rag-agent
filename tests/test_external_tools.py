import json
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.agent.external_tools.yoga_flow import (
    _extract_action_state,
    _mindbody_date_range,
    _mindbody_request_body,
    _select_upcoming_classes,
    parse_mindbody_schedule,
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
