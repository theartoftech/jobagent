from __future__ import annotations

import unittest

from scripts.platform_requirements import unsupported_primary_platforms


class PlatformRequirementTests(unittest.TestCase):
    def test_platform_in_title_blocks_even_when_qualifications_are_preferred(self) -> None:
        self.assertEqual(["Guidewire"], unsupported_primary_platforms(
            title="Senior Software Engineer, Guidewire BillingCenter",
            description="Preferred: five years of Guidewire configuration.",
            unsupported_platforms=["Guidewire"],
        ))

    def test_guidewire_product_and_language_titles_are_detected(self) -> None:
        for title in ("BillingCenter Developer", "PolicyCenter Engineer", "ClaimCenter Architect", "GOSU Developer"):
            with self.subTest(title=title):
                self.assertEqual(["Guidewire"], unsupported_primary_platforms(
                    title=title, description="", unsupported_platforms=["Guidewire"],
                ))

    def test_primary_delivery_in_body_blocks_generic_title(self) -> None:
        self.assertEqual(["Guidewire"], unsupported_primary_platforms(
            title="Senior Software Engineer",
            description="You will configure Guidewire BillingCenter. Own Guidewire upgrades and integrations.",
            unsupported_platforms=["Guidewire"],
        ))

    def test_single_explicit_platform_delivery_task_is_primary(self) -> None:
        self.assertEqual(["Guidewire"], unsupported_primary_platforms(
            title="Senior Engineer", description="Configure Guidewire BillingCenter integrations.",
            unsupported_platforms=["Guidewire"],
        ))

    def test_optional_or_incidental_mentions_do_not_block(self) -> None:
        for description in (
            "Build Java APIs. Familiarity with Guidewire is a plus.",
            "Guidewire integration is optional. Guidewire experience is preferred.",
            "Integrate Java services with Guidewire if needed. Guidewire knowledge is not required.",
            "Our customers use Guidewire. Their billing team uses BillingCenter.",
        ):
            with self.subTest(description=description):
                self.assertEqual([], unsupported_primary_platforms(
                    title="Principal Java Engineer", description=description, unsupported_platforms=["Guidewire"],
                ))

    def test_html_and_case_do_not_hide_primary_platform(self) -> None:
        self.assertEqual(["Guidewire"], unsupported_primary_platforms(
            title="Senior Engineer", description="<p>Configure GUIDEWIRE BillingCenter.</p>",
            unsupported_platforms=["Guidewire"],
        ))

    def test_only_configured_platforms_are_rejected(self) -> None:
        self.assertEqual([], unsupported_primary_platforms(
            title="Guidewire Engineer", description="Configure Guidewire.", unsupported_platforms=[],
        ))
        self.assertEqual(["Legacy ERP"], unsupported_primary_platforms(
            title="Legacy ERP Architect", description="", unsupported_platforms=["Legacy ERP"],
        ))

    def test_invalid_platform_configuration_fails_explicitly(self) -> None:
        with self.assertRaises(ValueError):
            unsupported_primary_platforms(title="Engineer", description="", unsupported_platforms=[""])
        with self.assertRaises(ValueError):
            unsupported_primary_platforms(title="Engineer", description="", unsupported_platforms="Guidewire")

    def test_platform_names_match_whole_terms_and_duplicate_rules_are_stable(self) -> None:
        self.assertEqual([], unsupported_primary_platforms(
            title="Guidewireless Engineer", description="", unsupported_platforms=["Guidewire"],
        ))
        self.assertEqual(["Guidewire"], unsupported_primary_platforms(
            title="Guidewire Engineer", description="", unsupported_platforms=["Guidewire", "Guidewire"],
        ))
