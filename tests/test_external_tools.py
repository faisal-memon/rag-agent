import json
import unittest
from datetime import date

from app.agent.external_tools import _mindbody_date_range, _mindbody_request_body, parse_mindbody_schedule


class OceanScheduleTest(unittest.TestCase):
    def test_date_range_uses_pacific_calendar_day(self) -> None:
        self.assertEqual(
            ("2026-09-28T07:00:00.000Z", "2026-09-29T06:59:59.999Z"),
            _mindbody_date_range("2026-09-28"),
        )

    def test_request_body_contains_date_arguments(self) -> None:
        body, boundary = _mindbody_request_body("2026-09-28")

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
                "start_time": "2026-09-28T16:00:00.0000000Z",
                "end_time": "2026-09-28T17:00:00.0000000Z",
                "teacher": "Avery",
                "bookable": True,
                "waitlistable": False,
            }],
            schedule["classes"],
        )

    def test_parse_schedule_rejects_payload_without_classes(self) -> None:
        with self.assertRaisesRegex(ValueError, "did not include"):
            parse_mindbody_schedule('0:["$@1"]', date.today().isoformat())
