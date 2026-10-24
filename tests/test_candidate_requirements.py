from __future__ import annotations

import unittest

from scripts.candidate_requirements import candidate_location_checks


class CandidateLocationTests(unittest.TestCase):
    def test_us_does_not_match_austria_or_australia(self) -> None:
        for country in ("Austria", "Australia"):
            with self.subTest(country=country):
                _, questions = candidate_location_checks(
                    f"Remote - {country}", "remote", {"countries": ["US"], "work_models": ["remote"]})
                self.assertIn("remote country eligibility requires confirmation", questions)

    def test_country_only_onsite_location_is_not_a_confirmed_commute(self) -> None:
        for country in ("Germany", "US", "United States"):
            with self.subTest(country=country):
                blockers, questions = candidate_location_checks(
                    country, "onsite", {"countries": ["Germany"], "work_models": ["onsite"],
                                         "hybrid_allowed_metro_tokens": ["Berlin"]})
                self.assertTrue(blockers or questions)

    def test_partial_city_name_is_not_a_commute_match(self) -> None:
        blockers, _ = candidate_location_checks(
            "Readington", "hybrid", {"hybrid_allowed_metro_tokens": ["Reading"]})
        self.assertIn("physical location outside candidate commute area", blockers)

    def test_configured_country_aliases_and_global_locations(self) -> None:
        for location, country in (("Remote USA", "United States"), ("Remote United Kingdom", "UK"),
                                  ("Remote worldwide", "Germany"), ("Remote - Germany", "Germany")):
            with self.subTest(location=location):
                self.assertEqual(([], []), candidate_location_checks(
                    location, "remote", {"countries": [country], "work_models": ["remote"]}))

    def test_known_metro_and_unknown_location_remain_distinct(self) -> None:
        profile = {"countries": ["Germany"], "work_models": ["hybrid"], "hybrid_allowed_metro_tokens": ["Berlin"]}
        self.assertEqual(([], []), candidate_location_checks("Berlin, Germany", "flex", profile))
        self.assertIn("physical location requires confirmation", candidate_location_checks("", "hybrid", profile)[1])
