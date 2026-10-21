"""Offline hosting, dependency and retained recovery CLI contracts."""
import json
from pathlib import Path
import tomllib
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


class OperationsContractTests(unittest.TestCase):
    def test_portable_check_cache_paths_are_initialized_by_a_shell_step(self):
        portable = (WORKFLOW_ROOT / "portable-checks.yml").read_text(encoding="utf-8")

        self.assertNotRegex(
            portable,
            r"(?m)^\s+(?:UV_CACHE_DIR|npm_config_cache):\s*\$\{\{\s*runner\.temp\s*\}\}",
        )
        configure = portable.index("      - name: Configure runner-local package caches")
        setup_python = portable.index("      - name: Set up Python 3.12")
        setup_uv = portable.index("      - name: Set up uv")
        self.assertLess(configure, setup_python)
        self.assertLess(configure, setup_uv)
        configure_step = portable[configure:setup_python]
        self.assertIn('echo "UV_CACHE_DIR=$RUNNER_TEMP/sportsrank-uv-cache" >> "$GITHUB_ENV"', configure_step)
        self.assertIn('mkdir -p "$RUNNER_TEMP/sportsrank-uv-cache"', configure_step)


    def test_firebase_and_node_boundaries_are_hosting_only(self):
        firebase = json.loads((REPO_ROOT / "firebase.json").read_text(encoding="utf-8"))
        self.assertEqual(firebase["hosting"]["public"], "website")
        self.assertNotIn("functions", firebase)

        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertRegex(package["devDependencies"]["firebase-tools"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(package["scripts"]["serve"], "firebase emulators:start --only hosting")
        self.assertNotIn("deploy", package["scripts"])
        self.assertNotIn("hosting:deploy", package["scripts"])
        self.assertNotRegex(" ".join(package["scripts"].values()), r"firebase\s+deploy")
        self.assertNotIn("functions", " ".join(package["scripts"].values()))

        lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
        pinned = package["devDependencies"]["firebase-tools"]
        self.assertEqual(lock["packages"][""]["devDependencies"]["firebase-tools"], pinned)
        self.assertEqual(lock["packages"]["node_modules/firebase-tools"]["version"], pinned)


    def test_python_runtime_retains_the_supported_cfbd_sdk_range(self):
        project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("cfbd>=5.26,<6", project["project"]["dependencies"])


    def test_operator_guidance_excludes_retired_unsafe_commands(self):
        """Reviewed-unsafe recovery commands must not remain operator guidance."""
        handoff = (REPO_ROOT / "docs/operations/2026-season-recovery-morning.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("--clone-published", handoff)
        self.assertNotIn("npm run deploy", handoff)


    def test_publication_cli_has_one_manual_operation_surface(self):
        from cfb.publication_cli import build_parser

        parser = build_parser()
        help_text = parser.format_help()
        for operation in ("prepare", "seal-only", "execute", "reconcile", "verify-only"):
            self.assertIn(operation, help_text)
        with self.assertRaises(SystemExit):
            parser.parse_args(["publish"])


    def test_protected_cli_grammar_is_shared_across_operation_modes(self):
        from cfb.publication_cli import build_parser

        parser = build_parser()
        common = [
            "--sealed-reference", "/tmp/sealed-attempt-reference.json",
            "--baseline-record", "/tmp/baseline.json",
            "--attempt-id", "run-123",
            "--retrieval-directory", "/tmp/receipts",
            "--result", "/tmp/result.json",
        ]
        for operation in ("reconcile", "verify-only"):
            with self.subTest(operation=operation):
                args = parser.parse_args([operation, *common])
                self.assertEqual(args.operation, operation)
                self.assertEqual(args.attempt_id, "run-123")
                self.assertEqual(args.sealed_reference.name, "sealed-attempt-reference.json")

        execute = parser.parse_args([
            "execute", *common, "--purpose", "normal",
            "--prior-reference", "/tmp/prior-reference.json",
            "--prior-baseline-record", "/tmp/prior-baseline.json",
        ])
        self.assertEqual(execute.purpose, "normal")
        self.assertEqual(execute.prior_reference.name, "prior-reference.json")

        external = parser.parse_args([
            "execute", *common, "--purpose", "normal",
            "--prior-external-reference", "/tmp/external-prior.json",
        ])
        self.assertEqual(external.prior_external_reference.name, "external-prior.json")

        seal = parser.parse_args([
            "seal-only", "--context", "/tmp/publication-context.json",
            "--attempt-id", "run-123", "--purpose", "rollback",
            "--prior-reference", "/tmp/prior-reference.json",
            "--prior-baseline-record", "/tmp/prior-baseline.json",
        ])
        self.assertEqual(seal.purpose, "rollback")

        initial = parser.parse_args([
            "seal-only", "--context", "/tmp/publication-context.json",
            "--attempt-id", "run-123", "--purpose", "normal",
            "--initial-baseline",
        ])
        self.assertTrue(initial.initial_baseline)
