from __future__ import annotations

import unittest
from pathlib import Path


class JobAgentPythonWrapperTests(unittest.TestCase):
    def test_wrapper_loads_local_env_files_before_python_exec(self) -> None:
        wrapper = Path(__file__).resolve().parents[1] / "scripts" / "jobagent_python.sh"
        content = wrapper.read_text(encoding="utf-8")

        self.assertIn(".env.local", content)
        self.assertIn("set -a", content)
        self.assertIn("source", content)


if __name__ == "__main__":
    unittest.main()
