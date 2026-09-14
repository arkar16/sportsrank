"""Static checks for the protected hosting workflow and portable checks."""

import json
from pathlib import Path
import subprocess
import textwrap
import tomllib
import unittest
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


class OperationsContractTests(unittest.TestCase):
    @staticmethod
    def _transport_bootstrap_script() -> str:
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        start = source.index(
            "      - name: Verify transported execution source against the authenticated Git object graph"
        )
        body_start = source.index("        run: |\n", start) + len("        run: |\n")
        body_end = source.index("\n      - name:", body_start)
        return textwrap.dedent(source[body_start:body_end])

    @staticmethod
    def _transport_fixture(root: Path) -> tuple[Path, Path, str]:
        repository = root / "candidate"
        repository.mkdir()
        for relative, value in (
            ("cfb/runtime.py", "print('runtime')\n"),
            ("tools/helper.py", "print('helper')\n"),
            ("README.md", "# fixture\n"),
            ("pyproject.toml", "[project]\nname='fixture'\nversion='0.0.0'\n"),
            ("uv.lock", "version = 1\n"),
        ):
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet", "--object-format=sha1"],
            cwd=repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "add", "README.md", "cfb", "tools", "pyproject.toml", "uv.lock"],
            cwd=repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            [
                "git", "-c", "user.name=SportsRank Fixture",
                "-c", "user.email=sportsrank@example.invalid",
                "commit", "--quiet", "-m", "candidate",
            ],
            cwd=repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository, text=True
        ).strip()
        preparation = root / "runner" / "preparation"
        preparation.mkdir(parents=True)
        subprocess.run(
            ["git", "bundle", "create", str(preparation / "candidate.bundle"), "HEAD"],
            cwd=repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        archive = subprocess.check_output(
            ["git", "archive", "--format=tar", commit, "README.md", "cfb", "tools", "pyproject.toml", "uv.lock"],
            cwd=repository,
        )
        with (preparation / "execution-source.tar.gz").open("wb") as output:
            subprocess.run(["gzip", "-n"], input=archive, stdout=output, check=True)
        (preparation / "publication-context.json").write_text(
            json.dumps({"candidate_commit": commit}), encoding="utf-8"
        )
        return root / "runner", preparation, commit

    def _run_transport_bootstrap(self, runner: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", self._transport_bootstrap_script()],
            env={"RUNNER_TEMP": str(runner), "PATH": "/usr/bin:/bin"},
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_publication_workflow_is_manual_and_portable_checks_are_non_publishing(self):
        workflows = sorted(WORKFLOW_ROOT.glob("*.yml")) + sorted(WORKFLOW_ROOT.glob("*.yaml"))
        self.assertEqual(
            [path.name for path in workflows],
            ["firebase-hosting-publish.yml", "portable-checks.yml"],
        )

        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertNotRegex(source, r"(?m)^\s*(push|pull_request|schedule):")
        for operation in ("prepare", "execute", "reconcile", "verify-only", "rollback", "correction"):
            self.assertIn(operation, source)
        self.assertIn("actions/checkout@v6", source)
        self.assertEqual(source.count("actions/checkout@v6"), 1)
        self.assertIn("actions/setup-python@v6", source)
        self.assertIn("astral-sh/setup-uv@v9.0.0", source)
        self.assertIn("python -m cfb.publication_cli", source)
        self.assertIn("github.sha", source)
        self.assertIn("refs/heads/main", source)
        self.assertIn("GITHUB_TOKEN", source)
        self.assertIn("actions: read", source)
        self.assertIn("production", source)
        self.assertNotIn("candidate_sha", source)
        self.assertNotIn("base_sha", source)
        self.assertNotIn("FirebaseExtended/action-hosting-deploy", source)
        self.assertNotIn("npm run deploy", source)
        self.assertNotIn("firebase deploy", source)
        self.assertNotIn("inputs.ref", source)

        portable = (WORKFLOW_ROOT / "portable-checks.yml").read_text(encoding="utf-8")
        self.assertRegex(portable, r"(?m)^\s*(push|pull_request):")
        self.assertIn("CFBD_API_KEY", portable)
        self.assertIn("uv run --locked python -m unittest", portable)
        self.assertIn("npm run build", portable)
        self.assertNotIn("FirebaseExtended/action-hosting-deploy", portable)
        self.assertNotIn("firebase deploy", portable)

    def test_workflow_requires_main_and_exact_immutable_candidate_in_prepare(self):
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")

        self.assertIn("git cat-file -t", source)
        self.assertIn('git rev-parse --verify "$sha^{commit}"', source)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', source)
        self.assertIn('test "$HEAD_SHA" = "$GITHUB_SHA"', source)
        self.assertIn("git rev-parse HEAD", source)
        self.assertIn('candidate-commit "$GITHUB_SHA"', source)
        self.assertIn("candidate.bundle", source)
        self.assertIn("git bundle create", source)
        self.assertIn("bind_merged_candidate", source)
        self.assertNotIn("merge-base --is-ancestor", source)

    def test_protected_job_consumes_prepared_artifact_without_checkout_or_rebuild(self):
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        prepare, protected = source.split("\n  protected:", 1)

        self.assertEqual(source.count("actions/upload-artifact@v4"), 2)
        self.assertEqual(source.count("actions/download-artifact@v4"), 1)
        self.assertEqual(source.count("actions/attest-build-provenance@v2"), 1)
        self.assertIn("prepare_review_package", prepare)
        self.assertIn("bind_merged_candidate", prepare)
        self.assertIn("publication-context.json", prepare)
        self.assertIn("publication-preparation-manifest.json", prepare)
        self.assertIn(
            "subject-path: ${{ runner.temp }}/preparation/publication-preparation-manifest.json",
            prepare,
        )
        self.assertIn("Package record SHA-256", prepare)
        self.assertIn("Preparation manifest SHA-256", prepare)
        self.assertIn("actions/checkout@v6", prepare)
        self.assertIn("jq -e '{schema_version, record_type, repository, target, candidate_commit", prepare)
        self.assertNotIn('cat "$RUNNER_TEMP/preparation/publication-context.json" >> "$GITHUB_STEP_SUMMARY"', prepare)
        self.assertIn("sha256sum -c", protected)
        self.assertIn("package.json", protected)
        self.assertIn("package.json.sha256", protected)
        self.assertIn("publication-preparation-manifest.json", protected)
        self.assertIn("publication_cli", protected)
        self.assertIn("Verify the authenticated preparation and state run origins", protected)
        self.assertIn("gh run view", protected)
        self.assertIn("candidate.bundle.sha256", protected)
        self.assertIn("archive --format=tar", protected)
        self.assertIn("README.md cfb tools pyproject.toml uv.lock", protected)
        self.assertIn("execution-source.expected.tar.gz", protected)
        self.assertIn("cmp", protected)
        bootstrap = protected.index("Verify transported execution source against the authenticated Git object graph")
        extraction = protected.index("Materialize the exact execution code without a source checkout")
        runtime_install = protected.index("Install the transported locked runtime")
        self.assertLess(bootstrap, extraction)
        self.assertLess(bootstrap, runtime_install)
        self.assertIn("bundle verify", protected)
        self.assertIn('git -C "$trusted_repo" fetch', protected)
        self.assertIn('cat-file -t "$candidate"', protected)
        self.assertIn("rev-parse refs/heads/candidate", protected)
        self.assertIn("FIREBASE_ACCESS_TOKEN", protected)
        self.assertIn("google-github-actions/auth@v2", protected)
        self.assertNotIn("actions/checkout", protected)
        self.assertNotIn("npm ci", protected)
        self.assertNotIn("FirebaseExtended/action-hosting-deploy", protected)
        self.assertIn("arkar16/sportsrank", protected)
        self.assertIn("sportsrank-837af", protected)
        self.assertIn("channel: live", protected)
        self.assertIn("predecessor_run_id", protected)
        self.assertIn("gh run download", protected)
        self.assertIn("publication-state", protected)
        self.assertIn("sportsrank-publication-", protected)

    def test_transport_bootstrap_rejects_corrupt_runtime_before_materialization(self):
        with TemporaryDirectory() as directory:
            runner, preparation, _commit = self._transport_fixture(Path(directory))
            (preparation / "execution-source.tar.gz").write_bytes(b"corrupt runtime")

            result = self._run_transport_bootstrap(runner)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("differ", result.stdout + result.stderr)
            self.assertTrue((runner / "trusted-candidate.git" / "refs" / "heads" / "candidate").exists())
            self.assertFalse((runner / "execution").exists())

    def test_transport_bootstrap_rejects_corrupt_bundle_before_candidate_ref(self):
        with TemporaryDirectory() as directory:
            runner, preparation, _commit = self._transport_fixture(Path(directory))
            (preparation / "candidate.bundle").write_bytes(b"corrupt candidate bundle")

            result = self._run_transport_bootstrap(runner)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((runner / "trusted-candidate.git").exists())
            self.assertFalse((runner / "trusted-candidate.git" / "refs" / "heads" / "candidate").exists())
            self.assertFalse((runner / "execution").exists())


    def test_protected_production_environment_is_the_only_publish_gate(self):
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        _, protected = source.split("\n  protected:", 1)

        self.assertRegex(protected, r"(?ms)^\s+environment:\n\s+name: production\n")
        self.assertIn("actions: read", protected)
        self.assertIn("operation", protected)
        self.assertIn("fresh", protected)
        self.assertIn("approval", protected)

    def test_firebase_and_node_boundaries_are_hosting_only(self):
        firebase = json.loads((REPO_ROOT / "firebase.json").read_text(encoding="utf-8"))
        self.assertEqual(firebase["hosting"]["public"], "website")
        self.assertNotIn("functions", firebase)

        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["devDependencies"]["firebase-tools"], "15.29.0")
        self.assertEqual(package["scripts"]["serve"], "firebase emulators:start --only hosting")
        self.assertNotIn("deploy", package["scripts"])
        self.assertNotIn("hosting:deploy", package["scripts"])
        self.assertNotRegex(" ".join(package["scripts"].values()), r"firebase\s+deploy")
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

    def test_unsafe_morning_handoff_is_blocked_by_authoritative_plan(self):
        """Reviewed-unsafe recovery commands must not remain operator guidance."""
        handoff = (REPO_ROOT / "docs/operations/2026-season-recovery-morning.md").read_text(
            encoding="utf-8"
        )
        plan = (REPO_ROOT / "docs/plans/2026-p0-recovery-and-backfill.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("procedure, not authorization", handoff)
        self.assertIn("2026-p0-recovery-and-backfill.md", handoff)
        self.assertIn("SR-15", handoff)
        self.assertNotIn("--clone-published", handoff)
        self.assertNotIn("npm run deploy", handoff)

        for marker in (
            "CFBD_API_KEY",
            "exactly six calls",
            "2024 FINAL",
            "2025 FINAL",
            "2026 PRESEASON",
            "Gate 1",
            "Gate 2",
            "systemd",
            "Pi/T7",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, plan)

    def test_publication_cli_has_one_manual_operation_surface(self):
        from cfb.publication_cli import build_parser

        parser = build_parser()
        help_text = parser.format_help()
        for operation in ("prepare", "execute", "reconcile", "verify-only", "rollback", "correction"):
            self.assertIn(operation, help_text)
        with self.assertRaises(SystemExit):
            parser.parse_args(["publish"])

    def test_protected_cli_grammar_is_shared_across_operation_modes(self):
        from cfb.publication_cli import build_parser

        parser = build_parser()
        common = [
            "--context", "/tmp/publication-context.json",
            "--attempt-id", "run-123",
            "--attempt-manifest", "/tmp/publication-run.json",
            "--prior-context", "/tmp/prior-context.json",
            "--prior-manifest", "/tmp/prior-run.json",
            "--retrieval-directory", "/tmp/receipts",
            "--result", "/tmp/result.json",
        ]
        for operation in ("execute", "reconcile", "verify-only", "rollback", "correction"):
            with self.subTest(operation=operation):
                args = parser.parse_args([operation, *common])
                self.assertEqual(args.operation, operation)
                self.assertEqual(args.attempt_id, "run-123")
                self.assertEqual(args.attempt_manifest.name, "publication-run.json")


if __name__ == "__main__":
    unittest.main()
