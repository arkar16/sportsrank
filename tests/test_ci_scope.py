"""The CI check reports on each PR without gating direct publication."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CIRoutingTests(unittest.TestCase):
    def test_workflow_reports_every_pr_without_duplicate_branch_pushes(self):
        workflow = (ROOT / ".github/workflows/portable-checks.yml").read_text()
        self.assertRegex(workflow, r"push:\s+branches: \[main\]\s+pull_request:")
        self.assertNotIn("paths-ignore:", workflow)
        self.assertNotIn("paths:", workflow)
        self.assertIn("name: Run locked offline checks", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertIn("github.event.pull_request.number || github.ref", workflow)
        self.assertNotIn("cfb.public_site verify", workflow)
        self.assertNotIn("scripts.ci_scope", workflow)
        self.assertNotIn("npm ci", workflow)
        self.assertNotIn("setup-node", workflow)
