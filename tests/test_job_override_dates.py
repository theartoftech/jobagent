import datetime as dt
import unittest

from scripts.job_override_dates import (
    filter_companies_by_overrides,
    override_is_active,
)


class JobOverrideDateTests(unittest.TestCase):
    def test_expiration_is_inclusive_and_then_releases_company(self) -> None:
        override = {
            "company": "Block",
            "decision": "Skip",
            "expires_on": "2026-10-28",
        }

        self.assertTrue(override_is_active(override, as_of=dt.date(2026, 10, 28)))
        self.assertFalse(override_is_active(override, as_of=dt.date(2026, 10, 29)))

    def test_company_filter_removes_only_active_company_wide_skip(self) -> None:
        companies = [{"name": "Block"}, {"name": "Stripe"}]
        overrides = [
            {
                "company": "Block",
                "decision": "Skip",
                "expires_on": "2026-10-28",
            }
        ]

        active = filter_companies_by_overrides(
            companies,
            overrides,
            as_of=dt.date(2026, 10, 28),
        )
        expired = filter_companies_by_overrides(
            companies,
            overrides,
            as_of=dt.date(2026, 10, 29),
        )

        self.assertEqual([{"name": "Stripe"}], active)
        self.assertEqual(companies, expired)

    def test_invalid_expiration_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "expires_on"):
            override_is_active(
                {"company": "Block", "decision": "Skip", "expires_on": "next month"},
                as_of=dt.date(2026, 9, 28),
            )


if __name__ == "__main__":
    unittest.main()
