from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from app.core.dates import resolve_date


PACIFIC = ZoneInfo("America/Los_Angeles")


class ResolveDateTest(TestCase):
    def test_resolves_relative_dates_in_the_callers_timezone(self) -> None:
        now = datetime(2026, 9, 30, 12, tzinfo=PACIFIC)

        self.assertEqual("2026-10-01", resolve_date("tomorrow", timezone=PACIFIC, now=now).isoformat())
        self.assertEqual("2026-10-02", resolve_date("Friday", timezone=PACIFIC, now=now).isoformat())
        self.assertEqual("2026-10-07", resolve_date("next Wednesday", timezone=PACIFIC, now=now).isoformat())

    def test_empty_date_means_today(self) -> None:
        now = datetime(2026, 9, 30, 12, tzinfo=PACIFIC)

        self.assertEqual("2026-09-30", resolve_date(None, timezone=PACIFIC, now=now).isoformat())

    def test_rejects_unparseable_phrase(self) -> None:
        with self.assertRaises(ValueError):
            resolve_date("not a date", timezone=PACIFIC)
