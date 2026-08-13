from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import match_network


class MatchNetworkVerificationTests(unittest.TestCase):
    def test_live_verification_can_promote_contact_into_company_match(self) -> None:
        jobs = [
            match_network.Job(
                decision="Apply",
                company="NCR Voyix",
                title="Senior Software Engineer",
                posted="2026-06-23",
                location="Remote / Remote",
                fit="80",
                url="https://example.com/jobs/1",
            )
        ]
        contacts = [
            match_network.Contact(
                first_name="Mila",
                last_name="Crain",
                linkedin_url="https://www.linkedin.com/in/milacrain",
                email="",
                company="Some Old Company",
                position="Global Talent Acquisition Manager",
                connected_on="08 Nov 2010",
            )
        ]
        verifications = {
            "https://www.linkedin.com/in/milacrain": match_network.LiveProfileVerification(
                linkedin_url="https://www.linkedin.com/in/milacrain",
                current_company="NCR Voyix",
                current_role="Global Talent Acquisition Manager",
                verified_on="2026-06-23",
            )
        }

        matches = match_network.match_contacts(jobs, contacts, verifications)

        self.assertEqual(len(matches["NCR Voyix"]), 1)
        match = matches["NCR Voyix"][0]
        self.assertEqual(match.match_source, "live-profile-confirmed")
        self.assertEqual(match.current_company, "NCR Voyix")
        self.assertEqual(match.current_role, "Global Talent Acquisition Manager")

    def test_live_verification_can_remove_stale_export_match(self) -> None:
        jobs = [
            match_network.Job(
                decision="Apply",
                company="NCR Voyix",
                title="Senior Software Engineer",
                posted="2026-06-23",
                location="Remote / Remote",
                fit="80",
                url="https://example.com/jobs/1",
            )
        ]
        contacts = [
            match_network.Contact(
                first_name="Mila",
                last_name="Crain",
                linkedin_url="https://www.linkedin.com/in/milacrain",
                email="",
                company="NCR Voyix",
                position="Global Talent Acquisition Manager",
                connected_on="08 Nov 2010",
            )
        ]
        verifications = {
            "https://www.linkedin.com/in/milacrain": match_network.LiveProfileVerification(
                linkedin_url="https://www.linkedin.com/in/milacrain",
                current_company="Different Company",
                current_role="Global Talent Acquisition Manager",
                verified_on="2026-06-23",
            )
        }

        matches = match_network.match_contacts(jobs, contacts, verifications)

        self.assertEqual(matches["NCR Voyix"], [])

    def test_export_match_is_marked_when_no_live_verification_exists(self) -> None:
        jobs = [
            match_network.Job(
                decision="Apply",
                company="NCR Voyix",
                title="Senior Software Engineer",
                posted="2026-06-23",
                location="Remote / Remote",
                fit="80",
                url="https://example.com/jobs/1",
            )
        ]
        contacts = [
            match_network.Contact(
                first_name="Mila",
                last_name="Crain",
                linkedin_url="https://www.linkedin.com/in/milacrain",
                email="",
                company="NCR Voyix",
                position="Global Talent Acquisition Manager",
                connected_on="08 Nov 2010",
            )
        ]

        matches = match_network.match_contacts(jobs, contacts, {})

        self.assertEqual(len(matches["NCR Voyix"]), 1)
        match = matches["NCR Voyix"][0]
        self.assertEqual(match.match_source, "export-confirmed")
        self.assertEqual(match.current_company, "NCR Voyix")

    def test_load_live_verifications_reads_json_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "verifications.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "linkedin_url": "https://www.linkedin.com/in/milacrain",
                            "current_company": "NCR Voyix",
                            "current_role": "Global Talent Acquisition Manager",
                            "verified_on": "2026-06-23",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            verifications = match_network.load_live_verifications(path)

        self.assertIn("https://www.linkedin.com/in/milacrain", verifications)
        self.assertEqual(
            verifications["https://www.linkedin.com/in/milacrain"].current_company,
            "NCR Voyix",
        )


if __name__ == "__main__":
    unittest.main()
