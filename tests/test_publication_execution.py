from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from cfb.firebase import (
    FakeFirebasePublicationBackend,
    FirebaseDeployArtifact,
    FirebasePublicationAdapter,
    FirebaseRestPublicationBackend,
    ProviderRejectedError,
    ProviderWriteUncertain,
    _deterministic_gzip,
)
from cfb.github_archive import ArchiveError, ArchiveSpec, FakeImmutableArchive
from cfb.publication import (
    GitCommitTreeReader,
    PreparedPackage,
    PriorVerifiedPublication,
    PublicationCoordinator,
    PublicationExecutionError,
    PublicationTags,
    _deterministic_package,
    bind_merged_candidate,
)
from cfb.publication_authorization import (
    FakeGitHubApprovalReader,
    GitHubRuntimeContext,
    ProtectedExecutionEvidence,
    PublicationAuthorizationError,
    authorize_protected_execution,
)
from cfb.publication_records import (
    BaselineRecord,
    ManagedResourceEvidence,
    ProviderIdentity,
    ProviderTarget,
    RecordValidationError,
    SourceProvenance,
    ValidatedPackageRecord,
    canonical_json,
)
from tools.scripts.postdeploy_smoke import REQUIRED_PATHS


STAMP = datetime(2026, 9, 14, tzinfo=timezone.utc)
TARGET = ProviderTarget("fixture-project", "fixture-site", "live")
APP_IDENTITY = {
    "project_id": "fixture-project",
    "messaging_sender_id": "1234",
    "auth_domain": "fixture-project.firebaseapp.com",
    "storage_bucket": "fixture-project.firebasestorage.app",
}


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def inventory(files: dict[str, bytes]) -> str:
    return sha(canonical_json({
        "files": [
            {"path": path, "sha256": sha(value), "size": len(value)}
            for path, value in sorted(files.items()) if path.startswith("website/")
        ]
    }))


def validation(
    inventory_sha: str, configuration_sha: str,
    baseline_sha: str, inputs_sha: str,
) -> str:
    return sha(canonical_json({
        "schema_version": 1,
        "record_type": "package_validation",
        "outcome": "valid",
        "inventory_sha256": inventory_sha,
        "configuration_sha256": configuration_sha,
        "expected_baseline_sha256": baseline_sha,
        "retained_inputs_sha256": inputs_sha,
    }))


def baseline(predecessor: ProviderIdentity) -> BaselineRecord:
    managed = tuple(
        ManagedResourceEvidence(
            path, sha(path.encode()), sha(("provider:" + path).encode()),
            1, APP_IDENTITY,
        )
        for path in ("/__/firebase/init.js", "/__/firebase/init.json")
    )
    return BaselineRecord(
        TARGET, predecessor, predecessor, predecessor,
        "2026-09-11T12:50:43.149Z",
        "1" * 64, "2" * 64, "3" * 64, "4" * 64,
        8, 128, SourceProvenance("unknown", None), managed,
        {"capture.json": "5" * 64}, "allowlisted-v1",
    )


def approval(commit: str, *, approved: bool = True, attempt: int = 1):
    reader = FakeGitHubApprovalReader(
        {
            "id": 123,
            "path": ".github/workflows/firebase-hosting-publish.yml",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": commit,
            "run_attempt": attempt,
        },
        [{
            "state": "approved" if approved else "rejected",
            "environments": [{"id": 9, "name": "production"}],
            "user": {"login": "arkar16", "id": 18_407_890},
        }],
    )
    runtime = GitHubRuntimeContext(
        "owner/repository", "123", str(attempt),
        "refs/heads/main", commit, "workflow_dispatch",
    )
    return reader, runtime


@dataclass
class Fixture:
    root: Path
    baseline: BaselineRecord
    prepared: PreparedPackage
    package: ValidatedPackageRecord
    reader: GitCommitTreeReader
    archive: FakeImmutableArchive
    evidence: dict
    approval: FakeGitHubApprovalReader
    runtime: GitHubRuntimeContext


