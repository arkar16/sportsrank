"""Portable publication entry-point behavior with no provider calls."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cfb.publication import SealedAttempt, _deterministic_package
from cfb.firebase import FirebasePublicationError
from cfb.publication_authorization import (
    preparation_manifest_bytes,
)
from cfb.publication_cli import (
    REPOSITORY,
    TARGET,
    WORKFLOW_PATH,
    PublicationCLIError,
    _attestation_dict,
    _context_dict,
    _external_predecessor_reference_dict,
    _load_external_predecessor_reference,
    _validate_initial_baseline_exception,
    load_preparation_context,
    main,
    prepare_operation,
)
from cfb.publication_records import (
    AttemptIntentRecord,
    ArchiveReference,
    BaselineRecord,
    ExternalPredecessorRecord,
    ManagedResourceEvidence,
    ProviderIdentity,
    SealedAttemptReference,
    SourceProvenance,
    ValidatedPackageRecord,
    canonical_json,
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _inventory(site: Path) -> str:
    return _sha(canonical_json({
        "files": [
            {
                "path": f"website/{path.relative_to(site).as_posix()}",
                "sha256": _sha(path.read_bytes()),
                "size": path.stat().st_size,
            }
            for path in sorted(site.rglob("*")) if path.is_file()
        ]
    }))


def _reference(commit: str, index: int) -> ArchiveReference:
    return ArchiveReference(
        REPOSITORY, str(index), f"fixture-{index}", commit, str(index),
        f"asset-{index}.json", _sha(f"asset-{index}".encode()), len(f"asset-{index}"), True,
    )


def _fixture(root: Path):
    site = root / "website"
    site.mkdir()
    (site / "index.html").write_text("<html>fixture</html>", encoding="utf-8")
    firebase_json = root / "firebase.json"
    firebase_json.write_bytes(canonical_json({"hosting": {"public": "website"}}))
    archive = root / "package.tar.gz"
    bundle_sha = _deterministic_package(site, firebase_json, archive)
    subprocess.run(
        ["git", "init", "--quiet", "--object-format=sha1"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "add", "website", "firebase.json"],
        cwd=root,
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
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    candidate_tree_archive = root / "candidate.bundle"
    subprocess.run(
        ["git", "bundle", "create", str(candidate_tree_archive), "HEAD"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    predecessor = ProviderIdentity(
        TARGET,
        "sites/sportsrank-837af/channels/live/releases/prior",
        "sites/sportsrank-837af/versions/prior",
    )
    app_identity = {
        "project_id": TARGET.project,
        "messaging_sender_id": "1234",
        "auth_domain": "sportsrank-837af.firebaseapp.com",
        "storage_bucket": "sportsrank-837af.firebasestorage.app",
    }
    managed = tuple(
        ManagedResourceEvidence(
            path, _sha(path.encode()), _sha(("provider:" + path).encode()), 1, app_identity
        )
        for path in ("/__/firebase/init.js", "/__/firebase/init.json")
    )
    baseline = BaselineRecord(
        TARGET, predecessor, predecessor, predecessor,
        datetime(2026, 9, 14).isoformat(), "1" * 64, "2" * 64, "3" * 64, "4" * 64,
        1, 1, SourceProvenance("unknown", None), managed,
        {"capture.json": "5" * 64}, "allowlisted-v1",
    )
    package = ValidatedPackageRecord.create(
        candidate_commit=commit,
        bundle_sha256=bundle_sha,
        inventory_sha256=_inventory(site),
        configuration_sha256=_sha(firebase_json.read_bytes()),
        expected_baseline_sha256=baseline.digest,
        retained_inputs_sha256="6" * 64,
        validation_sha256="7" * 64,
        expected_predecessor=predecessor.to_dict(),
    )
    (root / "package.json").write_bytes(canonical_json(package.to_dict()))
    (root / "baseline.json").write_bytes(canonical_json(baseline.to_dict()))
    (root / "publication-attestation.json").write_bytes(
        canonical_json(_attestation_dict(package=package, baseline=baseline))
    )
    (root / "publication-preparation-manifest.json").write_bytes(
        preparation_manifest_bytes(
            repository=REPOSITORY,
            workflow_path=WORKFLOW_PATH,
            event="workflow_dispatch",
            ref="refs/heads/main",
            head_sha=commit,
            run_id="123",
            run_attempt="1",
            package_archive_sha256=_sha(archive.read_bytes()),
            package_record_sha256=_sha((root / "package.json").read_bytes()),
        )
    )
    refs = {name: _reference(commit, index) for index, name in enumerate(
        ("baseline", "source_inputs", "original_prepared"), 1
    )}
    context = _context_dict(
        package=package,
        baseline=baseline,
        package_archive=archive,
        candidate_tree_archive=candidate_tree_archive,
        candidate_tree_sha256=_sha(candidate_tree_archive.read_bytes()),
        evidence_references=refs,
        output_root=root,
    )
    return context, package, baseline, archive


class _StubHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.status = 200
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False

    def read(self, limit: int) -> bytes:
        return self._body


class _ProvenanceTransport:
    """Offline command/API transport beneath the concrete production verifier."""

    def __init__(self, manifest: Path, commit: str) -> None:
        self.manifest = manifest
        self.run = {
            "id": 123,
            "run_attempt": 1,
            "path": WORKFLOW_PATH,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": commit,
            "status": "completed",
            "conclusion": "success",
        }
        self.commands: list[list[str]] = []
        self.urls: list[str] = []
        self._command_patch = None
        self._url_patch = None

    def _command(self, command: list[str]):
        self.commands.append(command)
        digest = _sha(self.manifest.read_bytes())
        body = json.dumps([{
            "verificationResult": {
                "statement": {
                    "subject": [{"digest": {"sha256": digest}}]
                }
            }
        }]).encode()
        return subprocess.CompletedProcess(command, 0, body, b"")

    def _urlopen(self, request, *, timeout: float):
        self.urls.append(request.full_url)
        return _StubHttpResponse(json.dumps(self.run).encode())

    def __enter__(self):
        self._command_patch = patch(
            "cfb.publication_authorization._run_attestation_command",
            side_effect=self._command,
        )
        self._url_patch = patch(
            "cfb.publication_authorization.urlopen",
            side_effect=self._urlopen,
        )
        self._command_patch.start()
        self._url_patch.start()
        return self

    def __exit__(self, *arguments):
        assert self._command_patch is not None
        assert self._url_patch is not None
        self._url_patch.stop()
        self._command_patch.stop()
        return False


def _preparation_provenance(root: Path, commit: str):
    manifest = root / "publication-preparation-manifest.json"
    return _ProvenanceTransport(manifest, commit)


class PublicationCLIContextTests(unittest.TestCase):
    def test_legacy_calculation_errors_redact_cfbd_credentials(self):
        import cfb.main as legacy_main

        secret = "legacy-cfbd-sentinel"

        def fail(*_args, **_kwargs):
            raise RuntimeError(f"provider failed with {secret}")

        with patch.dict(os.environ, {"CFBD_API_KEY": secret}, clear=True):
            with patch.object(legacy_main, "single_week_calc", fail):
                with self.assertLogs(level="ERROR") as logs:
                    with self.assertRaisesRegex(RuntimeError, secret):
                        legacy_main.run_calculations(
                            "single_week", 2025, 0, 0, "FBS", 2, 0,
                            "2026-09-20T00:00:00Z",
                        )

        rendered = "\n".join(logs.output)
        self.assertNotIn(secret, rendered)
        self.assertIn("[redacted]", rendered)

    def test_external_predecessor_reference_rehydrates_sealed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _context, package, baseline, _archive = _fixture(root)
            capture_bytes = b"external baseline bytes"
            sanitizer_bytes = b"external sanitizer record"
            capture_reference = ArchiveReference(
                REPOSITORY, "101", "external-capture", package.candidate_commit,
                "201", "baseline-public.tar.gz", _sha(capture_bytes), len(capture_bytes), True,
            )
            sanitizer_reference = ArchiveReference(
                REPOSITORY, "101", "external-capture", package.candidate_commit,
                "202", "baseline-sanitizer.json", _sha(sanitizer_bytes), len(sanitizer_bytes), True,
            )
            source = {
                "schema_version": 1,
                "record_type": "firebase_external_predecessor_observation",
                "target": baseline.observed.target.to_dict(),
                "release": baseline.observed.release,
                "version": baseline.observed.version,
                "baseline_sha256": baseline.digest,
                "archive_reference_sha256": capture_reference.digest,
                "sanitizer_reference_sha256": sanitizer_reference.digest,
                "consumed_archive_sha256": capture_reference.sha256,
                "inventory_sha256": baseline.inventory_sha256,
                "configuration_sha256": baseline.configuration_sha256,
                "application_tree_sha256": baseline.application_tree_sha256,
                "fresh_capture_correspondence": "verified",
            }
            observation = ExternalPredecessorRecord.create(
                baseline=baseline,
                archive_reference=capture_reference,
                sanitizer_reference=sanitizer_reference,
                consumed_archive_sha256=capture_reference.sha256,
                observed_at="2026-09-20T00:00:00+00:00",
                source_sha256=_sha(canonical_json(source)),
            )
            observation_reference = ArchiveReference(
                REPOSITORY, "101", "external-capture", package.candidate_commit,
                "203", "external-predecessor.json", observation.digest,
                len(canonical_json(observation.to_dict())), True,
            )
            source_reference = ArchiveReference(
                REPOSITORY, "101", "external-capture", package.candidate_commit,
                "204", "external-predecessor-source.json", _sha(canonical_json(source)),
                len(canonical_json(source)), True,
            )
            predecessor = SimpleNamespace(
                observed_identity=baseline.observed,
                observation=observation,
                capture_reference=capture_reference,
                sanitizer_reference=sanitizer_reference,
                observation_reference=observation_reference,
                observation_source_reference=source_reference,
            )
            reference_path = root / "external-predecessor-reference.json"
            reference_path.write_bytes(
                canonical_json(_external_predecessor_reference_dict(baseline, predecessor))
            )

            class ArchiveFake:
                def __init__(self):
                    self.files = {
                        capture_reference.asset_id: capture_bytes,
                        sanitizer_reference.asset_id: sanitizer_bytes,
                        observation_reference.asset_id: canonical_json(observation.to_dict()),
                        source_reference.asset_id: canonical_json(source),
                    }

                def retrieve_and_verify(self, reference, destination):
                    value = self.files[reference.asset_id]
                    destination = Path(destination)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(value)
                    return destination

            coordinator = SimpleNamespace(repository=REPOSITORY, archive=ArchiveFake())
            loaded = _load_external_predecessor_reference(
                reference_path,
                expected_baseline=baseline,
                coordinator=coordinator,
                destination=root / "retrieved",
            )
            self.assertEqual(loaded.observation, observation)
            self.assertEqual(loaded.capture_reference, capture_reference)
            self.assertEqual(loaded.observation_source_reference, source_reference)

            wrapper_path = root / "external-reconciliation-run.json"
            wrapper_path.write_bytes(
                canonical_json({
                    "schema_version": 1,
                    "record_type": "external_reconciliation_run",
                    "state": "external_verified",
                    "external_predecessor_reference": json.loads(
                        reference_path.read_bytes()
                    ),
                })
            )
            wrapped = _load_external_predecessor_reference(
                wrapper_path,
                expected_baseline=baseline,
                coordinator=coordinator,
                destination=root / "wrapped-retrieved",
            )
            self.assertEqual(wrapped.observation, observation)

    def test_prepare_copies_an_external_candidate_bundle_after_empty_output_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, package, baseline, _archive = _fixture(root)
            candidate_bundle = root / context["candidate_tree_archive"]
            output = root / "preparation"
            output.mkdir()
            baseline_archive = root / "baseline.tar.gz"
            baseline_archive.write_bytes(b"private baseline input")
            source_root = root / "source-inputs"
            source_root.mkdir()
            source_archive = root / "source-inputs.tar.gz"
            source_archive.write_bytes(b"source inputs")
            source_pins = root / "source-input-pins.json"
            source_pins.write_bytes(canonical_json({"snapshots/example.json": "f" * 64}))
            evidence_path = root / "evidence-references.json"
            evidence_path.write_bytes(b"{}\n")
            trusted_path = root / "sr7-recovery-inputs.json"
            trusted_path.write_bytes(b"{}\n")

            public_archive_sha = "a" * 64
            sanitizer_sha = "b" * 64
            trusted = SimpleNamespace(
                baseline_private_archive_sha256="c" * 64,
                baseline_record_sha256=baseline.digest,
                baseline_public_archive_sha256=public_archive_sha,
                baseline_sanitizer_record_sha256=sanitizer_sha,
                source_archive_sha256="d" * 64,
                evidence_archive_sha256="e" * 64,
                source_file_sha256={"snapshots/example.json": "f" * 64},
            )
            source_inputs = SimpleNamespace(assert_external_to=lambda _root: None)
            imported_baseline = SimpleNamespace(record=baseline)
            prepared_candidate = []

            def fake_prepare_review_package(*_args, **kwargs):
                self.assertEqual(tuple(output.iterdir()), ())
                prepared_candidate.append(_args[0])
                kwargs["output"].write_bytes(b"prepared package")
                return object()

            def fake_sanitizer(_baseline, destination):
                destination.write_bytes(b"public derivative")
                return SimpleNamespace(
                    source_baseline_record_sha256=baseline.digest,
                    source_archive_sha256=trusted.baseline_private_archive_sha256,
                    derivative_archive_sha256=public_archive_sha,
                    digest=sanitizer_sha,
                    to_dict=lambda: {"record_type": "sanitized_baseline"},
                )

            args = SimpleNamespace(
                candidate_root=root,
                candidate_commit=package.candidate_commit,
                candidate_tree_bundle=candidate_bundle,
                candidate_tree_sha256=_sha(candidate_bundle.read_bytes()),
                firebase_json=root / "firebase.json",
                baseline_archive=baseline_archive,
                baseline_sha256=trusted.baseline_private_archive_sha256,
                source_input_root=source_root,
                source_input_pins=source_pins,
                source_input_archive=source_archive,
                source_input_sha256=trusted.source_archive_sha256,
                retained_inputs_sha256=trusted.source_archive_sha256,
                evidence_references=evidence_path,
                trusted_input_manifest=trusted_path,
                output_directory=output,
            )
            with patch.dict(os.environ, {"GITHUB_SHA": package.candidate_commit}), patch(
                "cfb.publication_cli._load_trusted_recovery_inputs",
                return_value=trusted,
            ), patch(
                "cfb.publication_cli._load_evidence_references",
                return_value={},
            ), patch(
                "cfb.publication_cli.import_baseline",
                return_value=imported_baseline,
            ), patch(
                "cfb.publication_cli.RecoveryInputBundle.from_directory",
                return_value=source_inputs,
            ), patch(
                "cfb.publication_cli._assert_trusted_source_bundle",
            ), patch(
                "cfb.publication_cli.prepare_review_package",
                side_effect=fake_prepare_review_package,
            ), patch(
                "cfb.publication_cli.GitCommitTreeReader",
                return_value=SimpleNamespace(require_commit=lambda _commit: None),
            ), patch(
                "cfb.publication_cli.bind_merged_candidate",
                return_value=package,
            ), patch(
                "cfb.publication_cli.create_sanitized_baseline_archive",
                side_effect=fake_sanitizer,
            ):
                context_path = prepare_operation(args)

            self.assertEqual(context_path, (output / "publication-context.json").resolve())
            self.assertEqual(prepared_candidate, [(root / "website").resolve()])
            self.assertEqual(
                json.loads(context_path.read_text(encoding="utf-8"))["candidate_tree_archive"],
                "candidate.bundle",
            )
            self.assertEqual(
                (output / "candidate.bundle").read_bytes(), candidate_bundle.read_bytes()
            )
            self.assertEqual(
                (output / "candidate.bundle.sha256").read_text(encoding="ascii"),
                f"{_sha(candidate_bundle.read_bytes())}  candidate.bundle\n",
            )

    def test_context_loader_revalidates_exact_target_records_and_package_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, package, baseline, archive = _fixture(root)
            path = root / "publication-context.json"
            path.write_bytes(canonical_json(context))

            loaded = load_preparation_context(path)
            self.assertEqual(loaded.package, package)
            self.assertEqual(loaded.baseline, baseline)
            self.assertEqual(loaded.package_archive, archive.resolve())

            package_record = root / "package.json"
            original_package_record = package_record.read_bytes()
            package_record.write_bytes(b"{\"candidate_commit\": \"forged\"}\n")
            with self.assertRaisesRegex(PublicationCLIError, "package record"):
                load_preparation_context(path)
            package_record.write_bytes(original_package_record)

            attestation = root / "publication-attestation.json"
            original_attestation = attestation.read_bytes()
            forged_attestation = json.loads(original_attestation)
            forged_attestation["package_record_sha256"] = "f" * 64
            attestation.write_bytes(canonical_json(forged_attestation))
            with self.assertRaisesRegex(PublicationCLIError, "attestation"):
                load_preparation_context(path)
            attestation.write_bytes(original_attestation)

            tampered = dict(context)
            tampered["target"] = {"project": "other", "site": TARGET.site, "channel": "live"}
            path.unlink()
            path.write_bytes(canonical_json(tampered))
            with self.assertRaises(PublicationCLIError):
                load_preparation_context(path)

    def test_execute_dispatches_exact_reference_through_injected_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, package, _baseline, archive = _fixture(root)
            baseline_path = root / "baseline.json"
            baseline_path.write_bytes(canonical_json(_baseline.to_dict()))
            intent_reference = ArchiveReference(
                REPOSITORY,
                "100",
                "cli-intent",
                package.candidate_commit,
                "200",
                "attempt-intent.json",
                _sha(b"intent"),
                len(b"intent"),
                True,
            )
            sealed_reference = SealedAttemptReference(intent_reference)
            reference_path = root / "sealed-attempt-reference.json"
            reference_path.write_bytes(sealed_reference.to_bytes())
            evidence = {
                name: _reference(package.candidate_commit, index)
                for index, name in enumerate(
                    ("baseline", "source_inputs", "original_prepared"), 1
                )
            }
            artifact_reference = ArchiveReference(
                REPOSITORY, "100", "cli-package", package.candidate_commit, "201",
                "package.tar.gz", package.bundle_sha256, archive.stat().st_size, True,
            )
            intent = AttemptIntentRecord.create(
                package=package,
                attempt_id="cli-test",
                purpose="normal",
                expected_predecessor=package.expected_predecessor.to_dict(),
                artifact_reference=artifact_reference.to_dict(),
                evidence_references={
                    name: reference.to_dict() for name, reference in evidence.items()
                },
                protected_context={
                    "repository": REPOSITORY,
                    "workflow_ref": f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/main",
                    "workflow_sha": package.candidate_commit,
                    "run_id": "123",
                    "run_attempt": "1",
                    "environment": "production",
                    "event": "workflow_dispatch",
                    "ref": "refs/heads/main",
                    "head_sha": package.candidate_commit,
                    "approval_state": "approved",
                    "approver_login": "arkar16",
                    "approver_id": "18407890",
                },
            )
            attempt = SealedAttempt(
                package,
                intent,
                artifact_reference,
                _reference(package.candidate_commit, 10),
                _reference(package.candidate_commit, 11),
                _reference(package.candidate_commit, 12),
                archive,
                archive,
                archive,
                archive,
                {},
            )
            run = SimpleNamespace(
                state="verified",
                attempt=attempt,
                provider_result=None,
                provider_evidence=None,
                verification=None,
                verification_evidence=None,
                deployment_may_have_changed=False,
                permitted_next_operations=("reconcile",),
            )

            class CoordinatorFake:
                archive = SimpleNamespace()

                def __init__(self):
                    self.calls = []

                def execute_sealed_attempt(self, reference, **kwargs):
                    self.calls.append((reference, kwargs))
                    return run

            coordinator = CoordinatorFake()
            result_path = root / "publication-run.json"
            with patch.dict(os.environ, {"GITHUB_TOKEN": "offline-fixture"}), patch(
                "cfb.publication_cli._runtime", return_value=object()
            ):
                status = main(
                    [
                        "execute",
                        "--sealed-reference", str(reference_path),
                        "--baseline-record", str(baseline_path),
                        "--purpose", "normal",
                        "--attempt-id", "cli-test",
                        "--result", str(result_path),
                    ],
                    coordinator=coordinator,
                )

            self.assertEqual(status, 0)
            self.assertEqual(len(coordinator.calls), 1)
            self.assertEqual(coordinator.calls[0][0], sealed_reference)
            self.assertEqual(coordinator.calls[0][1]["purpose"], "normal")
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["record_type"], "publication_run")
            self.assertEqual(payload["operation"], "execute")
            self.assertEqual(payload["attempt"]["package"]["candidate_commit"], package.candidate_commit)
            self.assertNotIn("FIREBASE_ACCESS_TOKEN", result_path.read_text(encoding="utf-8"))

    def test_execute_rejects_missing_canonical_reference_newline_before_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, package, baseline, _archive = _fixture(root)
            baseline_path = root / "baseline.json"
            baseline_path.write_bytes(canonical_json(baseline.to_dict()))
            intent_reference = ArchiveReference(
                REPOSITORY, "100", "cli-intent", package.candidate_commit, "200",
                "attempt-intent.json", _sha(b"intent"), len(b"intent"), True,
            )
            reference_path = root / "sealed-attempt-reference.json"
            reference_path.write_bytes(SealedAttemptReference(intent_reference).to_bytes().rstrip(b"\n"))

            class CoordinatorMustNotRun:
                def execute_sealed_attempt(self, *args, **kwargs):
                    self.called = True
                    raise AssertionError("malformed reference reached coordinator")

            coordinator = CoordinatorMustNotRun()
            status = main(
                [
                    "execute",
                    "--sealed-reference", str(reference_path),
                    "--baseline-record", str(baseline_path),
                    "--purpose", "normal",
                    "--attempt-id", "bad-reference",
                ],
                coordinator=coordinator,
            )
            self.assertEqual(status, 1)
            self.assertFalse(hasattr(coordinator, "called"))

    def test_reconcile_uses_exact_reference_without_a_run_manifest_or_actions_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _context, package, baseline, _archive = _fixture(root)
            baseline_path = root / "baseline.json"
            baseline_path.write_bytes(canonical_json(baseline.to_dict()))
            reference = SealedAttemptReference(
                ArchiveReference(
                    REPOSITORY, "100", "cli-intent", package.candidate_commit,
                    "200", "attempt-intent.json", _sha(b"intent"), 6, True,
                )
            )
            reference_path = root / "sealed-attempt-reference.json"
            reference_path.write_bytes(reference.to_bytes())

            class CoordinatorFake:
                def __init__(self):
                    self.calls = []

                def reconcile_sealed_attempt(self, supplied, **kwargs):
                    self.calls.append((supplied, kwargs))
                    return SimpleNamespace(
                        state="prewrite_interrupted",
                        intent=SimpleNamespace(to_dict=lambda: {"attempt_id": "cli-test"}),
                        observed_identity=SimpleNamespace(
                            to_dict=lambda: baseline.observed.to_dict()
                        ),
                        observation=SimpleNamespace(
                            to_dict=lambda: {"record_type": "reconciliation"}
                        ),
                        observation_evidence=None,
                        provider_result=None,
                        provider_evidence=None,
                        verification=None,
                        verification_evidence=None,
                        permitted_next_operations=("new_owner_approved_attempt",),
                    )

            coordinator = CoordinatorFake()
            with patch.dict(os.environ, {"GITHUB_TOKEN": "offline-fixture"}):
                status = main(
                    [
                        "reconcile",
                        "--sealed-reference", str(reference_path),
                        "--baseline-record", str(baseline_path),
                        "--attempt-id", "cli-reconcile",
                    ],
                    coordinator=coordinator,
                )

            self.assertEqual(status, 0)
            self.assertEqual(len(coordinator.calls), 1)
            self.assertEqual(coordinator.calls[0][0], reference)
            self.assertEqual(coordinator.calls[0][1]["baseline"], baseline)
            result_path = root / "publication-run.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["record_type"], "reconciliation_run")
            self.assertEqual(payload["state"], "prewrite_interrupted")
            self.assertNotIn("attempt_manifest", payload)
            self.assertNotIn("actions", json.dumps(payload, sort_keys=True).lower())

    def test_seal_only_writes_only_canonical_reference_and_never_uses_one_shot_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, package, _baseline, _archive = _fixture(root)
            context_path = root / "publication-context.json"
            context_path.write_bytes(canonical_json(context))
            (root / "preparation-origin.json").write_bytes(
                canonical_json({"schema_version": 1, "record_type": "origin"})
            )
            intent_reference = ArchiveReference(
                REPOSITORY, "100", "seal-intent", package.candidate_commit, "200",
                "attempt-intent.json", _sha(b"intent"), 6, True,
            )
            sealed_reference = SealedAttemptReference(intent_reference)

            class CoordinatorFake:
                def __init__(self):
                    self.calls = []

                def seal_publication_attempt(self, package, **kwargs):
                    self.calls.append((package, kwargs))
                    return sealed_reference

            coordinator = CoordinatorFake()
            result_path = root / "sealed-attempt-reference.json"
            rehydrated = SimpleNamespace(
                prepared=object(), reader=object(), close=lambda: None
            )
            with patch(
                "cfb.publication_cli._rehydrate_package", return_value=rehydrated
            ), patch("cfb.publication_cli._runtime", return_value=object()), patch.dict(
                os.environ, {"GITHUB_TOKEN": "offline-fixture"}
            ), patch(
                "cfb.publication_cli._INITIAL_BASELINE_RECORD_SHA256",
                _baseline.digest,
            ), patch(
                "cfb.publication_cli._INITIAL_BASELINE_OBSERVED",
                _baseline.observed,
            ):
                status = main(
                    [
                        "seal-only",
                        "--context", str(context_path),
                        "--attempt-id", "seal-test",
                        "--purpose", "normal",
                        "--initial-baseline",
                        "--reference-output", str(result_path),
                    ],
                    coordinator=coordinator,
                )

            self.assertEqual(status, 0)
            self.assertEqual(result_path.read_bytes(), sealed_reference.to_bytes())
            self.assertEqual(len(coordinator.calls), 1)
            self.assertEqual(coordinator.calls[0][1]["purpose"], "normal")
            self.assertTrue(
                coordinator.calls[0][1]["allow_unknown_historical_baseline"]
            )

    def test_initial_baseline_rejects_a_future_unknown_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _context, _package, baseline, _archive = _fixture(root)

            with self.assertRaisesRegex(
                PublicationCLIError, "accepted historical baseline"
            ):
                _validate_initial_baseline_exception(
                    True,
                    purpose="normal",
                    baseline=baseline,
                    prior_supplied=False,
                )

    def test_context_loader_rejects_archive_substitution_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, _package, _baseline, archive = _fixture(root)
            path = root / "publication-context.json"
            path.write_bytes(canonical_json(context))
            candidate_bundle = root / context["candidate_tree_archive"]
            original_bundle = candidate_bundle.read_bytes()
            candidate_bundle.write_bytes(b"substituted candidate object graph")
            with self.assertRaisesRegex(PublicationCLIError, "candidate Git bundle"):
                load_preparation_context(path)
            candidate_bundle.write_bytes(original_bundle)

            archive.write_bytes(b"substituted")
            with self.assertRaisesRegex(PublicationCLIError, "archive"):
                load_preparation_context(path)

            context["package_archive"] = "../outside.tar.gz"
            archive.write_bytes(b"restored")
            path.write_bytes(canonical_json(context))
            with self.assertRaises(PublicationCLIError):
                load_preparation_context(path)

    def test_unavailable_provider_adapter_fails_closed_without_result_or_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _context, package, baseline, _archive = _fixture(root)
            baseline_path = root / "baseline.json"
            baseline_path.write_bytes(canonical_json(baseline.to_dict()))
            reference_path = root / "sealed-attempt-reference.json"
            reference_path.write_bytes(
                SealedAttemptReference(
                    ArchiveReference(
                        REPOSITORY, "100", "cli-intent", package.candidate_commit,
                        "200", "attempt-intent.json", _sha(b"intent"), 6, True,
                    )
                ).to_bytes()
            )
            result_path = root / "publication-run.json"

            class CoordinatorUnavailable:
                archive = SimpleNamespace()

                def execute_sealed_attempt(self, *args, **kwargs):
                    raise FirebasePublicationError("provider adapter unavailable")

            with patch.dict(os.environ, {"GITHUB_TOKEN": "offline-fixture"}), patch(
                "cfb.publication_cli._runtime", return_value=object()
            ):
                status = main(
                    [
                        "execute",
                        "--sealed-reference", str(reference_path),
                        "--baseline-record", str(baseline_path),
                        "--purpose", "normal",
                        "--attempt-id", "unavailable-adapter",
                        "--result", str(result_path),
                    ],
                    coordinator=CoordinatorUnavailable(),
                )

            self.assertEqual(status, 1)
            self.assertFalse(result_path.exists())
            self.assertEqual(package.candidate_commit, _context["candidate_commit"])


if __name__ == "__main__":
    unittest.main()
