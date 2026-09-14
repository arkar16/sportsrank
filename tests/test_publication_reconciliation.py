from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest

import json

from cfb.baseline import (
    create_sanitized_baseline_archive,
    import_sanitized_baseline,
)
from cfb.firebase import (
    FakeFirebasePublicationBackend,
    FakeFirebaseReadBackend,
    FirebaseReadAdapter,
)
from cfb.github_archive import ArchiveError, ArchiveSpec, FakeImmutableArchive
from cfb.publication_records import (
    ExternalPredecessorRecord,
    ProviderIdentity,
    ProviderResultRecord,
    ReconciliationRecord,
    RecordValidationError,
    VerificationRecord,
    canonical_json,
)
from cfb.publication import (
    PriorVerifiedPublication,
    PublicationCoordinator,
    PublicationExecutionError,
    ReconciliationTags,
    RecordedPublicationAttempt,
    SealedRecordEvidence,
    seal_attempt_evidence,
)
from cfb.publication_authorization import authorize_protected_execution
from tests.test_publication_execution import (
    APP_IDENTITY,
    STAMP,
    TARGET,
    baseline,
    coordinator,
    fixture,
    tags,
)


class PublicationReconciliationTests(unittest.TestCase):
    def test_arbitrary_sealed_sources_cannot_reconstruct_predecessor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fx = fixture(root)
            intent = seal_attempt_evidence(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                archive=fx.archive, repository="owner/repository",
                package_tag="forged-package", intent_tag="forged-intent",
                attempt_id="forged", purpose="normal",
                evidence_references=fx.evidence,
                protected_context=authorize_protected_execution(
                    fx.package, runtime=fx.runtime, github=fx.approval,
                    expected_repository="owner/repository",
                ).context,
                retrieval_directory=root / "attempt",
            )
            observed = ProviderIdentity(
                TARGET,
                "sites/fixture-site/channels/live/releases/forged-release",
                "sites/fixture-site/versions/forged-version",
            )

            def source_digest(value):
                return hashlib.sha256(canonical_json(value)).hexdigest()

            provider_source = {"fabricated": "provider"}
            result = ProviderResultRecord.create(
                intent=intent.intent, outcome="accepted",
                observed_target=TARGET,
                observed_release=observed.release,
                observed_version=observed.version,
                observed_at=STAMP.isoformat(),
                source_sha256=source_digest(provider_source),
            )
            verification_source = {"fabricated": "verification"}
            verification = VerificationRecord.create(
                intent=intent.intent, provider_result=result,
                outcome="verified",
                inventory_sha256=fx.package.inventory_sha256,
                configuration_sha256=fx.package.configuration_sha256,
                managed_resource_findings={
                    "/__/firebase/init.js": "verified",
                    "/__/firebase/init.json": "verified",
                },
                public_page_findings={"/index.html": "verified"},
                findings=("fabricated",), observed_at=STAMP.isoformat(),
                source_sha256=source_digest(verification_source),
            )
            reconciliation_source = {"fabricated": "reconciliation"}
            reconciliation = ReconciliationRecord.create(
                intent=intent.intent, observed_identity=observed,
                disposition="candidate_verified", provider_result=result,
                verification=verification, findings=("fabricated",),
                observed_at=STAMP.isoformat(),
                source_sha256=source_digest(reconciliation_source),
            )

            def seal(tag, record_name, record, source_name, source):
                evidence = root / tag
                evidence.mkdir()
                record_path = evidence / record_name
                source_path = evidence / source_name
                record_path.write_bytes(canonical_json(record.to_dict()))
                source_path.write_bytes(canonical_json(source))
                refs = fx.archive.seal_or_reconcile(ArchiveSpec(
                    "owner/repository", tag, fx.package.candidate_commit,
                    {record_name: record_path, source_name: source_path}, tag,
                ))
                return SealedRecordEvidence(
                    refs[record_name], refs[source_name],
                    record_path, source_path,
                )

            result_evidence = seal(
                "forged-result", "provider-result.json", result,
                "provider-result-source.json", provider_source,
            )
            verification_evidence = seal(
                "forged-verification", "verification.json", verification,
                "verification-source.json", verification_source,
            )
            reconciliation_evidence = seal(
                "forged-reconciliation", "reconciliation.json",
                reconciliation, "reconciliation-source.json",
                reconciliation_source,
            )
            backend = FakeFirebasePublicationBackend(
                TARGET, observed, managed_identity=APP_IDENTITY,
            )
            with self.assertRaisesRegex(
                PublicationExecutionError, "allowlisted provider observation"
            ):
                coordinator(fx, backend).reconstruct_predecessor(
                    RecordedPublicationAttempt(
                        intent, result, result_evidence,
                        verification, verification_evidence,
                    ),
                    reconciliation=reconciliation,
                    reconciliation_evidence=reconciliation_evidence,
                    baseline=fx.baseline,
                    retrieval_directory=root / "reconstruct",
                )
            self.assertEqual(backend.write_count, 0)

    def test_failed_verification_reconciles_to_paused_verification_only(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory))
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
                verification_failure="public",
            )
            published = coordinator(fx, backend).publish_normal(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                runtime=fx.runtime, baseline=fx.baseline,
                evidence_references=fx.evidence, tags=tags("failed-verify"),
                attempt_id="failed-verify",
                retrieval_directory=fx.root / "publish",
            )
            writes = backend.write_count

            reconciled = coordinator(fx, backend).reconcile(
                RecordedPublicationAttempt(
                    published.attempt, published.provider_result,
                    published.provider_evidence, published.verification,
                    published.verification_evidence,
                ),
                baseline=fx.baseline,
                tags=ReconciliationTags(
                    "failed-reconciliation", "failed-reconciled-result",
                    "failed-reconciled-verification",
                ),
                retrieval_directory=fx.root / "reconcile",
            )

            self.assertEqual(reconciled.state, "candidate_unverified")
            self.assertFalse(reconciled.ordinary_successor_allowed)
            self.assertEqual(
                reconciled.permitted_next_operations,
                ("verification_only", "reconcile"),
            )
            with self.assertRaisesRegex(
                PublicationExecutionError, "fully sealed verified"
            ):
                reconciled.as_prior()
            self.assertEqual(backend.write_count, writes)

    def test_reconciliation_archive_failure_preserves_truth_and_never_deploys(self):
        class FailingTagArchive(FakeImmutableArchive):
            fail_tag: str | None = None

            def seal_or_reconcile(self, spec):
                if spec.tag == self.fail_tag:
                    raise ArchiveError("injected reconciliation archive failure")
                return super().seal_or_reconcile(spec)

        with tempfile.TemporaryDirectory() as directory:
            archive = FailingTagArchive()
            fx = fixture(Path(directory), archive=archive)
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
                lose_response_at="release",
            )
            interrupted = coordinator(fx, backend).publish_normal(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                runtime=fx.runtime, baseline=fx.baseline,
                evidence_references=fx.evidence, tags=tags("archive-failure"),
                attempt_id="archive-failure",
                retrieval_directory=fx.root / "publish",
            )
            writes = backend.write_count
            archive.fail_tag = "failed-observation"

            result = coordinator(fx, backend).reconcile(
                RecordedPublicationAttempt(
                    interrupted.attempt, interrupted.provider_result,
                    interrupted.provider_evidence,
                ),
                baseline=fx.baseline,
                tags=ReconciliationTags(
                    "failed-observation", "recovered-result",
                    "recovered-verification",
                ),
                retrieval_directory=fx.root / "reconcile",
            )

            self.assertEqual(result.state, "reconciliation_unsealed")
            self.assertEqual(result.observed_identity, backend.live)
            self.assertIsNotNone(result.provider_result)
            self.assertIsNotNone(result.verification)
            self.assertIsNone(result.observation_evidence)
            self.assertEqual(result.permitted_next_operations, ("reconcile",))
            self.assertEqual(backend.write_count, writes)

    def test_prewrite_interruption_is_observed_and_sealed_without_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory))
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            authorization = authorize_protected_execution(
                fx.package, runtime=fx.runtime, github=fx.approval,
                expected_repository="owner/repository",
            )
            attempt = seal_attempt_evidence(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                archive=fx.archive, repository="owner/repository",
                package_tag="prewrite-package", intent_tag="prewrite-intent",
                attempt_id="prewrite", purpose="normal",
                evidence_references=fx.evidence,
                protected_context=authorization.context,
                retrieval_directory=fx.root / "attempt-receipts",
            )

            result = coordinator(fx, backend).reconcile(
                RecordedPublicationAttempt(attempt),
                baseline=fx.baseline,
                tags=ReconciliationTags(
                    observation="prewrite-observation",
                    provider_result="prewrite-provider-result",
                    verification="prewrite-verification",
                ),
                retrieval_directory=fx.root / "reconciliation",
            )

            self.assertEqual(result.state, "prewrite_interrupted")
            self.assertEqual(result.observed_identity, fx.package.expected_predecessor)
            self.assertIsNotNone(result.observation_evidence)
            self.assertEqual(
                ReconciliationRecord.from_dict(
                    result.observation.to_dict(), intent=attempt.intent,
                ),
                result.observation,
            )
            malformed = result.observation.to_dict()
            malformed["unexpected"] = "private metadata"
            with self.assertRaises(RecordValidationError):
                ReconciliationRecord.from_dict(malformed)
            self.assertIsNone(result.provider_result)
            self.assertFalse(result.ordinary_successor_allowed)
            self.assertEqual(
                result.permitted_next_operations,
                ("new_owner_approved_attempt",),
            )
            self.assertEqual(backend.write_count, 0)

    def test_lost_accepted_response_is_reconciled_by_exact_live_content(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory))
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
                lose_response_at="release",
            )
            interrupted = coordinator(fx, backend).publish_normal(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                runtime=fx.runtime, baseline=fx.baseline,
                evidence_references=fx.evidence, tags=tags("lost"),
                attempt_id="lost", retrieval_directory=fx.root / "publish",
            )
            writes = backend.write_count
            self.assertEqual(interrupted.state, "provider_unknown")

            reconciled = coordinator(fx, backend).reconcile(
                RecordedPublicationAttempt(
                    interrupted.attempt,
                    interrupted.provider_result,
                    interrupted.provider_evidence,
                ),
                baseline=fx.baseline,
                tags=ReconciliationTags(
                    observation="lost-reconciliation",
                    provider_result="lost-reconciled-result",
                    verification="lost-reconciled-verification",
                ),
                retrieval_directory=fx.root / "reconciliation",
            )

            self.assertEqual(reconciled.state, "reconciled_verified")
            self.assertTrue(reconciled.ordinary_successor_allowed)
            self.assertEqual(reconciled.provider_result.outcome, "accepted")
            self.assertEqual(reconciled.verification.outcome, "verified")
            self.assertIsNotNone(reconciled.observation_evidence)
            self.assertIsNotNone(reconciled.provider_evidence)
            self.assertIsNotNone(reconciled.verification_evidence)
            self.assertEqual(backend.write_count, writes)
            without_observation = replace(
                reconciled, observation_evidence=None
            )
            with self.assertRaisesRegex(
                PublicationExecutionError, "fully sealed verified"
            ):
                without_observation.as_prior()
            reconstructed = coordinator(fx, backend).reconstruct_predecessor(
                RecordedPublicationAttempt(
                    interrupted.attempt, reconciled.provider_result,
                    reconciled.provider_evidence, reconciled.verification,
                    reconciled.verification_evidence,
                ),
                reconciliation=reconciled.observation,
                reconciliation_evidence=reconciled.observation_evidence,
                baseline=fx.baseline,
                retrieval_directory=fx.root / "cross-run-reconstruction",
            )
            self.assertEqual(
                reconstructed.provider_result, reconciled.provider_result
            )
            cross_repository_result = replace(
                reconciled.provider_evidence,
                record_reference=replace(
                    reconciled.provider_evidence.record_reference,
                    repository="attacker/repository",
                ),
            )
            with self.assertRaisesRegex(
                PublicationExecutionError, "configured repository"
            ):
                coordinator(fx, backend).reconstruct_predecessor(
                    RecordedPublicationAttempt(
                        interrupted.attempt, reconciled.provider_result,
                        cross_repository_result, reconciled.verification,
                        reconciled.verification_evidence,
                    ),
                    reconciliation=reconciled.observation,
                    reconciliation_evidence=reconciled.observation_evidence,
                    baseline=fx.baseline,
                    retrieval_directory=fx.root / "cross-repository-prior",
                )

    def test_missing_or_corrupt_result_never_overrides_exact_live_correspondence(self):
        for mode in ("missing", "corrupt"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                fx = fixture(Path(directory))
                backend = FakeFirebasePublicationBackend(
                    TARGET, fx.package.expected_predecessor,
                    managed_identity=APP_IDENTITY,
                    lose_response_at="release",
                )
                interrupted = coordinator(fx, backend).publish_normal(
                    fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=fx.runtime, baseline=fx.baseline,
                    evidence_references=fx.evidence, tags=tags(f"{mode}-lost"),
                    attempt_id=f"{mode}-lost",
                    retrieval_directory=fx.root / "publish",
                )
                if mode == "corrupt":
                    fx.archive.corrupt(
                        interrupted.provider_evidence.record_reference,
                        b"corrupt provider result",
                    )
                    recorded = RecordedPublicationAttempt(
                        interrupted.attempt,
                        interrupted.provider_result,
                        interrupted.provider_evidence,
                    )
                else:
                    recorded = RecordedPublicationAttempt(interrupted.attempt)
                writes = backend.write_count

                reconciled = coordinator(fx, backend).reconcile(
                    recorded, baseline=fx.baseline,
                    tags=ReconciliationTags(
                        observation=f"{mode}-observation",
                        provider_result=f"{mode}-result",
                        verification=f"{mode}-verification",
                    ),
                    retrieval_directory=fx.root / "reconciliation",
                )

                self.assertEqual(reconciled.state, "reconciled_verified")
                self.assertEqual(reconciled.provider_result.outcome, "accepted")
                self.assertEqual(reconciled.verification.outcome, "verified")
                self.assertIn(
                    "prior provider-result evidence: missing_or_invalid",
                    reconciled.observation.findings,
                )
                self.assertEqual(backend.write_count, writes)

    def test_verification_only_retries_failed_or_missing_evidence_without_deploy(self):
        for mode in ("failed", "missing"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                fx = fixture(Path(directory))
                backend = FakeFirebasePublicationBackend(
                    TARGET, fx.package.expected_predecessor,
                    managed_identity=APP_IDENTITY,
                    verification_failure="public" if mode == "failed" else None,
                )
                published = coordinator(fx, backend).publish_normal(
                    fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=fx.runtime, baseline=fx.baseline,
                    evidence_references=fx.evidence,
                    tags=tags(f"verify-only-{mode}"),
                    attempt_id=f"verify-only-{mode}",
                    retrieval_directory=fx.root / "publish",
                )
                if mode == "failed":
                    self.assertEqual(published.state, "verification_failed")
                    backend.verification_failure = None
                recorded = RecordedPublicationAttempt(
                    published.attempt,
                    published.provider_result,
                    published.provider_evidence,
                    published.verification if mode == "failed" else None,
                    published.verification_evidence if mode == "failed" else None,
                )
                writes = backend.write_count

                retried = coordinator(fx, backend).verify_only(
                    recorded, baseline=fx.baseline,
                    verification_tag=f"verify-only-{mode}-retry",
                    retrieval_directory=fx.root / "verification-only",
                )

                self.assertEqual(retried.state, "verified")
                self.assertFalse(retried.ordinary_successor_allowed)
                self.assertEqual(
                    retried.permitted_next_operations, ("reconcile",)
                )
                self.assertEqual(retried.verification.outcome, "verified")
                self.assertIsNotNone(retried.verification_evidence)
                self.assertEqual(
                    retried.verification.provider_result_sha256,
                    published.provider_result.digest,
                )
                self.assertEqual(backend.write_count, writes)

    def test_external_identity_needs_complete_archived_capture_before_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fx = fixture(root / "attempt")
            authorization = authorize_protected_execution(
                fx.package, runtime=fx.runtime, github=fx.approval,
                expected_repository="owner/repository",
            )
            attempt = seal_attempt_evidence(
                fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                archive=fx.archive, repository="owner/repository",
                package_tag="external-package", intent_tag="external-intent",
                attempt_id="external", purpose="normal",
                evidence_references=fx.evidence,
                protected_context=authorization.context,
                retrieval_directory=root / "attempt-receipts",
            )
            external_identity = fx.package.expected_predecessor.__class__(
                TARGET,
                "sites/fixture-site/channels/live/releases/fake-release",
                "sites/fixture-site/versions/fake-version",
            )
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
                stale_identity=external_identity,
            )
            writes = backend.write_count
            unresolved = coordinator(fx, backend).reconcile(
                RecordedPublicationAttempt(attempt), baseline=fx.baseline,
                tags=ReconciliationTags(
                    "external-unresolved-observation",
                    "external-unresolved-result",
                    "external-unresolved-verification",
                ),
                retrieval_directory=root / "unresolved",
            )
            self.assertEqual(unresolved.state, "external_unverified")
            self.assertFalse(unresolved.ordinary_successor_allowed)
            self.assertEqual(
                unresolved.permitted_next_operations,
                ("capture_external", "reconcile"),
            )
            self.assertEqual(backend.write_count, writes)

            files = {
                relative: b"external:" + relative.encode()
                for relative in (
                    "index.html",
                    "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html",
                    "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html",
                    "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html",
                    "cfb/years/2026/rankings/2026_PRESEASON_FBS_cors.html",
                    "cfb/years/2026/data/slate/weekly_slate/2026_W0_FBS_slate.html",
                )
            }
            managed = {
                "projectId": APP_IDENTITY["project_id"],
                "messagingSenderId": APP_IDENTITY["messaging_sender_id"],
                "authDomain": APP_IDENTITY["auth_domain"],
                "storageBucket": APP_IDENTITY["storage_bucket"],
            }
            files["__/firebase/init.json"] = json.dumps(managed).encode()
            files["__/firebase/init.js"] = (
                f"firebase.initializeApp({json.dumps(managed)});".encode()
            )
            private_capture = FirebaseReadAdapter(
                TARGET,
                FakeFirebaseReadBackend(
                    TARGET, files, serving_config={},
                ),
            ).capture(root / "capture")
            public_archive = root / "external-public.tar.gz"
            sanitizer = create_sanitized_baseline_archive(
                private_capture, public_archive
            )
            captured = import_sanitized_baseline(
                public_archive,
                expected_derivative_sha256=sanitizer.derivative_archive_sha256,
                sanitizer_record=sanitizer,
                expected_sanitizer_record_sha256=sanitizer.digest,
                baseline_record=private_capture.record,
                expected_baseline_record_sha256=private_capture.record.digest,
                target=TARGET,
                materialize_to=root / "external-public-site",
                source_archive=private_capture.evidence_archive,
            )
            sanitizer_path = root / "external-sanitizer.json"
            sanitizer_path.write_bytes(canonical_json(sanitizer.to_dict()))
            capture_refs = fx.archive.seal_or_reconcile(ArchiveSpec(
                "owner/repository", "external-capture",
                fx.package.candidate_commit,
                {
                    captured.evidence_archive.name: captured.evidence_archive,
                    sanitizer_path.name: sanitizer_path,
                },
                "external capture",
            ))
            capture_ref = capture_refs[captured.evidence_archive.name]
            sanitizer_ref = capture_refs[sanitizer_path.name]

            attacker_refs = fx.archive.seal_or_reconcile(ArchiveSpec(
                "attacker/repository", "external-attacker-capture",
                fx.package.candidate_commit,
                {
                    captured.evidence_archive.name: captured.evidence_archive,
                    sanitizer_path.name: sanitizer_path,
                },
                "attacker external capture",
            ))
            with self.assertRaisesRegex(
                PublicationExecutionError, "configured repository"
            ):
                coordinator(fx, backend).reconcile_external(
                    captured,
                    archive_reference=attacker_refs[
                        captured.evidence_archive.name
                    ],
                    sanitizer_reference=attacker_refs[sanitizer_path.name],
                    observation_tag="external-attacker-rejected",
                    retrieval_directory=root / "external-attacker-rejected",
                )

            with self.assertRaisesRegex(
                PublicationExecutionError,
                "sanitized public capture derivative",
            ):
                coordinator(fx, backend).reconcile_external(
                    private_capture, archive_reference=capture_ref,
                    sanitizer_reference=sanitizer_ref,
                    observation_tag="external-private-rejected",
                    retrieval_directory=root / "external-private-rejected",
                )

            verified = coordinator(fx, backend).reconcile_external(
                captured, archive_reference=capture_ref,
                sanitizer_reference=sanitizer_ref,
                observation_tag="external-verified-observation",
                retrieval_directory=root / "external-verified",
            )
            self.assertEqual(verified.state, "external_verified")
            self.assertTrue(verified.ordinary_successor_allowed)
            self.assertEqual(verified.observed_identity, external_identity)
            self.assertIsNotNone(verified.observation_evidence)
            self.assertEqual(
                ExternalPredecessorRecord.from_dict(
                    verified.observation.to_dict()
                ),
                verified.observation,
            )
            self.assertEqual(backend.write_count, writes)
            external_prior = verified.as_prior()
            with self.assertRaisesRegex(
                PublicationExecutionError,
                "fully sealed external reconciliation",
            ):
                replace(verified, observation_evidence=None).as_prior()

            recovery = fixture(
                root / "external-correction",
                predecessor=external_identity,
                baseline_record=captured.record,
                marker=b"external-corrected",
                archive=fx.archive,
            )
            backend.lose_response_at = None
            recovered = coordinator(recovery, backend).publish_recovery(
                recovery.package, purpose="correction", prior=external_prior,
                prepared=recovery.prepared, commit_reader=recovery.reader,
                runtime=recovery.runtime, baseline=recovery.baseline,
                evidence_references=recovery.evidence,
                tags=tags("external-correction"),
                attempt_id="external-correction",
                retrieval_directory=recovery.root / "publish",
            )
            self.assertEqual(recovered.state, "verified")
            self.assertEqual(recovered.attempt.intent.purpose, "correction")

    def test_correction_is_a_new_approved_attempt_against_verified_current(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = fixture(root / "first")
            backend = FakeFirebasePublicationBackend(
                TARGET, first.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
                lose_response_at="release",
            )
            first_run = coordinator(first, backend).publish_normal(
                first.package, prepared=first.prepared,
                commit_reader=first.reader, runtime=first.runtime,
                baseline=first.baseline, evidence_references=first.evidence,
                tags=tags("recovery-first"), attempt_id="recovery-first",
                retrieval_directory=first.root / "publish",
            )
            self.assertEqual(first_run.state, "provider_unknown")
            reconciled = coordinator(first, backend).reconcile(
                RecordedPublicationAttempt(
                    first_run.attempt, first_run.provider_result,
                    first_run.provider_evidence,
                ),
                baseline=first.baseline,
                tags=ReconciliationTags(
                    "recovery-reconciliation", "recovery-reconciled-result",
                    "recovery-reconciled-verification",
                ),
                retrieval_directory=first.root / "reconcile",
            )
            current = reconciled.observed_identity
            second = fixture(
                root / "correction", predecessor=current,
                baseline_record=baseline(current), marker=b"corrected",
                archive=first.archive,
            )
            prior = reconciled.as_prior()
            backend.lose_response_at = None
            writes = backend.write_count

            result = coordinator(second, backend).publish_recovery(
                second.package, purpose="correction",
                prepared=second.prepared, commit_reader=second.reader,
                runtime=second.runtime, baseline=second.baseline,
                evidence_references=second.evidence,
                tags=tags("correction"), attempt_id="correction",
                retrieval_directory=second.root / "publish", prior=prior,
            )

            self.assertEqual(result.state, "verified")
            self.assertEqual(result.attempt.intent.purpose, "correction")
            self.assertGreater(backend.write_count, writes)

            stale = fixture(
                root / "stale-rollback",
                predecessor=first.package.expected_predecessor,
                baseline_record=first.baseline, marker=b"old",
                archive=first.archive,
            )
            stale_writes = backend.write_count
            with self.assertRaisesRegex(
                PublicationExecutionError, "predecessor|live provider identity"
            ):
                coordinator(stale, backend).publish_recovery(
                    stale.package, purpose="rollback",
                    prepared=stale.prepared, commit_reader=stale.reader,
                    runtime=stale.runtime, baseline=stale.baseline,
                    evidence_references=stale.evidence,
                    tags=tags("stale-rollback"), attempt_id="stale-rollback",
                    retrieval_directory=stale.root / "publish", prior=prior,
                )
            self.assertEqual(backend.write_count, stale_writes)

    def test_empty_recovery_prior_is_rejected_before_provider_write(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory))
            backend = FakeFirebasePublicationBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            with self.assertRaisesRegex(
                PublicationExecutionError, "coordinator reconstruction"
            ):
                PriorVerifiedPublication(
                    None, None, None, None, None, None, None
                )
            empty = object.__new__(PriorVerifiedPublication)
            with self.assertRaises(PublicationExecutionError):
                coordinator(fx, backend).publish_recovery(
                    fx.package, purpose="correction", prior=empty,
                    prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=fx.runtime, baseline=fx.baseline,
                    evidence_references=fx.evidence, tags=tags("empty-prior"),
                    attempt_id="empty-prior",
                    retrieval_directory=fx.root / "publish",
                )
            self.assertEqual(backend.write_count, 0)

if __name__ == "__main__":
    unittest.main()
