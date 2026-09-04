"""Static checks for the credential-safe hosting and recovery operations path."""

import json
from pathlib import Path
import tomllib
import unittest

from cfb.recovery import build_parser


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


class OperationsContractTests(unittest.TestCase):
    def test_only_manual_firebase_workflow_remains(self):
        workflows = sorted(WORKFLOW_ROOT.glob("*.yml")) + sorted(WORKFLOW_ROOT.glob("*.yaml"))
        self.assertEqual([path.name for path in workflows], ["firebase-hosting-publish.yml"])

        source = workflows[0].read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertNotRegex(source, r"(?m)^\s*(push|pull_request|schedule):")
        self.assertIn("actions/checkout@v6", source)
        self.assertIn("actions/setup-python@v6", source)
        self.assertIn("astral-sh/setup-uv@v9", source)
        self.assertIn("validate website --json", source)
        self.assertIn("needs: validate", source)
        self.assertIn("name: production", source)
        self.assertNotIn("candidate:", source)

    def test_firebase_and_node_boundaries_are_hosting_only(self):
        firebase = json.loads((REPO_ROOT / "firebase.json").read_text(encoding="utf-8"))
        self.assertEqual(firebase["hosting"]["public"], "website")
        self.assertNotIn("functions", firebase)

        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["devDependencies"]["firebase-tools"], "15.29.0")
        self.assertEqual(package["scripts"]["serve"], "firebase emulators:start --only hosting")
        self.assertEqual(package["scripts"]["deploy"], "firebase deploy --only hosting")
        self.assertNotIn("functions", " ".join(package["scripts"].values()))

        lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["packages"][""]["devDependencies"]["firebase-tools"], "15.29.0")

    def test_python_lock_declares_only_direct_runtime_dependencies(self):
        project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("cfbd>=5.26,<6", project["project"]["dependencies"])
        self.assertEqual(
            {
                dependency.split(">", 1)[0].split("<", 1)[0].split("=", 1)[0]
                for dependency in project["project"]["dependencies"]
            },
            {"beautifulsoup4", "cfbd", "html5lib", "lxml", "numpy", "pandas"},
        )

    def test_morning_handoff_examples_match_recovery_cli(self):
        """Every documented recovery invocation remains parseable by the CLI."""
        parser = build_parser()
        examples = (
            ["smoke", "2026", "--classification", "FBS", "--category", "scheduled"],
            ["fetch", "2024", "--classification", "FBS"],
            [
                "build",
                "2024",
                "--classification",
                "FBS",
                "--release-id",
                "2024-recovery",
                "--output-root",
                ".sportsrank/releases",
                "--published-site",
                "website",
                "--clone-published",
            ],
            ["validate", ".sportsrank/releases/2024-recovery", "--json"],
            ["promote", ".sportsrank/releases/2026-recovery", "website"],
        )
        for argv in examples:
            with self.subTest(argv=argv):
                self.assertEqual(parser.parse_args(argv).command, argv[0])

        handoff = (REPO_ROOT / "docs/operations/2026-season-recovery-morning.md").read_text(
            encoding="utf-8"
        )
        for marker in (
            "CFBD_API_KEY",
            '"calls": 1',
            "2024-recovery",
            "2025-recovery",
            "2026-recovery",
            "production",
            "systemd",
            "Samsung T7",
            "Google",
            "Drive",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, handoff)


if __name__ == "__main__":
    unittest.main()
