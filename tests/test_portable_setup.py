from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from scripts import setup_jobagent
from scripts import discover_jobs, search_jobs

ROOT = Path(__file__).resolve().parents[1]


def answers() -> dict[str, Any]:
    return {
        "name": "Sample Candidate", "email": "sample@example.test",
        "title_targets": ["Registered Nurse"], "adjacent_titles": ["Clinical Educator"],
        "strong_keywords": ["patient care", "triage"], "secondary_keywords": [],
        "last_role": "Registered nurse in an outpatient clinic.",
        "evidence": ["Delivered patient care and triage in an outpatient clinic."],
        "do_not_claim": ["surgery"], "languages": ["English", "German"],
        "unsupported_required_languages": [], "city": "Potsdam", "metro": "Berlin",
        "commute_locations": ["Berlin", "Potsdam"], "countries": ["Germany"],
        "work_models": ["onsite", "hybrid"], "salary_floor": 0, "currency": "EUR",
        "employment_types": ["full-time"], "work_authorization": "Authorized in Germany",
        "sponsorship_required": False, "travel": "Up to 10%", "relocation": "No",
        "excluded_companies": [], "excluded_titles": [], "exclude_if_primary_focus": [],
        "freshness_days": 30, "timezone": "Europe/Berlin",
        "schedule": "Weekdays at 08:00", "schedule_enabled": False,
        "companies": [{"name": "Example Clinic", "careers_url": "https://clinic.example.test/careers",
                       "evidence_url": "https://clinic.example.test/careers", "reason": "Local nursing roles", "verified_on": "2026-10-24"}],
        "applied_jobs": [], "manual_jobs": [], "resume_path": "", "document_generation": False,
    }


class PortableSetupTests(unittest.TestCase):
    def test_generates_all_private_inputs_without_inherited_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup_jobagent.configure(answers(), root)
            for name in setup_jobagent.OUTPUT_FILES:
                self.assertTrue((root / name).is_file(), name)
            profile = discover_jobs.profile_doc_to_profile(discover_jobs.load_yaml(root / "candidate_profile.yml"))
            self.assertEqual(profile["title_targets"], ["Registered Nurse", "Clinical Educator"])
            queries = " ".join(search_jobs.build_title_queries(answers()["companies"][0], profile))
            queries += " ".join(q for _, q in discover_jobs.build_x_post_searches(profile))
            self.assertIn("Registered Nurse", queries)
            self.assertNotIn("Java", queries)
            self.assertNotIn("Atlanta", queries)
            self.assertEqual(profile["unsupported_required_languages"], [])
            self.assertFalse(json.loads((root / "run_preferences.json").read_text())["schedule_enabled"])

    def test_rejects_missing_malformed_and_oversized_answers_before_writing(self) -> None:
        cases = [{}, {**answers(), "work_models": ["maybe"]}, {**answers(), "timezone": "Invalid/Zone"},
                 {**answers(), "title_targets": []}, {**answers(), "strong_keywords": "triage"},
                 {**answers(), "companies": answers()["companies"] * 201},
                 {**answers(), "companies": [{"name": "Bad", "careers_url": "javascript:alert(1)"}]},
                 {**answers(), "salary_floor": -1}, {**answers(), "freshness_days": 0}]
        for invalid in cases:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaises(ValueError):
                    setup_jobagent.configure(invalid, root)
                self.assertEqual(list(root.iterdir()), [])

    def test_refuses_to_overwrite_existing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup_jobagent.configure(answers(), root)
            with self.assertRaises(FileExistsError):
                setup_jobagent.configure(answers(), root)

    def test_private_outputs_remain_ignored_after_setup(self) -> None:
        for name in (*setup_jobagent.OUTPUT_FILES, "setup.answers.json", "candidate_profile.yaml"):
            result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--no-index", "-q", name], check=False)
            self.assertEqual(0, result.returncode, name)


    def test_german_nurse_in_berlin_not_rejected_by_previous_candidates_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup_jobagent.configure(answers(), root)
            profile = discover_jobs.profile_doc_to_profile(discover_jobs.load_yaml(root / "candidate_profile.yml"))
            job = discover_jobs.JobPosting(company="Example Clinic", title="Registered Nurse", url="https://clinic.example.test/jobs/1", location="Berlin, Germany", work_model="onsite", description="Patient care and triage. Fluent German required.", posted_age_days=1)
            discover_jobs.score_job(job, profile, 30)
            self.assertEqual(job.decision, "Apply")

