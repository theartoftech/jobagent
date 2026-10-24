import unittest

from scripts.linkedin_connection_notes import (
    LINKEDIN_CONNECTION_NOTE_MAX_CHARS,
    build_linkedin_connection_note,
)


class LinkedinConnectionNotesTests(unittest.TestCase):
    def test_note_uses_concise_candidate_voice_and_role_context(self) -> None:
        note = build_linkedin_connection_note(
            company="ExampleCo",
            role="Principal Software Engineer",
        )

        self.assertEqual(
            "Hi, I'm interested in the Principal Software Engineer role at ExampleCo. "
            "I'd appreciate connecting and learning more about the team.",
            note,
        )
        self.assertLess(len(note), 300)

    def test_note_personalizes_first_name_without_email_signature(self) -> None:
        note = build_linkedin_connection_note(
            company="ExampleCo",
            role="Staff Software Engineer",
            contact_name="Riley Recruiter",
        )

        self.assertTrue(note.startswith("Hi Riley,"))
        self.assertNotIn("Thanks,", note)
        self.assertNotIn("-Jeff", note)

    def test_long_role_and_company_still_fit_linkedin_limit(self) -> None:
        note = build_linkedin_connection_note(
            company="Example Company " * 20,
            role="Senior Principal Software Engineer for Enterprise Platform Modernization " * 20,
            contact_name="Alexandria" * 20,
        )

        self.assertLessEqual(len(note), LINKEDIN_CONNECTION_NOTE_MAX_CHARS)
        self.assertLess(len(note), 300)

    def test_missing_company_or_role_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "company is required"):
            build_linkedin_connection_note(company="", role="Principal Engineer")
        with self.assertRaisesRegex(ValueError, "role is required"):
            build_linkedin_connection_note(company="ExampleCo", role="")


if __name__ == "__main__":
    unittest.main()
