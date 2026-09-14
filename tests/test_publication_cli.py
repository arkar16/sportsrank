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
    load_preparation_context,
    main,
)
from cfb.publication_records import (
    AttemptIntentRecord,
    ArchiveReference,
    BaselineRecord,
    ManagedResourceEvidence,
    ProviderIdentity,
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

    def test_execute_dispatches_one_prepared_package_through_injected_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, package, _baseline, archive = _fixture(root)
            context_path = root / "publication-context.json"
            context_path.write_bytes(canonical_json(context))

            evidence = {
                name: _reference(package.candidate_commit, index)
                for index, name in enumerate(
                    ("baseline", "source_inputs", "original_prepared"), 1
                )
            }
            artifact_reference = ArchiveReference(
                REPOSITORY,
                "100",
                "cli-package",
                package.candidate_commit,
                "200",
                "package.tar.gz",
                package.bundle_sha256,
                archive.stat().st_size,
                True,
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

                def publish_normal(self, *args, **kwargs):
                    self.calls.append((args, kwargs))
                    return run

            coordinator = CoordinatorFake()
            result_path = root / "publication-run.json"
            with patch.dict(os.environ, {"GITHUB_TOKEN": "offline-fixture"}), _preparation_provenance(
                root, package.candidate_commit
            ), patch("cfb.publication_cli._runtime", return_value=object()):
                status = main(
                    [
                        "execute",
                        "--context", str(context_path),
                        "--attempt-id", "cli-test",
                        "--result", str(result_path),
                    ],
                    coordinator=coordinator,
                )

            self.assertEqual(status, 0)
            self.assertEqual(len(coordinator.calls), 1)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["record_type"], "publication_run")
            self.assertEqual(payload["operation"], "execute")
            self.assertEqual(payload["attempt"]["package"]["candidate_commit"], package.candidate_commit)
            self.assertNotIn("FIREBASE_ACCESS_TOKEN", result_path.read_text(encoding="utf-8"))

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
            context, package, _baseline, _archive = _fixture(root)
            context_path = root / "publication-context.json"
            context_path.write_bytes(canonical_json(context))
            result_path = root / "publication-run.json"

            class CoordinatorUnavailable:
                archive = SimpleNamespace()

                def publish_normal(self, *args, **kwargs):
                    raise FirebasePublicationError("provider adapter unavailable")

            with patch.dict(os.environ, {"GITHUB_TOKEN": "offline-fixture"}), _preparation_provenance(
                root, package.candidate_commit
            ), patch("cfb.publication_cli._runtime", return_value=object()):
                status = main(
                    [
                        "execute",
                        "--context", str(context_path),
                        "--attempt-id", "unavailable-adapter",
                        "--result", str(result_path),
                    ],
                    coordinator=CoordinatorUnavailable(),
                )

            self.assertEqual(status, 1)
            self.assertFalse(result_path.exists())
            self.assertEqual(package.candidate_commit, context["candidate_commit"])


if __name__ == "__main__":
    unittest.main()
