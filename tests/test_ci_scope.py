"""CI routing is exercised against real commit trees, without GitHub or CFBD."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_scope.py"


class CIRoutingTests(unittest.TestCase):
    def test_real_changes_select_release_readiness_including_moves_and_deletions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", str(root), *args], stderr=subprocess.PIPE, text=True
                ).strip()
            git("init", "-q")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "user.name", "CI fixture")
            (root / "website").mkdir()
            (root / "website" / "old.html").write_text("retained")
            git("add", ".")
            git("commit", "-qm", "baseline")
            base = git("rev-parse", "HEAD")
            for path, operation, expected in (
                ("cfb/model.py", "write", False),
                ("docs/guide.md", "write", False),
                ("website/new.html", "write", True),
                ("config/sr7-recovery-inputs.json", "write", True),
                ("config/sr7-local-validation-receipt.json", "write", True),
                ("firebase.json", "write", True),
                ("website/old.html", "delete", True),
                ("website/old.html", "move", True),
            ):
                with self.subTest(path=path, operation=operation):
                    git("reset", "--hard", base)
                    file = root / path
                    if operation == "delete":
                        file.unlink()
                    elif operation == "move":
                        file.rename(root / "moved.html")
                    else:
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_text("changed")
                    git("add", "-A")
                    git("commit", "-qm", "change")
                    for name, event in (
                        ("pull_request", {"pull_request": {"base": {"sha": base}}}),
                        ("push", {"before": base}),
                    ):
                        result = self.run_scope(root, name, event)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, f"release_readiness={str(expected).lower()}\n")
            initial = self.run_scope(root, "push", {"before": "0" * 40})
            self.assertEqual(initial.stdout, "release_readiness=true\n")
            for name, event in (
                ("push", {}), ("push", {"before": "HEAD~1"}),
                ("push", {"before": "f" * 40}), ("schedule", {}),
            ):
                with self.subTest(invalid_event=name, event=event):
                    result = self.run_scope(root, name, event)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("release_readiness=false", result.stdout)

    @staticmethod
    def run_scope(root, name, event):
        import sys
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
            json.dump(event, handle)
            handle.flush()
            return subprocess.run(
                [sys.executable, str(SCRIPT)], cwd=root,
                env={**os.environ, "GITHUB_EVENT_NAME": name, "GITHUB_EVENT_PATH": handle.name},
                capture_output=True, text=True, timeout=10,
            )

    def test_workflow_reports_every_pr_and_only_routes_release_receipt_step(self):
        workflow = (ROOT / ".github/workflows/portable-checks.yml").read_text()
        self.assertRegex(workflow, r"push:\s+branches: \[main\]\s+pull_request:")
        self.assertNotIn("paths-ignore:", workflow)
        self.assertNotIn("paths:", workflow)
        self.assertIn("name: Run locked offline checks", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertIn("github.event.pull_request.number || github.ref", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn('python -m scripts.ci_scope >> "$GITHUB_OUTPUT"', workflow)
        self.assertIn("if: steps.scope.outputs.release_readiness == 'true'", workflow)
        job, steps = workflow.split("    steps:", 1)
        self.assertNotRegex(job, r"(?m)^\s+if:")
        for step in steps.split("      - "):
            if "python -m unittest" in step:
                self.assertNotRegex(step, r"(?m)^\s+if:")
        self.assertNotIn("npm ci", workflow)
        self.assertNotIn("setup-node", workflow)