def fixture(
    root: Path,
    *,
    predecessor: ProviderIdentity | None = None,
    baseline_record: BaselineRecord | None = None,
    marker: bytes = b"first",
    archive: FakeImmutableArchive | None = None,
) -> Fixture:
    predecessor = predecessor or ProviderIdentity(
        TARGET,
        "sites/fixture-site/channels/live/releases/baseline-release",
        "sites/fixture-site/versions/baseline-version",
    )
    baseline_record = baseline_record or baseline(predecessor)
    repository = root / "repository"
    repository.mkdir(parents=True)

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.decode().strip()

    git("init", "--object-format=sha1", "-q")
    site = repository / "website"
    for relative in REQUIRED_PATHS:
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(marker + b":" + relative.encode())
    config = repository / "firebase.json"
    config.write_bytes(canonical_json({
        "hosting": {
            "public": "website",
            "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
        }
    }))
    git("add", "website", "firebase.json")
    git(
        "-c", "user.name=SportsRank Test",
        "-c", "user.email=sportsrank@example.invalid",
        "commit", "-q", "-m", "candidate",
    )
    commit = git("rev-parse", "HEAD")
    committed = {
        f"website/{path.relative_to(site).as_posix()}": path.read_bytes()
        for path in site.rglob("*") if path.is_file()
    }
    committed["firebase.json"] = config.read_bytes()
    inventory_sha = inventory(committed)
    configuration_sha = sha(config.read_bytes())
    inputs_sha = "6" * 64
    package_path = root / "candidate.tar.gz"
    bundle_sha = _deterministic_package(site, config, package_path)
    prepared = PreparedPackage._create(
        package_path, bundle_sha, inventory_sha, configuration_sha,
        baseline_record.digest, predecessor, inputs_sha,
        validation(inventory_sha, configuration_sha, baseline_record.digest, inputs_sha),
        site, config,
    )
    reader = GitCommitTreeReader(repository)
    package = bind_merged_candidate(
        prepared, candidate_commit=commit, reader=reader
    )
    archive = archive or FakeImmutableArchive()
    evidence = {}
    for role in ("baseline", "source_inputs", "original_prepared"):
        path = root / f"{role}.tar.gz"
        path.write_bytes(role.encode())
        evidence[role] = archive.seal_or_reconcile(ArchiveSpec(
            "owner/repository", f"retained-{role}-{root.name}", commit,
            {path.name: path}, role,
        ))[path.name]
    approval_reader, runtime = approval(commit)
    return Fixture(
        root, baseline_record, prepared, package, reader, archive, evidence,
        approval_reader, runtime,
    )


def tags(suffix: str) -> PublicationTags:
    return PublicationTags(
        f"{suffix}-package", f"{suffix}-intent",
        f"{suffix}-provider-result", f"{suffix}-verification",
    )


def coordinator(fx: Fixture, backend: FakeFirebasePublicationBackend):
    return PublicationCoordinator(
        provider=FirebasePublicationAdapter(TARGET, backend),
        archive=fx.archive,
        repository="owner/repository",
        approval_reader=fx.approval,
        clock=lambda: STAMP,
    )


