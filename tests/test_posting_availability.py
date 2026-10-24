from __future__ import annotations

import datetime as dt
import unittest

from scripts.posting_availability import posting_availability_blockers


class PostingAvailabilityTests(unittest.TestCase):
    def blockers(self, status: str = "official_live", description: str = "") -> list[str]:
        return posting_availability_blockers(
            verification_status=status, description=description, as_of=dt.date(2026, 10, 14),
        )

    def test_unavailable_and_unknown_are_not_live(self) -> None:
        for status in ("official_unavailable", "verification_required", "verification_failed"):
            with self.subTest(status=status):
                self.assertTrue(self.blockers(status))

    def test_missing_legacy_status_and_live_status_remain_compatible(self) -> None:
        self.assertEqual([], self.blockers(""))
        self.assertEqual([], self.blockers())

    def test_past_explicit_deadline_blocks_cached_description(self) -> None:
        self.assertEqual(["official posting end date has passed: 2026-09-29"], self.blockers(
            description="Anticipated End Date: 2026-09-29 Position Title: Engineer Senior",
        ))

    def test_future_and_same_day_deadlines_are_not_assumed_closed(self) -> None:
        for date in ("2026-10-14", "2026-10-15"):
            self.assertEqual([], self.blockers(description=f"End Date: {date}"))

    def test_workday_day_before_cutoff_blocks_on_listed_end_date(self) -> None:
        self.assertTrue(self.blockers(description=(
            "Anticipated End Date: 2026-10-14 NOTE: Workday keeps job postings active "
            "through 11:59:59 PM on the day before the listed end date."
        )))

    def test_unrelated_dates_and_invalid_calendar_dates_are_ignored(self) -> None:
        for text in ("Start Date: 2026-09-29", "End Date: 2026-02-30", "End Date: unspecified"):
            self.assertEqual([], self.blockers(description=text))


if __name__ == "__main__":
    unittest.main()
