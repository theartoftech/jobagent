from __future__ import annotations

import html
import json
import re
import tempfile
import unittest
from pathlib import Path

from scripts.render_workflow_review_html import write_workflow_review_html


class RenderWorkflowReviewHtmlTests(unittest.TestCase):
    def test_write_html_separates_resume_ready_from_human_review(self) -> None:
        payload = {
            "generated_on": "2026-07-22",
            "reviews": [
                {
                    "company": "ReadyCo",
                    "title": "Senior Java Engineer",
                    "job_id": "REQ-12345",
                    "url": "https://example.com/ready",
                    "location": "Remote US",
                    "work_model": "Remote",
                    "source_decision": "Apply",
                    "source_fit_score": 86,
                    "workflow_decision": "APPLY",
                    "should_generate_resume": True,
                    "recommended_resume_base": "software_engineering",
                    "overqualified_risk": "medium",
                    "resume_positioning": "senior_lead_execution_forward",
                    "seniority_scope_risks": ["Senior title may still appear below the candidate's current Senior Principal level"],
                    "interview_positioning_note": "Use reduced-scope interview positioning. Emphasize hands-on contribution.",
                    "gap_questions_needed": False,
                    "hard_blockers": [],
                    "required_gaps": [],
                    "preferred_gaps": [],
                    "direct_matches": ["Java", "Spring Boot"],
                    "questions_for_candidate": [],
                    "final_action": "Run resume workflow now.",
                    "risk_flags": [],
                },
                {
                    "company": "ReviewCo",
                    "title": "Platform Engineer",
                    "url": "https://example.com/review",
                    "location": "Atlanta",
                    "work_model": "Hybrid",
                    "source_decision": "Maybe",
                    "source_fit_score": 68,
                    "workflow_decision": "MODERATE STRETCH",
                    "should_generate_resume": False,
                    "recommended_resume_base": "software_engineering",
                    "gap_questions_needed": True,
                    "hard_blockers": [],
                    "required_gaps": [],
                    "preferred_gaps": ["Go"],
                    "direct_matches": ["Java"],
                    "questions_for_candidate": ["Is Go a blocker?"],
                    "final_action": "Review manually before resume workflow.",
                    "risk_flags": [],
                },
                {
                    "company": "AppliedCo",
                    "title": "Staff Engineer",
                    "url": "https://example.com/applied",
                    "location": "Remote US",
                    "work_model": "Remote",
                    "source_decision": "Apply",
                    "source_fit_score": 84,
                    "workflow_decision": "APPLY",
                    "should_generate_resume": False,
                    "recommended_resume_base": "software_engineering",
                    "gap_questions_needed": False,
                    "already_applied": True,
                    "applied_date": "2026-07-20",
                    "hard_blockers": [],
                    "required_gaps": [],
                    "preferred_gaps": [],
                    "direct_matches": ["Java"],
                    "questions_for_candidate": [],
                    "final_action": "Already applied — do not generate another resume.",
                    "risk_flags": [],
                },
                {
                    "company": "SkipCo",
                    "title": "Frontend Engineer",
                    "url": "https://example.com/skip",
                    "location": "Remote US",
                    "work_model": "Remote",
                    "source_decision": "Maybe",
                    "source_fit_score": 55,
                    "workflow_decision": "SKIP",
                    "should_generate_resume": False,
                    "recommended_resume_base": "none",
                    "gap_questions_needed": True,
                    "already_applied": False,
                    "hard_blockers": ["React primary stack"],
                    "required_gaps": [],
                    "preferred_gaps": [],
                    "direct_matches": [],
                    "questions_for_candidate": ["Does the candidate have required React experience?"],
                    "final_action": "Skip.",
                    "risk_flags": [],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            review_path = root / "2026-07-22-workflow-review.json"
            review_path.write_text(json.dumps(payload), encoding="utf-8")
            (root / "2026-07-22-human-search-companies.md").write_text(
                "\n".join(
                    [
                        "## Human Search Companies - 2026-07-22",
                        "",
                        "1. TriNet - https://jobs.trinet.com/careers",
                        "2. Riskonnect - https://riskonnect.com/company/careers/",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "2026-07-22-networking-matches.md").write_text(
                "\n".join(
                    [
                        "# Networking Matches - 2026-07-22",
                        "",
                        "## LinkedIn Recruiter Add Notes",
                        "",
                        "### ReadyCo",
                        "",
                        "A company-level note.",
                        "",
                        "## People to Contact",
                        "",
                        "### ReadyCo",
                        "",
                        "Relevant jobs:",
                        "- Apply: Senior Java Engineer; 2026-07-22; Remote; fit 86; https://example.com/ready",
                        "",
                        "| Priority | Name | Current Company | Current Role | LinkedIn | Connected On | Match Source | Why Reach Out |",
                        "|---:|---|---|---|---|---|---|---|",
                        "| 1 | Riley Recruiter | ReadyCo | Senior Technical Recruiter | [Profile](https://www.linkedin.com/in/riley-recruiter) | 12 Jun 2026 | export-confirmed | hiring-channel contact |",
                        "",
                        "## Introduction Messages",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            resume_dir = root / "resumes"
            resume_dir.mkdir()
            resume_name = "2026-07-22-ReadyCo-Senior-Java-Engineer-Candidate-Name.docx"
            (resume_dir / resume_name).write_bytes(b"generated resume")
            cover_letters_dir = root / "cover_letters"
            cover_letters_dir.mkdir()
            cover_letter_name = "2026-07-22-ReadyCo-Senior-Java-Engineer-Candidate-Name-Cover-Letter.docx"
            (cover_letters_dir / cover_letter_name).write_bytes(b"generated cover letter")
            (root / "2026-07-22-cover-letter-production.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "company": "ReadyCo",
                                "url": "https://example.com/ready",
                                "artifact_path": f"cover_letters/{cover_letter_name}",
                                "validation_status": "validated",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            output_path = write_workflow_review_html(review_path=review_path)
            rendered = output_path.read_text(encoding="utf-8")
            detail_paths = sorted((root / "2026-07-22-workflow-review-details").glob("*.html"))
            detail_rendered = detail_paths[0].read_text(encoding="utf-8")

        self.assertEqual("2026-07-22-workflow-review.html", output_path.name)
        self.assertIn("<strong>1</strong>Resume ready", rendered)
        self.assertIn("<strong>1</strong>Gap questions", rendered)
        self.assertIn("<strong>1</strong>Already applied", rendered)
        ready_section = rendered.split('id="resume-ready"', 1)[1].split('id="gap-questions"', 1)[0]
        self.assertIn("ReadyCo", ready_section)
        self.assertNotIn("ReviewCo", ready_section)
        self.assertIn("ReviewCo", rendered)
        self.assertIn("MODERATE STRETCH", rendered)
        self.assertIn("Is Go a blocker?", rendered)
        self.assertEqual(1, rendered.count("Copy Resolution Prompt"))
        self.assertEqual(1, rendered.count("Copy All Gap Questions"))
        self.assertIn("Resolve workflow review for ReviewCo — Platform Engineer", rendered)
        self.assertIn("Is Go a blocker?", rendered)
        self.assertIn('aria-live="polite"', rendered)
        self.assertIn("navigator.clipboard.writeText", rendered)
        self.assertEqual(1, len(detail_paths))
        self.assertIn('class="clickable-job-row"', ready_section)
        self.assertIn("Open role details", ready_section)
        self.assertIn("Overqualified risk", ready_section)
        self.assertIn("medium", ready_section)
        self.assertNotIn("Seniority / scope risks", ready_section)
        self.assertNotIn("Interview positioning", ready_section)
        self.assertNotIn("Open generated resume", ready_section)
        self.assertIn("senior_lead_execution_forward", detail_rendered)
        self.assertIn("<dt>Job ID</dt><dd>REQ-12345</dd>", detail_rendered)
        self.assertIn("Why it fits", detail_rendered)
        self.assertIn("Technical gaps", detail_rendered)
        self.assertIn("Seniority / scope risks", detail_rendered)
        self.assertIn("Interview positioning", detail_rendered)
        self.assertIn("Use reduced-scope interview positioning", detail_rendered)
        self.assertIn("Run resume workflow", detail_rendered)
        self.assertEqual(1, ready_section.count('class="resume-ready-check"'))
        self.assertIn('data-job-url="https://example.com/ready"', ready_section)
        self.assertIn('aria-label="Mark ReadyCo — Senior Java Engineer as applied"', ready_section)
        self.assertIn(
            'href="2026-07-22-daily-job-search.html">Open broad discovery artifact</a>',
            rendered,
        )
        self.assertIn(f'href="../resumes/{resume_name}"', detail_rendered)
        self.assertIn("Open generated resume", detail_rendered)
        self.assertIn("Open cover letter", detail_rendered)
        self.assertIn(f'href="../cover_letters/{cover_letter_name}"', detail_rendered)
        self.assertIn("LinkedIn outreach", detail_rendered)
        self.assertIn("Riley Recruiter", detail_rendered)
        self.assertIn("Senior Technical Recruiter", detail_rendered)
        self.assertIn("https://www.linkedin.com/in/riley-recruiter", detail_rendered)
        self.assertIn("export-confirmed", detail_rendered)
        self.assertIn("Hi Riley,", detail_rendered)
        self.assertIn("Senior Java Engineer role at ReadyCo", detail_rendered)
        self.assertRegex(detail_rendered, r"\d+ / 299 characters")
        self.assertIn("Copy message", detail_rendered)
        self.assertIn("navigator.clipboard.writeText", detail_rendered)
        self.assertIn('href="../2026-07-22-workflow-review.html"', detail_rendered)
        self.assertIn("jobagent-resume-ready:2026-07-22", rendered)
        self.assertIn("resumeReadyCheckboxes", rendered)
        self.assertIn("clickableJobRows", rendered)
        self.assertIn('event.key === "Enter"', rendered)
        self.assertIn("Resume-ready application progress saved in this browser.", rendered)
        self.assertIn('id="gap-questions"', rendered)
        self.assertIn("Gap Questions for Candidate", rendered)
        gap_section = rendered.split('id="gap-questions"', 1)[1].split('id="already-applied"', 1)[0]
        self.assertNotIn("SkipCo", gap_section)
        batch_match = re.search(
            r'class="copy-resolution copy-all-resolution"[^>]+data-resolution-prompt="([^"]+)"',
            gap_section,
        )
        self.assertIsNotNone(batch_match)
        batch_prompt = html.unescape(batch_match.group(1)) if batch_match is not None else ""
        self.assertIn("Batch workflow gap resolution — 2026-07-22", batch_prompt)
        self.assertIn("Job 1: ReviewCo — Platform Engineer", batch_prompt)
        self.assertIn("Posting: https://example.com/review", batch_prompt)
        self.assertIn("- Is Go a blocker?", batch_prompt)
        self.assertNotIn("SkipCo", batch_prompt)
        self.assertIn("professional_evidence", batch_prompt)
        self.assertIn("limited_exposure", batch_prompt)
        self.assertIn("do_not_claim", batch_prompt)
        self.assertIn("posting_false_positive", batch_prompt)
        self.assertIn("candidate_workflow_profile.yml", batch_prompt)
        self.assertIn("candidate_gap_resolutions", batch_prompt)
        self.assertIn("answers each factual experience question once", batch_prompt)
        self.assertIn("keyed by the exact posting URL", batch_prompt)
        self.assertIn("Never store posting_false_positive as a candidate-wide answer", batch_prompt)
        self.assertIn("scripts/review_job_fit.py", batch_prompt)
        self.assertIn("2026-07-22-resume-ready-jobs.md", batch_prompt)
        self.assertIn("moved to Resume Ready", batch_prompt)
        self.assertIn("still need answers", batch_prompt)
        self.assertIn("downgraded or rejected", batch_prompt)
        self.assertIn("scripts/produce_resumes.py", batch_prompt)
        self.assertIn("sole authorization queue", batch_prompt)
        self.assertIn("Reuse already validated resumes", batch_prompt)
        self.assertIn("regenerate the workflow HTML", batch_prompt)
        self.assertIn('id="already-applied"', rendered)
        self.assertIn("AppliedCo", rendered)
        self.assertIn('<details class="diagnostic-archive">', rendered)
        diagnostic_section = rendered.split('<details class="diagnostic-archive">', 1)[1]
        self.assertIn("SkipCo", diagnostic_section)
        self.assertIn("Discovery diagnostics", diagnostic_section)
        self.assertIn("Discovery candidate", rendered)
        self.assertNotIn("Use reduced-scope interview positioning", rendered)
        self.assertNotIn("Source: Apply", rendered)
        self.assertIn('data-tab-target="companies-panel"', rendered)
        self.assertIn("Companies to Check", rendered)
        self.assertEqual(2, rendered.count('class="company-check"'))
        self.assertIn("TriNet", rendered)

    def test_resume_ready_row_marks_missing_resume_artifact(self) -> None:
        payload = {
            "generated_on": "2026-07-22",
            "reviews": [
                {
                    "company": "MissingCo",
                    "title": "Principal Engineer",
                    "url": "https://example.com/missing",
                    "workflow_decision": "APPLY",
                    "should_generate_resume": True,
                    "source_fit_score": 86,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            review_path = Path(tmp_dir) / "2026-07-22-workflow-review.json"
            review_path.write_text(json.dumps(payload), encoding="utf-8")
            rendered = write_workflow_review_html(review_path=review_path).read_text(encoding="utf-8")
            detail_path = next((Path(tmp_dir) / "2026-07-22-workflow-review-details").glob("*.html"))
            detail_rendered = detail_path.read_text(encoding="utf-8")

        self.assertNotIn("Generated resume", rendered)
        self.assertIn('<span class="resume-missing">Resume not generated</span>', detail_rendered)
        self.assertIn(
            '<span class="resume-missing">Cover letter not generated</span>',
            detail_rendered,
        )
        self.assertNotIn("Open generated resume", detail_rendered)
        self.assertIn("<dt>Job ID</dt><dd>Not provided</dd>", detail_rendered)
        self.assertNotIn("Copy All Gap Questions", rendered)
        self.assertIn("LinkedIn outreach", detail_rendered)
        self.assertIn("No matching current-company LinkedIn contacts", detail_rendered)
        self.assertIn("Principal Engineer role at MissingCo", detail_rendered)
        self.assertIn("Copy message", detail_rendered)

    def test_write_html_escapes_external_job_text(self) -> None:
        payload = {
            "generated_on": "2026-07-22",
            "reviews": [
                {
                    "company": "<script>alert(1)</script>",
                    "title": "Engineer",
                    "url": "javascript:alert(1)",
                    "workflow_decision": "SKIP",
                    "should_generate_resume": False,
                    "source_fit_score": 10,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            review_path = Path(tmp_dir) / "review.json"
            review_path.write_text(json.dumps(payload), encoding="utf-8")
            rendered = write_workflow_review_html(review_path=review_path).read_text(encoding="utf-8")

        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)


if __name__ == "__main__":
    unittest.main()
