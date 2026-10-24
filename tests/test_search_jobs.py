from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "search_jobs.py"
SPEC = importlib.util.spec_from_file_location("search_jobs", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {MODULE_PATH}")

search_jobs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = search_jobs
SPEC.loader.exec_module(search_jobs)


class SearchPlanCompactionTests(unittest.TestCase):


    def test_write_search_plan_caps_detailed_company_sections(self) -> None:
        companies = [
            {
                "name": f"Company {index}",
                "careers_url": f"https://company{index}.example.com/careers",
                "notes": "Important company notes.",
            }
            for index in range(search_jobs.MAX_DETAILED_COMPANY_SECTIONS_IN_PLAN + 5)
        ]
        profile = {
            "title_targets": ["Senior Software Engineer", "Solution Architect"],
            "strong_keywords": ["java", "spring"],
            "acceptable_secondary_keywords": ["sql"],
            "exclude_if_primary_focus": ["python"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = search_jobs.write_search_plan(
                today="2026-06-25",
                companies=companies,
                profile=profile,
                rules_text="Keep it tight.",
                out_dir=Path(temp_dir),
            )
            content = output_path.read_text(encoding="utf-8")

        self.assertLessEqual(
            content.count("\n### Company "),
            search_jobs.MAX_DETAILED_COMPANY_SECTIONS_IN_PLAN,
        )
        self.assertIn("Additional companies not expanded in this plan", content)
        self.assertIn("Run sharded ATS/company discovery first", content)
        self.assertIn("scripts/jobagent_python.sh scripts/discover_jobs_sharded.py", content)


if __name__ == "__main__":
    unittest.main()