class PortableRequirementTests(unittest.TestCase):
    def test_role_families_are_candidate_owned(self) -> None:
        for title, skill in [("Junior Nurse", "patient care"), ("Sales Engineer", "presales"),
                             ("Mobile Engineer", "iOS"), ("Software Engineer", "Ruby on Rails"),
                             ("SRE Engineer", "observability"), ("ML Engineer", "machine learning"),
                             ("Software Engineer", ".NET"), ("Director", "administration")]:
            with self.subTest(title=title, skill=skill):
                profile = {"title_targets": [title], "strong_keywords": [skill]}
                job = discover_jobs.JobPosting(company="Example", title=title, url="https://example.test/1", description=f"Required: {skill}.", posted_age_days=1)
                discover_jobs.score_job(job, profile, 30)
                self.assertEqual("Apply", job.decision)
                discover_jobs.score_job(job, {**profile, "do_not_claim": [skill]}, 30)
                self.assertEqual("Skip", job.decision)

    def test_language_support_is_not_inherited(self) -> None:
        job = discover_jobs.JobPosting(company="Example", title="Nurse", url="https://example.test/1", description="Patient care. German speaking required.", posted_age_days=1)
        profile = {"title_targets": ["Nurse"], "strong_keywords": ["patient care"]}
        discover_jobs.score_job(job, profile, 30)
        self.assertEqual("Apply", job.decision)
        discover_jobs.score_job(job, {**profile, "unsupported_required_languages": ["German"]}, 30)
        self.assertEqual("Skip", job.decision)

    def test_social_and_board_queries_use_chosen_roles(self) -> None:
        profile = {"title_targets": ["Nurse"], "strong_keywords": ["triage"], "locations": {"preferred": ["Berlin"]}}
        for searches in (discover_jobs.build_x_post_searches(profile), discover_jobs.build_threads_post_searches(profile), discover_jobs.build_public_board_searches({"name": "Clinic"}, profile)):
            self.assertTrue(searches)
            for title, query in searches:
                self.assertIn("Nurse", query)
                self.assertIn("Berlin", query)
                self.assertNotIn("Java", query)
        self.assertTrue(discover_jobs.social_post_matches_profile(post_title="Nurse", post_text="Hiring", profile=profile))
        self.assertFalse(discover_jobs.social_post_matches_profile(post_title="Java engineer", post_text="Hiring", profile=profile))

    def test_plan_is_local_and_does_not_invoke_private_integrations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = search_jobs.write_search_plan(today="2026-10-24", companies=answers()["companies"], profile={"title_targets": ["Nurse"], "freshness": {"include_max_age_days": 14}}, rules_text="Only verified postings", out_dir=Path(tmp))
            plan = path.read_text()
            for expected in ("--local-applied applied_jobs.json", "--max-age-days 14", "internal browser", "strict review", "resume-ready-jobs.md"):
                self.assertIn(expected, plan)
            for forbidden in ("--ssh-host", "publish_skill_gap_analytics.py", "USAJOBS", "Bonsai", "jhaynes"):
                self.assertNotIn(forbidden, plan)

