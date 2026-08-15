import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "process_codex_review.py"
SPEC = importlib.util.spec_from_file_location("process_codex_review", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class TestCodexReviewProcessing(unittest.TestCase):
    def test_valid_pass(self):
        data = MODULE._validate(
            {"verdict": "PASS", "summary": "No blocking issues.", "findings": []}
        )
        rendered = MODULE._render(data)
        self.assertIn("Verdict: PASS", rendered)

    def test_valid_failure(self):
        data = MODULE._validate(
            {
                "verdict": "FAIL",
                "summary": "One unsafe change.",
                "findings": [
                    {
                        "priority": "P1",
                        "path": "src/orbit/core/main.py",
                        "line": 10,
                        "title": "Worker can terminate",
                        "body": "The changed call raises outside the worker guard.",
                    }
                ],
            }
        )
        self.assertIn("[P1] Worker can terminate", MODULE._render(data))

    def test_pass_with_findings_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE._validate(
                {
                    "verdict": "PASS",
                    "summary": "Inconsistent.",
                    "findings": [
                        {
                            "priority": "P2",
                            "path": "file.py",
                            "line": 1,
                            "title": "Bug",
                            "body": "Concrete bug.",
                        }
                    ],
                }
            )

    def test_incomplete_failure_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "Codex review did not complete") as error:
            MODULE._validate(
                {
                    "verdict": "FAIL",
                    "summary": "Sandbox initialization failed.",
                    "findings": [],
                }
            )
        self.assertIn("Sandbox initialization failed", str(error.exception))

    def test_parent_path_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE._validate(
                {
                    "verdict": "FAIL",
                    "summary": "Unsafe path.",
                    "findings": [
                        {
                            "priority": "P1",
                            "path": "../secret",
                            "line": 1,
                            "title": "Bad path",
                            "body": "Path escapes the repository.",
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
