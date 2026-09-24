"""Static checks for the protected hosting workflow and portable checks."""

import json
import os
from pathlib import Path
import subprocess
import textwrap
import tomllib
import unittest
from tempfile import TemporaryDirectory
from cfb.candidate_tree import create_candidate_tree_archive

from cfb.publication_records import (
    BaselineRecord,
    ManagedResourceEvidence,
    ProviderIdentity,
    ProviderTarget,
    SourceProvenance,
    ValidatedPackageRecord,
    canonical_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


class OperationsContractTests(unittest.TestCase):
    @staticmethod
    def _sealed_reference_input_script() -> str:
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        start = source.index("      - name: Preserve the exact sealed reference input")
        body_start = source.index("        run: |\n", start) + len("        run: |\n")
        body_end = source.index("\n      - name:", body_start)
        return textwrap.dedent(source[body_start:body_end])

    @staticmethod
    def _prior_transport_script() -> str:
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        start = source.index(
            "      - name: Retrieve the exact predecessor baseline when a successor needs it"
        )
        body_start = source.index("        run: |\n", start) + len("        run: |\n")
        body_end = source.index("\n      - name:", body_start)
        return textwrap.dedent(source[body_start:body_end])

    @staticmethod
    def _preparation_transport_script() -> str:
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        start = source.index("      - name: Download the exact prepared package transport")
        body_start = source.index("        run: |\n", start) + len("        run: |\n")
        body_end = source.index("\n      - name:", body_start)
        return textwrap.dedent(source[body_start:body_end])

    @staticmethod
    def _external_reference_transport_script() -> str:
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        start = source.index("      - name: Preserve the exact external predecessor reference input")
        body_start = source.index("        run: |\n", start) + len("        run: |\n")
        body_end = source.index("\n      - name:", body_start)
        return textwrap.dedent(source[body_start:body_end])

    @staticmethod
    def _attestation_bootstrap_script() -> str:
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        start = source.index(
            "      - name: Verify the preparation attestation before runtime materialization"
        )
        body_start = source.index("        run: |\n", start) + len("        run: |\n")
        body_end = source.index("\n      - name:", body_start)
        return textwrap.dedent(source[body_start:body_end])

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
    def _baseline_reconstruction_python() -> str:
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        start = source.index(
            "      - name: Reconstruct the exact baseline record from the sealed package"
        )
        body_start = source.index("          uv run --locked python - <<'PY'\n", start)
        body_start = source.index("          import os\n", body_start)
        body_end = source.index("\n          PY", body_start)
        return textwrap.dedent(source[body_start:body_end])

    @staticmethod
    def _durable_baseline_fixture(root: Path) -> tuple[Path, Path, Path, Path, bytes]:
        target = ProviderTarget("sportsrank-837af", "sportsrank-837af", "live")
        identity = ProviderIdentity(
            target,
            "sites/sportsrank-837af/channels/live/releases/prior",
            "sites/sportsrank-837af/versions/prior",
        )
        managed = tuple(
            ManagedResourceEvidence(
                path,
                "1" * 64,
                "2" * 64,
                1,
                {
                    "project_id": target.project,
                    "messaging_sender_id": "1234",
                    "auth_domain": "sportsrank-837af.firebaseapp.com",
                    "storage_bucket": "sportsrank-837af.firebasestorage.app",
                },
            )
            for path in ("/__/firebase/init.js", "/__/firebase/init.json")
        )
        baseline = BaselineRecord(
            target,
            identity,
            identity,
            identity,
            "2026-09-14T00:00:00",
            "3" * 64,
            "4" * 64,
            "5" * 64,
            "6" * 64,
            1,
            1,
            SourceProvenance("unknown", None),
            managed,
            {"capture.json": "7" * 64},
            "allowlisted-v1",
        )
        package = ValidatedPackageRecord.create(
            candidate_commit="a" * 40,
            bundle_sha256="b" * 64,
            inventory_sha256="c" * 64,
            configuration_sha256="d" * 64,
            expected_baseline_sha256=baseline.digest,
            retained_inputs_sha256="e" * 64,
            validation_sha256="f" * 64,
            expected_predecessor=identity.to_dict(),
        )
        package_path = root / "validated-package.json"
        package_path.write_bytes(canonical_json(package.to_dict()))
        manifest_path = root / "candidate-manifest.json"
        manifest_path.write_bytes(canonical_json({
            "baseline_provenance": {
                "record": baseline.to_dict(),
                "record_digest": baseline.digest,
            }
        }))
        trusted_path = root / "sr7-recovery-inputs.json"
        trusted_path.write_bytes(canonical_json({
            "baseline": {"record_sha256": baseline.digest},
        }))
        output_path = root / "baseline.json"
        return package_path, manifest_path, trusted_path, output_path, canonical_json(baseline.to_dict())

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
            ("config/sr7-recovery-inputs.json", '{"schema_version":1}\n'),
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
            [
                "git",
                "add",
                "README.md",
                "cfb",
                "tools",
                "pyproject.toml",
                "uv.lock",
                "config",
            ],
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
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True
        ).strip()
        preparation = root / "runner" / "preparation"
        preparation.mkdir(parents=True)
        create_candidate_tree_archive(
            repository, commit, preparation / "candidate-tree.tar.gz"
        )
        archive = subprocess.check_output(
            [
                "git",
                "archive",
                "--format=tar",
                commit,
                "README.md",
                "cfb",
                "tools",
                "pyproject.toml",
                "uv.lock",
                "config",
            ],
            cwd=repository,
        )
        with (preparation / "execution-source.tar.gz").open("wb") as output:
            subprocess.run(["gzip", "-n"], input=archive, stdout=output, check=True)
        (preparation / "publication-context.json").write_text(
            json.dumps({"candidate_commit": commit}), encoding="utf-8"
        )
        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        tree_entries = []
        for entry in subprocess.check_output(
            ["git", "ls-tree", "-r", "-z", commit], cwd=repository
        ).split(b"\0"):
            if entry:
                meta, path = entry.split(b"\t", 1)
                mode, kind, oid = meta.decode("ascii").split(" ")
                tree_entries.append({"path": path.decode(), "mode": mode, "type": kind, "sha": oid})
        (fake_bin / "gh").write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" != api ]; then exit 1; fi\n"
            "shift\n"
            "if [ \"$1\" = --repo ]; then\n"
            "  echo 'unsupported gh api --repo flag' >&2\n"
            "  exit 2\n"
            "fi\n"
            "case \"$1\" in\n"
            "  repos/arkar16/sportsrank/commits/*)\n"
            f"    printf '%s\\n' '{json.dumps({'sha': commit, 'commit': {'tree': {'sha': tree}}})}'\n"
            "    ;;\n"
            "  repos/arkar16/sportsrank/git/trees/*)\n"
            f"    printf '%s\\n' '{json.dumps({'tree': tree_entries})}'\n"
            "    ;;\n"
            "  *) exit 3 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        (fake_bin / "gh").chmod(0o755)
        return root / "runner", preparation, commit

    def _run_transport_bootstrap(self, runner: Path) -> subprocess.CompletedProcess[str]:
        candidate = json.loads(
            (runner / "preparation" / "publication-context.json").read_text(
                encoding="utf-8"
            )
        )["candidate_commit"]
        return subprocess.run(
            ["bash", "-c", self._transport_bootstrap_script()],
            env={
                "RUNNER_TEMP": str(runner),
                "PREPARATION_HEAD_SHA": candidate,
                "GH_TOKEN": "fixture-token",
                "PATH": f"{runner.parent / 'fake-bin'}:/usr/bin:/bin",
            },
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
        for operation in ("prepare", "seal-only", "execute", "reconcile", "verify-only"):
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

    def test_portable_check_cache_paths_are_initialized_by_a_shell_step(self):
        portable = (WORKFLOW_ROOT / "portable-checks.yml").read_text(encoding="utf-8")

        self.assertNotRegex(
            portable,
            r"(?m)^\s+(?:UV_CACHE_DIR|npm_config_cache):\s*\$\{\{\s*runner\.temp\s*\}\}",
        )
        configure = portable.index("      - name: Configure runner-local package caches")
        setup_python = portable.index("      - name: Set up Python 3.12")
        setup_uv = portable.index("      - name: Set up uv")
        setup_node = portable.index("      - name: Set up Node.js")
        self.assertLess(configure, setup_python)
        self.assertLess(configure, setup_uv)
        self.assertLess(configure, setup_node)
        configure_step = portable[configure:setup_python]
        self.assertIn('echo "UV_CACHE_DIR=$RUNNER_TEMP/sportsrank-uv-cache" >> "$GITHUB_ENV"', configure_step)
        self.assertIn('echo "npm_config_cache=$RUNNER_TEMP/sportsrank-npm-cache" >> "$GITHUB_ENV"', configure_step)
        self.assertIn('mkdir -p "$RUNNER_TEMP/sportsrank-uv-cache" "$RUNNER_TEMP/sportsrank-npm-cache"', configure_step)

    def test_workflow_requires_main_and_exact_immutable_candidate_in_prepare(self):
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")

        self.assertIn("git cat-file -t", source)
        self.assertIn('git rev-parse --verify "$sha^{commit}"', source)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', source)
        self.assertIn('test "$HEAD_SHA" = "$GITHUB_SHA"', source)
        self.assertIn("git rev-parse HEAD", source)
        self.assertIn('candidate-commit "$GITHUB_SHA"', source)
        self.assertIn("candidate-tree.tar.gz", source)
        self.assertIn("python -m cfb.candidate_tree", source)
        self.assertIn('candidate_tree="$RUNNER_TEMP/candidate-tree.tar.gz"', source)
        self.assertNotIn("git bundle create", source)
        self.assertIn("prepare-reviewed", source)
        self.assertNotIn("merge-base --is-ancestor", source)

    def test_protected_job_consumes_prepared_artifact_without_checkout_or_rebuild(self):
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        prepare, protected = source.split("\n  protected:", 1)

        self.assertEqual(source.count("actions/upload-artifact@v4"), 2)
        self.assertEqual(source.count("actions/download-artifact@v4"), 0)
        self.assertEqual(source.count("actions/attest-build-provenance@v2"), 1)
        self.assertIn("prepare-reviewed", prepare)
        self.assertIn("--local-validation-receipt", prepare)
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
        self.assertIn("id: preparation_transport", protected)
        self.assertIn("gh run download", protected)
        self.assertIn('echo "available=false" >> "$GITHUB_OUTPUT"', protected)
        self.assertIn("package.json", protected)
        self.assertIn("package.json.sha256", protected)
        self.assertIn("publication-preparation-manifest.json", protected)
        self.assertIn("gh attestation verify", protected)
        self.assertIn("gh api", protected)
        self.assertIn("commit.tree.sha", protected)
        self.assertIn("reachable-objects.txt", protected)
        self.assertIn("github-tree.json", protected)
        self.assertIn("--signer-workflow", protected)
        self.assertIn("--source-ref", protected)
        self.assertIn("--source-digest", protected)
        self.assertIn("--deny-self-hosted-runners", protected)
        self.assertIn("publication_cli", protected)
        self.assertIn("Verify the authenticated preparation origin and exact reference input", protected)
        self.assertIn("gh run view", protected)
        self.assertIn("candidate-tree.tar.gz.sha256", protected)
        self.assertIn("archive --format=tar", protected)
        self.assertIn("README.md cfb tools pyproject.toml uv.lock", protected)
        self.assertIn("execution-source.expected.tar.gz", protected)
        self.assertIn("cmp", protected)
        bootstrap = protected.index("Verify transported execution source against the authenticated Git object graph")
        extraction = protected.index("Materialize the exact execution code without a source checkout")
        runtime_install = protected.index("Install the transported locked runtime")
        self.assertLess(bootstrap, extraction)
        self.assertLess(bootstrap, runtime_install)
        self.assertIn("candidate_current_tree", protected)
        self.assertIn("declared-objects.txt", protected)
        self.assertIn('cp -R "$tree_stage/objects/." "$trusted_repo/objects/"', protected)
        self.assertNotIn('git -C "$trusted_repo" fetch', protected)
        self.assertIn('cat-file -t "$candidate"', protected)
        self.assertIn('rev-parse "$candidate^{tree}"', protected)
        self.assertIn("FIREBASE_ACCESS_TOKEN", protected)
        self.assertIn("google-github-actions/auth@v2", protected)
        attestation = protected.index(
            "Verify the preparation attestation before runtime materialization"
        )
        self.assertNotIn("actions/checkout", protected)
        self.assertNotIn("npm ci", protected)
        self.assertNotIn("FirebaseExtended/action-hosting-deploy", protected)
        self.assertIn("arkar16/sportsrank", protected)
        self.assertIn("sportsrank-837af", protected)
        self.assertIn("channel: live", protected)
        self.assertIn("sealed-attempt-reference.json", protected)
        self.assertIn("prior_external_reference", protected)
        self.assertIn("--prior-external-reference", protected)
        self.assertIn("--initial-baseline", protected)
        self.assertIn(
            'tail -c 1 "$RUNNER_TEMP/prior-external-predecessor-reference.json"',
            protected,
        )
        self.assertIn("Bootstrap exact runtime and baseline from the durable reference", protected)
        self.assertIn("Reconstruct the exact baseline record from the sealed package", protected)
        self.assertIn("Bootstrap the predecessor package from its durable reference", protected)
        self.assertIn("gh run download", protected)
        self.assertIn('echo "available=false" >> "$GITHUB_OUTPUT"', protected)
        self.assertIn("steps.prior_preparation.outputs.available != 'true'", protected)
        self.assertIn(
            "Reconstruct the predecessor baseline record from the sealed package",
            protected,
        )
        reconstruct = protected.index(
            "      - name: Reconstruct the predecessor baseline record from the sealed package"
        )
        reconstruct_end = protected.index("\n      - name:", reconstruct + 1)
        self.assertIn(
            "steps.prior_preparation.outputs.available != 'true'",
            protected[reconstruct:reconstruct_end],
        )
        self.assertNotIn("cp \"$RUNNER_TEMP/preparation/baseline.json\" \"$RUNNER_TEMP/prior-baseline.json\"", protected)
        self.assertIn("candidate-manifest.json", protected)
        self.assertIn("sealed baseline does not match committed input pin", protected)
        self.assertIn('authenticated_commit="$(gh api "repos/arkar16/sportsrank/commits/$candidate")"', protected)
        self.assertIn('rev-parse "$candidate^{tree}"', protected)
        self.assertIn(
            'git -C "$trusted_repo" rev-list --objects --no-object-names "$candidate^{tree}"',
            protected,
        )
        self.assertIn(
            'cmp "$RUNNER_TEMP/reachable-objects.txt" "$RUNNER_TEMP/declared-objects.txt"',
            protected,
        )
        self.assertIn("state-free recovery", protected)
        self.assertIn("releases/assets/$asset_id", protected)
        self.assertIn("publication-state", protected)
        self.assertIn("sportsrank-publication-", protected)
        reference_step = protected.index("Preserve the exact sealed reference input")
        fallback = protected.index("Bootstrap exact runtime and baseline from the durable reference")
        self.assertLess(reference_step, fallback)
        self.assertIn("printf '%s' \"$SEALED_REFERENCE\"", protected)
        self.assertIn("tail -c 1 \"$RUNNER_TEMP/sealed-attempt-reference.json\"", protected)
        self.assertIn("OPERATION: ${{ inputs.operation }}", protected)
        self.assertLess(attestation, extraction)
        self.assertLess(attestation, runtime_install)

    def test_workflow_reference_transport_preserves_the_canonical_trailing_newline(self):
        script = self._sealed_reference_input_script()
        reference = (
            '{"schema_version":1,"record_type":"sealed_attempt_reference",'
            '"intent_reference":{"asset_name":"attempt-intent.json"}}'
        )
        for supplied in (reference, reference + "\n"):
            with self.subTest(trailing_newline=supplied.endswith("\n")), TemporaryDirectory() as directory:
                runner = Path(directory)
                result = subprocess.run(
                    ["bash", "-c", script],
                    env={
                        **os.environ,
                        "RUNNER_TEMP": str(runner),
                        "SEALED_REFERENCE": supplied,
                    },
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                if supplied.endswith("\n"):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        (runner / "sealed-attempt-reference.json").read_bytes(),
                        supplied.encode("utf-8"),
                    )
                else:
                    self.assertNotEqual(result.returncode, 0)

    def test_expired_predecessor_transport_selects_durable_fallback(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / "gh"
            fake_gh.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            fake_gh.chmod(0o755)
            output = root / "github-output"
            result = subprocess.run(
                ["bash", "-c", self._prior_transport_script()],
                env={
                    **os.environ,
                    "RUNNER_TEMP": str(root / "runner"),
                    "GITHUB_OUTPUT": str(output),
                    "GH_TOKEN": "offline-fixture",
                    "PRIOR_PREPARATION_RUN_ID": "123",
                    "PRIOR_PREPARATION_ARTIFACT_NAME": "expired-artifact",
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                },
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "available=false\n")

    def test_expired_current_transport_selects_durable_reference_bootstrap(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / "gh"
            fake_gh.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            fake_gh.chmod(0o755)
            output = root / "github-output"
            result = subprocess.run(
                ["bash", "-c", self._preparation_transport_script()],
                env={
                    **os.environ,
                    "RUNNER_TEMP": str(root / "runner"),
                    "GITHUB_OUTPUT": str(output),
                    "GH_TOKEN": "offline-fixture",
                    "PREPARATION_RUN_ID": "123",
                    "PREPARATION_ARTIFACT_NAME": "expired-artifact",
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                },
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "available=false\n")

    def test_external_reference_transport_requires_canonical_trailing_newline(self):
        script = self._external_reference_transport_script()
        reference = '{"schema_version":1,"record_type":"external_predecessor_reference"}'
        for supplied in (reference, reference + "\n"):
            with self.subTest(trailing_newline=supplied.endswith("\n")), TemporaryDirectory() as directory:
                runner = Path(directory)
                result = subprocess.run(
                    ["bash", "-c", script],
                    env={
                        **os.environ,
                        "RUNNER_TEMP": str(runner),
                        "PRIOR_EXTERNAL_REFERENCE": supplied,
                    },
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                if supplied.endswith("\n"):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        (runner / "prior-external-predecessor-reference.json").read_bytes(),
                        supplied.encode("utf-8"),
                    )
                else:
                    self.assertNotEqual(result.returncode, 0)

    def test_durable_fallback_reconstructs_baseline_from_sealed_package_provenance(self):
        script = self._baseline_reconstruction_python()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package, manifest, trusted, output, expected = self._durable_baseline_fixture(root)
            result = subprocess.run(
                ["python3", "-c", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "PACKAGE_RECORD": str(package),
                    "PACKAGE_MANIFEST": str(manifest),
                    "TRUSTED_INPUT_MANIFEST": str(trusted),
                    "BASELINE_OUTPUT": str(output),
                },
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), expected)

            forged = json.loads(package.read_text(encoding="utf-8"))
            forged["expected_baseline_sha256"] = "0" * 64
            package.write_bytes(canonical_json(forged))
            failed = subprocess.run(
                ["python3", "-c", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "PACKAGE_RECORD": str(package),
                    "PACKAGE_MANIFEST": str(manifest),
                    "TRUSTED_INPUT_MANIFEST": str(trusted),
                    "BASELINE_OUTPUT": str(output),
                },
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertNotEqual(failed.returncode, 0)

    def test_attestation_failure_stops_before_runtime_execution_install_or_auth(self):
        """The trusted shell gate must fail before any transported code is run."""

        with TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / "runner"
            preparation = runner / "preparation"
            preparation.mkdir(parents=True)
            manifest = preparation / "publication-preparation-manifest.json"
            manifest.write_text("{}\n", encoding="utf-8")
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\n"
                "printf 'attestation attempted\\n' > \"$RUNNER_TEMP/gh-called\"\n"
                "exit 42\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            # GitHub's hosted Linux runner provides sha256sum.  The local
            # macOS test host does not, so provide the equivalent command in
            # the fake tool directory while keeping the workflow gate intact.
            fake_sha256sum = fake_bin / "sha256sum"
            fake_sha256sum.write_text(
                "#!/bin/sh\nexec shasum -a 256 \"$@\"\n",
                encoding="utf-8",
            )
            fake_sha256sum.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", self._attestation_bootstrap_script()],
                env={
                    "RUNNER_TEMP": str(runner),
                    "PREPARATION_HEAD_SHA": "a" * 40,
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                },
                text=True,
                capture_output=True,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((runner / "gh-called").exists())
            self.assertFalse((runner / "execution").exists())
            self.assertFalse((runner / "uv-sync").exists())
            self.assertFalse((runner / "firebase-auth").exists())

    def test_transport_bootstrap_rejects_corrupt_runtime_before_materialization(self):
        with TemporaryDirectory() as directory:
            runner, preparation, _commit = self._transport_fixture(Path(directory))
            (preparation / "execution-source.tar.gz").write_bytes(b"corrupt runtime")

            result = self._run_transport_bootstrap(runner)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("differ", result.stdout + result.stderr)
            self.assertTrue((runner / "trusted-candidate.git" / "objects").exists())
            self.assertFalse((runner / "execution").exists())

    def test_transport_bootstrap_rejects_corrupt_bundle_before_candidate_ref(self):
        with TemporaryDirectory() as directory:
            runner, preparation, _commit = self._transport_fixture(Path(directory))
            (preparation / "candidate-tree.tar.gz").write_bytes(b"corrupt candidate tree")

            result = self._run_transport_bootstrap(runner)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((runner / "trusted-candidate.git").exists())
            self.assertFalse((runner / "execution").exists())

    def test_transport_bootstrap_rejects_bundle_tree_mismatch_against_authenticated_commit(self):
        with TemporaryDirectory() as directory:
            runner, _preparation, _commit = self._transport_fixture(Path(directory))
            repository = runner.parent / "candidate"
            tree = subprocess.check_output(
                ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True
            ).strip()
            fake_gh = runner.parent / "fake-bin" / "gh"
            fake_gh.write_text(
                fake_gh.read_text(encoding="utf-8").replace(tree, "0" * 40),
                encoding="utf-8",
            )

            result = self._run_transport_bootstrap(runner)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((runner / "candidate-tree-stage" / "candidate-tree.json").exists())
            self.assertFalse((runner / "execution").exists())

    def test_gh_api_repository_selection_is_an_explicit_offline_endpoint(self):
        help_result = subprocess.run(
            ["gh", "api", "--help"],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("gh api <endpoint>", help_result.stdout)

        invalid_result = subprocess.run(
            [
                "gh",
                "api",
                "--repo",
                "arkar16/sportsrank",
                "--help",
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertNotEqual(invalid_result.returncode, 0)
        self.assertIn("--repo", invalid_result.stdout + invalid_result.stderr)


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

        self.assertIn("procedure, not an approval", handoff)
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


if __name__ == "__main__":
    unittest.main()
