from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

POC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC_ROOT))

from benchmark import BenchmarkError  # noqa: E402
from run_benchmark import parse_args, resolve_bonsai_key, resolve_openai_key  # noqa: E402


class ArgumentTests(unittest.TestCase):
    def test_default_certificate_path_uses_current_user_home(self) -> None:
        with patch("run_benchmark.Path.home", return_value=Path("/example-home")):
            args = parse_args([])

        self.assertEqual(Path("/example-home/.config/bonsai/llmserver.crt"), args.bonsai_ca_file)

    def test_defaults_to_both_providers(self) -> None:
        args = parse_args([])

        self.assertIsNone(args.providers)

    def test_accepts_explicit_single_provider(self) -> None:
        args = parse_args(["--provider", "bonsai"])

        self.assertEqual(args.providers, ["bonsai"])


class CredentialResolutionTests(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "environment-key"})
    def test_openai_environment_key_has_precedence(self) -> None:
        self.assertEqual(resolve_openai_key(None), "environment-key")

    @patch.dict(os.environ, {}, clear=True)
    def test_openai_requires_environment_or_file(self) -> None:
        with self.assertRaisesRegex(BenchmarkError, "--openai-env-file"):
            resolve_openai_key(None)

    @patch.dict(os.environ, {"BONSAI_API_KEY": "bonsai-environment-key"})
    def test_bonsai_environment_key_has_precedence(self) -> None:
        self.assertEqual(resolve_bonsai_key("service"), "bonsai-environment-key")


if __name__ == "__main__":
    unittest.main()