class SetupBoundaryTests(unittest.TestCase):
    def test_optional_startups_and_disabled_rotation(self) -> None:
        from scripts import discover_jobs_sharded as sharded
        import datetime as dt
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "startup.yml"
            path.write_text("startups: []\n")
            self.assertEqual([], sharded.load_startup_watchlist(path))
        self.assertEqual([], sharded.select_startup_rotation(startups=[], base_companies=[], today=dt.date.today(), count=0))

    def test_interview_validation_matrix(self) -> None:
        invalid_changes = [
            {"name": ""}, {"languages": []}, {"work_models": ["onsite"], "commute_locations": []},
            {"unsupported_required_languages": ["german"]}, {"schedule_enabled": "false"},
            {"schedule_enabled": True}, {"salary_floor": float("inf")}, {"salary_floor": True},
            {"freshness_days": True}, {"companies": []}, {"companies": [{}]},
            {"applied_jobs": {}}, {"applied_jobs": [{}]},
            {"applied_jobs": [{"company": "Example", "title": "Nurse", "job_url": "https://example.test/1"}]},
            {"manual_jobs": [{"company": "Example", "title": "Nurse", "url": "file:///etc/passwd"}]},
            {"resume_path": None}, {"document_generation": True},
        ]
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    setup_jobagent.validate_answers({**answers(), **changes})
        with self.assertRaises(ValueError):
            setup_jobagent.validate_answers([])
        for url in (None, "http://example.test", "https://user:pass@example.test", "https:///missing"):
            with self.assertRaises(ValueError):
                setup_jobagent.public_url(url)
        for change in ({"verified_on": "yesterday"}, {"reason": ""}, {"evidence_url": "http://example.test"}):
            with self.assertRaises(ValueError):
                setup_jobagent.validate_answers({**answers(), "companies": [{**answers()["companies"][0], **change}]})
        with self.assertRaises(ValueError):
            setup_jobagent.validate_answers({**answers(), "companies": answers()["companies"] * 2})

    def test_maximum_site_budget_and_real_application_history(self) -> None:
        data = answers()
        data["companies"] = [{**data["companies"][0], "name": f"Clinic {i}", "careers_url": f"https://clinic{i}.example.test"} for i in range(199)]
        data["applied_jobs"] = [{"company": "Clinic", "title": "Nurse", "job_url": "https://clinic.example.test/job/123", "status": "Applied", "applied_date": "2026-10-20"}]
        data["manual_jobs"] = [{"company": "Clinic", "title": "Nurse", "url": "https://clinic.example.test/job/456"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup_jobagent.configure(data, root)
            sources = discover_jobs.load_yaml(root / "job_sources.yml")["sources"]
            self.assertEqual(200, len(sources))
            from scripts.mark_applied_jobs import load_tracker_jobs_snapshot
            applied = load_tracker_jobs_snapshot(root / "applied_jobs.json")
            self.assertEqual(1, len(applied))
            self.assertTrue(applied[0].already_applied)
        data["companies"].append({**data["companies"][0], "name": "Over budget", "careers_url": "https://over.example.test"})
        with self.assertRaises(ValueError):
            setup_jobagent.validate_answers(data)

    def test_setup_cli_and_location_requirements(self) -> None:
        import sys
        from unittest import mock
        from scripts.candidate_requirements import candidate_location_checks
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "answers.json"
            source.write_text(json.dumps(answers()))
            with mock.patch.object(sys, "argv", ["setup_jobagent", "--answers", str(source), "--root", str(root / "config")]):
                setup_jobagent.main()
            self.assertTrue((root / "config" / "candidate_profile.yml").exists())
        profile = {"work_models": ["remote"], "countries": ["Germany"], "hybrid_allowed_metro_tokens": ["Berlin"]}
        self.assertTrue(candidate_location_checks("Berlin", "onsite", profile)[0])
        self.assertFalse(candidate_location_checks("Germany", "remote", profile)[1])
        self.assertTrue(candidate_location_checks("Unknown", "remote", profile)[1])
        self.assertFalse(candidate_location_checks("Worldwide", "remote", profile)[1])
        self.assertTrue(candidate_location_checks("", "Unknown", profile)[1])
        self.assertTrue(candidate_location_checks("Paris", "flex", profile)[0])

class FirstRunIntegrationTests(unittest.TestCase):
    def test_new_directory_to_ats_discovery_local_history_and_review(self) -> None:
        import contextlib
        import datetime as dt
        import io
        import os
        import sys
        from unittest import mock
        from scripts import discover_jobs_sharded, build_job_inspection_queue, mark_applied_jobs, review_job_fit
        data = answers()
        data["companies"][0]["careers_url"] = "https://boards.greenhouse.io/exampleclinic"
        today = dt.date.today().isoformat()
        ats_payload = {"jobs": [{"id": 123, "title": "Registered Nurse", "location": {"name": "Berlin, Germany"},
                                "updated_at": today, "absolute_url": "https://boards.greenhouse.io/exampleclinic/jobs/123",
                                "content": "Onsite patient care and triage. Fluent German required."}]}
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            setup_jobagent.configure(data, root)
            original = Path.cwd()
            try:
                os.chdir(root)
                def run(main: Callable[[], None], argv: list[str]) -> None:
                    with mock.patch.object(sys, "argv", ["command", *argv]):
                        main()
                run(search_jobs.main, [])
                with mock.patch.object(discover_jobs, "request_json", return_value=ats_payload), mock.patch.object(discover_jobs, "request_text", return_value=""):
                    run(discover_jobs_sharded.main, ["--no-rotation", "--startup-count", "0", "--limit-companies", "1", "--ats-only"])
                discovered = root / "outputs" / f"{today}-discovered-jobs.json"
                self.assertTrue(discovered.exists())
                run(build_job_inspection_queue.main, ["--discovered-jobs", str(discovered), "--out", "outputs", "--date", today])
                report = root / "outputs" / f"{today}-daily-job-search.md"
                snapshot = root / "outputs" / f"{today}-applied-jobs.json"
                with mock.patch.object(mark_applied_jobs, "fetch_tracker_jobs", side_effect=AssertionError("No SSH during onboarding")):
                    run(mark_applied_jobs.main, ["--daily-report", str(report), "--local-applied", "applied_jobs.json", "--applied-jobs-out", str(snapshot)])
                run(review_job_fit.main, ["--discovered-jobs", str(discovered), "--workflow-profile", "candidate_workflow_profile.yml", "--applied-jobs", str(snapshot), "--out", "outputs", "--date", today])
                reviews = json.loads((root / "outputs" / f"{today}-workflow-review.json").read_text())["reviews"]
                self.assertEqual(1, len(reviews))
                self.assertEqual("Registered Nurse", reviews[0]["title"])
                self.assertEqual("STRONG APPLY", reviews[0]["workflow_decision"])
                self.assertTrue((root / "outputs" / f"{today}-workflow-review.html").exists())
                self.assertFalse((root / "outputs" / "resumes").exists())
            finally:
                os.chdir(original)

class RuntimeSetupValidationTests(unittest.TestCase):
    def test_private_setup_check_counts_sites_and_rejects_growth_over_budget(self) -> None:
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup_jobagent.configure(answers(), root)
            self.assertEqual(2, setup_jobagent.check_setup(root))
            startups = [{"name": f"New {i}", "careers_url": f"https://new{i}.example.test"} for i in range(199)]
            (root / "startup_watchlist.yml").write_text(yaml.safe_dump({"startups": startups}))
            with self.assertRaisesRegex(ValueError, "200"):
                setup_jobagent.check_setup(root)

    def test_missing_private_input_fails_before_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                setup_jobagent.check_setup(Path(tmp))