class PublicationExecutionTests(unittest.TestCase):
    def test_first_publication_delivers_exact_bytes_and_seals_result_and_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory))
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            result = coordinator(fx, backend).publish_normal(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                runtime=fx.runtime, baseline=fx.baseline,
                evidence_references=fx.evidence, tags=tags("first"),
                attempt_id="first", retrieval_directory=fx.root / "receipts",
            )

            self.assertEqual(result.state, "verified")
            self.assertEqual(fx.baseline.source.status, "unknown")
            self.assertTrue(result.ordinary_successor_allowed)
            self.assertEqual(result.provider_result.outcome, "accepted")
            self.assertEqual(result.verification.outcome, "verified")
            self.assertNotEqual(
                result.provider_evidence.record_reference.release_id,
                result.verification_evidence.record_reference.release_id,
            )
            self.assertEqual(backend._released_files, {
                path.removeprefix("website/"): value
                for path, value in {
                    f"website/{item.relative_to(fx.prepared.site).as_posix()}": item.read_bytes()
                    for item in fx.prepared.site.rglob("*") if item.is_file()
                }.items()
            })
            self.assertTrue(all(size <= 1000 for size in backend.populate_batch_sizes))
            for provider_sha, payload in backend.uploaded_payloads.items():
                self.assertEqual(sha(payload), provider_sha)
                self.assertEqual(payload[9], 255)

    def test_verified_successor_requires_and_consumes_sealed_predecessor_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = fixture(root / "first")
            backend = FakeFirebasePublicationBackend(
                TARGET, first.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            first_run = coordinator(first, backend).publish_normal(
                first.package, prepared=first.prepared, commit_reader=first.reader,
                runtime=first.runtime, baseline=first.baseline,
                evidence_references=first.evidence, tags=tags("first-successor"),
                attempt_id="first-successor",
                retrieval_directory=first.root / "receipts",
            )
            successor_identity = ProviderIdentity(
                TARGET, first_run.provider_result.observed_release,
                first_run.provider_result.observed_version,
            )
            second = fixture(
                root / "second", predecessor=successor_identity,
                baseline_record=first.baseline, marker=b"second",
                archive=first.archive,
            )
            prior = PriorVerifiedPublication(
                first_run.attempt.intent, first_run.provider_result,
                first_run.verification,
                first_run.provider_evidence.record_reference,
                first_run.provider_evidence.source_reference,
                first_run.verification_evidence.record_reference,
                first_run.verification_evidence.source_reference,
            )
            bad_prior = replace(
                prior,
                provider_result_source_reference=replace(
                    prior.provider_result_source_reference, sha256="f" * 64
                ),
            )
            writes_before = backend.write_count
            with self.assertRaisesRegex(
                PublicationExecutionError, "predecessor evidence"
            ):
                coordinator(second, backend).publish_normal(
                    second.package, prepared=second.prepared,
                    commit_reader=second.reader, runtime=second.runtime,
                    baseline=second.baseline,
                    evidence_references=second.evidence,
                    tags=tags("bad-successor"), attempt_id="bad-successor",
                    retrieval_directory=second.root / "bad-receipts",
                    prior=bad_prior,
                )
            self.assertEqual(backend.write_count, writes_before)
            second_run = coordinator(second, backend).publish_normal(
                second.package, prepared=second.prepared, commit_reader=second.reader,
                runtime=second.runtime, baseline=second.baseline,
                evidence_references=second.evidence, tags=tags("second-successor"),
                attempt_id="second-successor",
                retrieval_directory=second.root / "receipts", prior=prior,
            )
            self.assertEqual(second_run.state, "verified")
            self.assertNotEqual(
                second_run.provider_result.observed_version,
                first_run.provider_result.observed_version,
            )

    def test_successor_without_verified_sealed_predecessor_makes_no_provider_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_identity = ProviderIdentity(
                TARGET,
                "sites/fixture-site/channels/live/releases/baseline-release",
                "sites/fixture-site/versions/baseline-version",
            )
            original_baseline = baseline(base_identity)
            successor = ProviderIdentity(
                TARGET,
                "sites/fixture-site/channels/live/releases/prior-release",
                "sites/fixture-site/versions/prior-version",
            )
            fx = fixture(
                root, predecessor=successor, baseline_record=original_baseline
            )
            backend = FakeFirebasePublicationBackend(
                TARGET, successor, managed_identity=APP_IDENTITY,
            )
            with self.assertRaisesRegex(PublicationExecutionError, "successor requires"):
                coordinator(fx, backend).publish_normal(
                    fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=fx.runtime, baseline=fx.baseline,
                    evidence_references=fx.evidence, tags=tags("missing-prior"),
                    attempt_id="missing-prior",
                    retrieval_directory=fx.root / "receipts",
                )
            self.assertEqual(backend.write_count, 0)

    def test_stale_identity_after_approval_and_archive_work_causes_zero_provider_writes(self):
        class MutatingArchive(FakeImmutableArchive):
            on_intent = None

            def seal_or_reconcile(self, spec):
                references = super().seal_or_reconcile(spec)
                if spec.tag == "stale-intent" and self.on_intent is not None:
                    self.on_intent()
                return references

        with tempfile.TemporaryDirectory() as directory:
            archive = MutatingArchive()
            fx = fixture(Path(directory), archive=archive)
            stale = ProviderIdentity(
                TARGET,
                "sites/fixture-site/channels/live/releases/external-release",
                "sites/fixture-site/versions/external-version",
            )
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            archive.on_intent = lambda: setattr(backend, "live", stale)
            with self.assertRaisesRegex(PublicationExecutionError, "changed after"):
                coordinator(fx, backend).publish_normal(
                    fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=fx.runtime, baseline=fx.baseline,
                    evidence_references=fx.evidence, tags=tags("stale"),
                    attempt_id="stale", retrieval_directory=fx.root / "receipts",
                )
            self.assertEqual(backend.write_count, 0)
            self.assertEqual(fx.approval.read_count, 2)

    def test_definite_rejection_and_lost_release_response_are_not_retried(self):
        for mode, expected_state, expected_outcome in (
            ("reject", "rejected", "rejected"),
            ("lost", "provider_unknown", "unknown"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                fx = fixture(Path(directory))
                backend = FakeFirebasePublicationBackend(
                    TARGET, fx.package.expected_predecessor,
                    managed_identity=APP_IDENTITY,
                    reject_at="create" if mode == "reject" else None,
                    lose_response_at="release" if mode == "lost" else None,
                )
                result = coordinator(fx, backend).publish_normal(
                    fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=fx.runtime, baseline=fx.baseline,
                    evidence_references=fx.evidence, tags=tags(mode),
                    attempt_id=mode, retrieval_directory=fx.root / "receipts",
                )
                self.assertEqual(result.state, expected_state)
                self.assertEqual(result.provider_result.outcome, expected_outcome)
                self.assertIsNotNone(result.provider_evidence)
                self.assertIsNone(result.verification)
                self.assertEqual(backend.write_steps.count("create"), 1)
                self.assertEqual(backend.write_steps.count("release"), 0 if mode == "reject" else 1)
                if mode == "lost":
                    self.assertTrue(result.deployment_may_have_changed)
                    self.assertEqual(
                        result.provider_result.observed_version, backend.live.version
                    )

    def test_result_receipt_seal_failure_pauses_after_truthful_accepted_write(self):
        class ReceiptFailureArchive(FakeImmutableArchive):
            def seal_or_reconcile(self, spec):
                if spec.tag.endswith("provider-result"):
                    raise ArchiveError("offline receipt seal failure")
                return super().seal_or_reconcile(spec)

        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory), archive=ReceiptFailureArchive())
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            result = coordinator(fx, backend).publish_normal(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                runtime=fx.runtime, baseline=fx.baseline,
                evidence_references=fx.evidence, tags=tags("receipt"),
                attempt_id="receipt", retrieval_directory=fx.root / "receipts",
            )
            self.assertEqual(result.state, "provider_result_unsealed")
            self.assertEqual(result.provider_result.outcome, "accepted")
            self.assertTrue(result.deployment_may_have_changed)
            self.assertEqual(result.permitted_next_operations, ("reconcile",))
            self.assertIsNone(result.verification)

    def test_verification_receipt_seal_failure_preserves_both_in_memory_facts(self):
        class VerificationFailureArchive(FakeImmutableArchive):
            def seal_or_reconcile(self, spec):
                if spec.tag.endswith("verification"):
                    raise ArchiveError("offline verification seal failure")
                return super().seal_or_reconcile(spec)

        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory), archive=VerificationFailureArchive())
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            result = coordinator(fx, backend).publish_normal(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                runtime=fx.runtime, baseline=fx.baseline,
                evidence_references=fx.evidence, tags=tags("verification"),
                attempt_id="verification",
                retrieval_directory=fx.root / "receipts",
            )
            self.assertEqual(result.state, "verification_unsealed")
            self.assertEqual(result.provider_result.outcome, "accepted")
            self.assertEqual(result.verification.outcome, "verified")
            self.assertIsNotNone(result.provider_evidence)
            self.assertIsNone(result.verification_evidence)
            self.assertEqual(result.permitted_next_operations, ("reconcile",))

    def test_each_complete_verification_failure_preserves_actual_identity_and_pauses(self):
        for failure in (
            "inventory", "configuration", "managed", "managed-missing",
            "managed-generated", "public",
        ):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                fx = fixture(Path(directory))
                backend = FakeFirebasePublicationBackend(
                    TARGET, fx.package.expected_predecessor,
                    managed_identity=APP_IDENTITY,
                    verification_failure=failure,
                )
                result = coordinator(fx, backend).publish_normal(
                    fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=fx.runtime, baseline=fx.baseline,
                    evidence_references=fx.evidence,
                    tags=tags(f"verify-{failure}"), attempt_id=f"verify-{failure}",
                    retrieval_directory=fx.root / "receipts",
                )
                self.assertEqual(result.state, "verification_failed")
                self.assertEqual(result.verification.outcome, "failed")
                self.assertEqual(
                    result.verification.observed_version,
                    result.provider_result.observed_version,
                )
                self.assertFalse(result.ordinary_successor_allowed)
                self.assertEqual(result.permitted_next_operations, ("reconcile",))

    def test_unavailable_verification_identity_is_explicitly_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory))
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
                verification_failure="observation",
            )
            result = coordinator(fx, backend).publish_normal(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                runtime=fx.runtime, baseline=fx.baseline,
                evidence_references=fx.evidence, tags=tags("verify-unknown"),
                attempt_id="verify-unknown",
                retrieval_directory=fx.root / "receipts",
            )
            self.assertEqual(result.state, "verification_unknown")
            self.assertEqual(result.verification.outcome, "unknown")
            self.assertEqual(
                result.verification.observed_release,
                result.provider_result.observed_release,
            )
            self.assertTrue(result.deployment_may_have_changed)
            self.assertEqual(result.permitted_next_operations, ("reconcile",))

    def test_missing_evidence_and_forged_approval_fail_before_provider_write(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory))
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            missing = dict(fx.evidence)
            missing.pop("baseline")
            with self.assertRaises(RecordValidationError):
                coordinator(fx, backend).publish_normal(
                    fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=fx.runtime, baseline=fx.baseline,
                    evidence_references=missing, tags=tags("missing"),
                    attempt_id="missing", retrieval_directory=fx.root / "missing",
                )
            self.assertEqual(backend.write_count, 0)

            forged_reader, forged_runtime = approval(
                fx.package.candidate_commit, approved=False
            )
            forged = PublicationCoordinator(
                provider=FirebasePublicationAdapter(TARGET, backend),
                archive=fx.archive, repository="owner/repository",
                approval_reader=forged_reader, clock=lambda: STAMP,
            )
            with self.assertRaises(PublicationAuthorizationError):
                forged.publish_normal(
                    fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=forged_runtime, baseline=fx.baseline,
                    evidence_references=fx.evidence, tags=tags("forged"),
                    attempt_id="forged", retrieval_directory=fx.root / "forged",
                )
            self.assertEqual(backend.write_count, 0)
            with self.assertRaises(PublicationAuthorizationError):
                ProtectedExecutionEvidence({}, "1" * 64, "c" * 40)

    def test_runtime_metadata_cannot_substitute_for_exact_owner_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory))
            cases = []
            wrong_path, runtime = approval(fx.package.candidate_commit)
            wrong_path.run_value["path"] = ".github/workflows/other.yml"
            cases.append(("workflow", wrong_path, runtime))
            wrong_head, runtime = approval(fx.package.candidate_commit)
            wrong_head.run_value["head_sha"] = "d" * 40
            cases.append(("head", wrong_head, runtime))
            wrong_owner, runtime = approval(fx.package.candidate_commit)
            wrong_owner.approval_values[0]["user"] = {
                "login": "someone-else", "id": 1,
            }
            cases.append(("owner", wrong_owner, runtime))
            ambiguous, runtime = approval(fx.package.candidate_commit)
            ambiguous.approval_values = (
                *ambiguous.approval_values,
                dict(ambiguous.approval_values[0]),
            )
            cases.append(("ambiguous", ambiguous, runtime))
            no_approval, runtime = approval(fx.package.candidate_commit)
            no_approval.approval_values = ()
            cases.append(("missing", no_approval, runtime))
            rerun, runtime = approval(fx.package.candidate_commit, attempt=2)
            cases.append(("rerun", rerun, runtime))

            for label, reader, context in cases:
                with self.subTest(label=label), self.assertRaises(
                    PublicationAuthorizationError
                ):
                    authorize_protected_execution(
                        fx.package, runtime=context, github=reader,
                        expected_repository="owner/repository",
                    )

    def test_concrete_rest_backend_maps_write_transport_and_http_failures(self):
        backend = FirebaseRestPublicationBackend(lambda: "offline-token")
        with patch("cfb.firebase.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(ProviderWriteUncertain):
                backend.create_version(TARGET, {}, {})
        rejected = HTTPError(
            "https://firebase.invalid", 400, "bad request", {}, None
        )
        with patch("cfb.firebase.urlopen", side_effect=rejected):
            with self.assertRaises(ProviderRejectedError):
                backend.create_version(TARGET, {}, {})

        class UnexpectedSuccess:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *arguments):
                return False

            def read(self, limit):
                return b""

            def geturl(self):
                return (
                    "https://upload-firebasehosting.googleapis.com/upload/"
                    "sites/fixture-site/versions/version/files/" + "a" * 64
                )

        upload_url = (
            "https://upload-firebasehosting.googleapis.com/upload/"
            "sites/fixture-site/versions/version/files"
        )
        with patch("cfb.firebase.urlopen", return_value=UnexpectedSuccess()):
            with self.assertRaises(ProviderWriteUncertain):
                backend.upload_file(upload_url, "a" * 64, b"payload")

    def test_deterministic_payloads_and_population_batches_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory))
            for index in range(1001):
                path = fx.prepared.site / "bulk" / f"{index:04d}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"value-{index}".encode())
            # Rebind a fresh package through a second real Git commit.
            repository = fx.prepared.site.parent
            subprocess.run(
                ["git", "-C", str(repository), "add", "website"], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=SportsRank Test",
                 "-c", "user.email=sportsrank@example.invalid", "commit", "-q",
                 "-m", "bulk candidate"], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            commit = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).stdout.decode().strip()
            files = {
                f"website/{path.relative_to(fx.prepared.site).as_posix()}": path.read_bytes()
                for path in fx.prepared.site.rglob("*") if path.is_file()
            }
            files["firebase.json"] = fx.prepared.firebase_json.read_bytes()
            inventory_sha = inventory(files)
            package_path = fx.root / "bulk.tar.gz"
            bundle_sha = _deterministic_package(
                fx.prepared.site, fx.prepared.firebase_json, package_path
            )
            prepared = PreparedPackage._create(
                package_path, bundle_sha, inventory_sha,
                fx.prepared.configuration_sha256, fx.baseline.digest,
                fx.package.expected_predecessor, fx.prepared.retained_inputs_sha256,
                validation(
                    inventory_sha, fx.prepared.configuration_sha256,
                    fx.baseline.digest, fx.prepared.retained_inputs_sha256,
                ), fx.prepared.site, fx.prepared.firebase_json,
            )
            package = bind_merged_candidate(
                prepared, candidate_commit=commit, reader=fx.reader
            )
            artifact = FirebaseDeployArtifact.from_archive(package_path, package)
            backend = FakeFirebasePublicationBackend(
                TARGET, package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            FirebasePublicationAdapter(TARGET, backend).deploy(
                artifact, attempt_id="bulk"
            )
            self.assertEqual(backend.populate_batch_sizes, [1000, 7])
            self.assertEqual(
                artifact.provider_payloads[artifact.provider_hashes["/index.html"]],
                _deterministic_gzip(artifact.files["index.html"]),
            )
            self.assertEqual(dict(artifact.serving_config), {})


if __name__ == "__main__":
    unittest.main()
