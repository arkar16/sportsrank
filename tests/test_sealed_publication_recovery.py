from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

from cfb.firebase import (
    FakeFirebasePublicationBackend,
    FirebasePublicationAdapter,
    FirebasePublicationError,
)
from cfb.github_archive import (
    ArchiveError,
    ArchiveSpec,
    FakeImmutableArchive,
    GitHubReleaseArchive,
)
from cfb.publication import (
    PublicationExecutionError,
    PublicationCoordinator,
    PublicationTags,
    ReconciliationTags,
    _verify_prior_publication,
    bind_merged_candidate,
    retrieve_sealed_attempt,
)
from cfb.publication_authorization import (
    FakeGitHubApprovalReader,
    GitHubPreparationProvenanceReader,
    GitHubRuntimeContext,
    preparation_manifest_bytes,
)
from cfb.publication_records import (
    ArchiveReference,
    ProviderIdentity,
    SealedAttemptReference,
    canonical_json,
)
from tests.test_publication_execution import APP_IDENTITY, TARGET, fixture
from tests.test_publication_rehydration import ProvenanceTransport


REPOSITORY = "arkar16/sportsrank"
WORKFLOW = ".github/workflows/firebase-hosting-publish.yml"


class SealedPublicationRecoveryTests(unittest.TestCase):
    def test_unknown_historical_baseline_requires_explicit_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fx = fixture(root)
            with self.assertRaisesRegex(
                PublicationExecutionError, "successor requires"
            ):
                _verify_prior_publication(
                    fx.package,
                    fx.baseline,
                    None,
                    archive=fx.archive,
                    repository=REPOSITORY,
                    destination=root / "without-exception",
                )
            _verify_prior_publication(
                fx.package,
                fx.baseline,
                None,
                archive=fx.archive,
                repository=REPOSITORY,
                destination=root / "with-exception",
                allow_unknown_historical_baseline=True,
            )

    def test_public_one_shot_entry_points_are_unconditionally_disabled(self):
        predecessor = ProviderIdentity(
            TARGET,
            "sites/fixture-site/channels/live/releases/baseline-release",
            "sites/fixture-site/versions/baseline-version",
        )
        for repository in (
            "arkar16/sportsrank",
            "ARKAR16/SPORTSRANK",
            "arkar16/SportsRank",
            "owner/repository",
        ):
            with self.subTest(repository=repository):
                backend = FakeFirebasePublicationBackend(
                    TARGET, predecessor, managed_identity=APP_IDENTITY,
                )
                archive = FakeImmutableArchive()
                coordinator = PublicationCoordinator(
                    provider=FirebasePublicationAdapter(TARGET, backend),
                    archive=archive,
                    repository=repository,
                    approval_reader=FakeGitHubApprovalReader({}, []),
                )
                arguments = {
                    "prepared": None,
                    "commit_reader": None,
                    "runtime": None,
                    "baseline": None,
                    "evidence_references": {},
                    "tags": None,
                    "attempt_id": "disabled",
                    "retrieval_directory": ".",
                }
                with self.assertRaisesRegex(Exception, "seal_publication_attempt"):
                    coordinator.publish_normal(None, **arguments)
                with self.assertRaisesRegex(Exception, "seal_publication_attempt"):
                    coordinator.publish_recovery(
                        None, purpose="correction", prior=None, **arguments
                    )
                self.assertEqual(backend.write_count, 0)
                self.assertEqual(archive._releases, {})

    def test_exact_intent_reference_has_canonical_dispatch_grammar(self):
        reference = ArchiveReference(
            "arkar16/sportsrank", "17", "attempt-intent-17", "a" * 40,
            "23", "attempt-intent.json", "b" * 64, 481, True,
        )

        sealed = SealedAttemptReference(reference)

        self.assertEqual(
            sealed.to_dict(),
            {
                "schema_version": 1,
                "record_type": "sealed_attempt_reference",
                "intent_reference": reference.to_dict(),
            },
        )
        self.assertEqual(SealedAttemptReference.from_dict(sealed.to_dict()), sealed)
        self.assertEqual(SealedAttemptReference.from_bytes(sealed.to_bytes()), sealed)
        with self.assertRaisesRegex(Exception, "canonically"):
            SealedAttemptReference.from_bytes(
                json.dumps(sealed.to_dict(), indent=2).encode()
            )

    def test_execution_claim_is_exclusive_even_for_identical_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "execution-claim.json"
            path.write_bytes(b"claim")
            archive = FakeImmutableArchive()
            spec = ArchiveSpec(
                REPOSITORY, "claim", "a" * 40, {path.name: path}, "claim"
            )
            archive.seal_exclusive(spec)
            with self.assertRaisesRegex(ArchiveError, "already exists"):
                archive.seal_exclusive(spec)

    def test_fake_archive_serializes_concurrent_execution_claim_contenders(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "execution-claim.json"
            path.write_bytes(b"claim")
            archive = FakeImmutableArchive()
            spec = ArchiveSpec(
                REPOSITORY, "concurrent-claim", "a" * 40,
                {path.name: path}, "claim",
            )
            barrier = threading.Barrier(2)

            def contend():
                barrier.wait()
                try:
                    archive.seal_exclusive(spec)
                    return "sealed"
                except ArchiveError:
                    return "blocked"

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = sorted(pool.map(lambda unused: contend(), range(2)))
            self.assertEqual(outcomes, ["blocked", "sealed"])

    def test_concrete_archive_never_reconciles_a_preexisting_execution_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "execution-claim.json"
            path.write_bytes(b"claim")
            archive = GitHubReleaseArchive()
            with patch.object(
                archive, "_release", return_value={"id": 91}
            ), patch.object(archive, "_run") as run:
                with self.assertRaisesRegex(ArchiveError, "already exists"):
                    archive.seal_exclusive(ArchiveSpec(
                        REPOSITORY, "existing-claim", "a" * 40,
                        {path.name: path}, "claim",
                    ))
            run.assert_not_called()

    def test_seal_only_returns_exact_reference_without_firebase_reads_or_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fx = fixture(root)
            archive = FakeImmutableArchive()
            retained_paths = {}
            retained_digests = {}
            for role in ("baseline", "source_inputs", "original_prepared"):
                path = root / f"retained-{role}.tar.gz"
                path.write_bytes(role.encode())
                retained_paths[role] = path
                retained_digests[role] = hashlib.sha256(path.read_bytes()).hexdigest()
            trust_path = fx.reader.repository / "config" / "sr7-recovery-inputs.json"
            trust_path.parent.mkdir()
            trust_path.write_bytes(canonical_json({
                "evidence_archives": retained_digests,
            }))
            subprocess.run(
                ["git", "-C", str(fx.reader.repository), "add", str(trust_path)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["git", "-C", str(fx.reader.repository), "commit", "--amend", "--no-edit", "--quiet"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            fx.package = bind_merged_candidate(
                fx.prepared,
                candidate_commit=subprocess.run(
                    ["git", "-C", str(fx.reader.repository), "rev-parse", "HEAD"],
                    check=True, stdout=subprocess.PIPE,
                ).stdout.decode().strip(),
                reader=fx.reader,
            )
            evidence = {}
            for role, path in retained_paths.items():
                evidence[role] = archive.seal_or_reconcile(ArchiveSpec(
                    REPOSITORY, f"retained-{role}", fx.package.candidate_commit,
                    {path.name: path}, role,
                ))[path.name]
            bundle = root / "candidate.bundle"
            subprocess.run(
                ["git", "-C", str(fx.reader.repository), "bundle", "create", str(bundle), "HEAD"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            record = root / "package.json"
            record.write_bytes(canonical_json(fx.package.to_dict()))
            manifest = root / "publication-preparation-manifest.json"
            manifest.write_bytes(preparation_manifest_bytes(
                repository=REPOSITORY, workflow_path=WORKFLOW,
                event="workflow_dispatch", ref="refs/heads/main",
                head_sha=fx.package.candidate_commit, run_id="101",
                run_attempt="1",
                package_archive_sha256=hashlib.sha256(fx.prepared.archive.read_bytes()).hexdigest(),
                package_record_sha256=hashlib.sha256(record.read_bytes()).hexdigest(),
            ))
            origin = root / "preparation-origin.json"
            origin.write_bytes(canonical_json({
                "schema_version": 1,
                "record_type": "publication_preparation_origin",
                "repository": REPOSITORY,
                "workflow_path": WORKFLOW,
                "event": "workflow_dispatch",
                "ref": "refs/heads/main",
                "head_sha": fx.package.candidate_commit,
                "run_id": "101",
                "run_attempt": "1",
            }))
            approval = FakeGitHubApprovalReader(
                {
                    "id": 202, "path": WORKFLOW, "event": "workflow_dispatch",
                    "head_branch": "main", "head_sha": fx.package.candidate_commit,
                    "run_attempt": 1,
                },
                [{
                    "state": "approved",
                    "environments": [{"id": 9, "name": "production"}],
                    "user": {"login": "arkar16", "id": 18_407_890},
                }],
            )
            runtime = GitHubRuntimeContext(
                REPOSITORY, "202", "1", "refs/heads/main",
                fx.package.candidate_commit, "workflow_dispatch",
            )
            class CountingBackend(FakeFirebasePublicationBackend):
                read_count = 0

                def observe(self, target):
                    self.read_count += 1
                    return super().observe(target)

            backend = CountingBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            coordinator = PublicationCoordinator(
                provider=FirebasePublicationAdapter(TARGET, backend),
                archive=archive, repository=REPOSITORY,
                approval_reader=approval,
            )
            provenance = GitHubPreparationProvenanceReader.from_github_token(
                {"GITHUB_TOKEN": "offline-fixture"}
            )
            transport = ProvenanceTransport(manifest, {
                "id": 101, "run_attempt": 1, "path": WORKFLOW,
                "event": "workflow_dispatch", "head_branch": "main",
                "head_sha": fx.package.candidate_commit,
                "status": "completed", "conclusion": "success",
            })

            with self.assertRaisesRegex(Exception, "seal_publication_attempt"):
                coordinator.publish_normal(
                    fx.package, prepared=fx.prepared, commit_reader=fx.reader,
                    runtime=runtime, baseline=fx.baseline,
                    evidence_references=evidence,
                    tags=PublicationTags("one-shot-package", "one-shot-intent", "one-shot-result", "one-shot-verification"),
                    attempt_id="one-shot", retrieval_directory=root / "one-shot",
                )
            self.assertEqual(backend.write_count, 0)

            with patch.object(
                archive, "seal_or_reconcile",
                side_effect=ArchiveError("seal failed"),
            ), transport, self.assertRaises(ArchiveError):
                coordinator.seal_publication_attempt(
                    fx.package, purpose="normal", prepared=fx.prepared,
                    commit_reader=fx.reader, runtime=runtime,
                    baseline=fx.baseline, evidence_references=evidence,
                    preparation_manifest=manifest, preparation_origin=origin,
                    candidate_bundle=bundle, provenance_reader=provenance,
                    tags=PublicationTags(
                        "failed-package", "failed-intent", "unused-result", "unused-verification"
                    ),
                    attempt_id="failed-seal", retrieval_directory=root / "failed-seal",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(backend.write_count, 0)

            substituted_path = root / "substituted-baseline.tar.gz"
            substituted_path.write_bytes(b"substituted")
            substituted_reference = archive.seal_or_reconcile(ArchiveSpec(
                REPOSITORY, "substituted-baseline", fx.package.candidate_commit,
                {substituted_path.name: substituted_path}, "substituted",
            ))[substituted_path.name]
            with transport, self.assertRaisesRegex(
                Exception, "immutable candidate pins"
            ):
                coordinator.seal_publication_attempt(
                    fx.package, purpose="normal", prepared=fx.prepared,
                    commit_reader=fx.reader, runtime=runtime,
                    baseline=fx.baseline,
                    evidence_references={**evidence, "baseline": substituted_reference},
                    preparation_manifest=manifest, preparation_origin=origin,
                    candidate_bundle=bundle, provenance_reader=provenance,
                    tags=PublicationTags(
                        "substituted-package", "substituted-intent",
                        "unused-result", "unused-verification",
                    ),
                    attempt_id="substituted",
                    retrieval_directory=root / "substituted",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(backend.write_count, 0)

            with transport:
                reference = coordinator.seal_publication_attempt(
                    fx.package, purpose="normal", prepared=fx.prepared,
                    commit_reader=fx.reader, runtime=runtime,
                    baseline=fx.baseline, evidence_references=evidence,
                    preparation_manifest=manifest, preparation_origin=origin,
                    candidate_bundle=bundle, provenance_reader=provenance,
                    tags=PublicationTags(
                        "sealed-package", "sealed-intent", "unused-result", "unused-verification"
                    ),
                    attempt_id="sealed", retrieval_directory=root / "seal",
                    allow_unknown_historical_baseline=True,
                )

            self.assertIsInstance(reference, SealedAttemptReference)
            self.assertEqual(reference.intent_reference.asset_name, "attempt-intent.json")
            self.assertEqual(backend.read_count, 0)
            self.assertEqual(backend.write_count, 0)
            with transport:
                recorded = retrieve_sealed_attempt(
                    reference, archive=archive, repository=REPOSITORY,
                    provenance_reader=provenance,
                    retrieval_directory=root / "recovered",
                )
            self.assertEqual(recorded.attempt.package, fx.package)
            self.assertEqual(recorded.attempt.intent.attempt_id, "sealed")
            self.assertEqual(backend.read_count, 0)
            self.assertEqual(backend.write_count, 0)

            execute_approval = FakeGitHubApprovalReader(
                {
                    "id": 303, "path": WORKFLOW, "event": "workflow_dispatch",
                    "head_branch": "main", "head_sha": fx.package.candidate_commit,
                    "run_attempt": 1,
                },
                approval.approval_values,
            )
            execute_runtime = GitHubRuntimeContext(
                REPOSITORY, "303", "1", "refs/heads/main",
                fx.package.candidate_commit, "workflow_dispatch",
            )
            executor = PublicationCoordinator(
                provider=FirebasePublicationAdapter(TARGET, backend),
                archive=archive, repository=REPOSITORY,
                approval_reader=execute_approval,
            )
            with transport, self.assertRaisesRegex(Exception, "fresh protected"):
                coordinator.execute_sealed_attempt(
                    reference, purpose="normal", runtime=runtime,
                    baseline=fx.baseline, provenance_reader=provenance,
                    tags=PublicationTags(
                        "same-run-package", "same-run-intent",
                        "same-run-result", "same-run-verification",
                    ),
                    retrieval_directory=root / "same-run",
                    allow_unknown_historical_baseline=True,
                )
            with transport, self.assertRaisesRegex(Exception, "purpose"):
                executor.execute_sealed_attempt(
                    reference, purpose="rollback", runtime=execute_runtime,
                    baseline=fx.baseline, provenance_reader=provenance,
                    tags=PublicationTags("x-package", "x-intent", "x-result", "x-verification"),
                    retrieval_directory=root / "wrong-purpose",
                )
            wrong_baseline = replace(fx.baseline, inventory_sha256="f" * 64)
            with transport, self.assertRaisesRegex(Exception, "baseline"):
                executor.execute_sealed_attempt(
                    reference, purpose="normal", runtime=execute_runtime,
                    baseline=wrong_baseline, provenance_reader=provenance,
                    tags=PublicationTags(
                        "bad-base-package", "bad-base-intent",
                        "bad-base-result", "bad-base-verification",
                    ),
                    retrieval_directory=root / "wrong-baseline",
                )
            with patch.object(
                archive, "seal_exclusive",
                side_effect=ArchiveError("lost claim response"),
            ), transport, self.assertRaisesRegex(Exception, "exactly once"):
                executor.execute_sealed_attempt(
                    reference, purpose="normal", runtime=execute_runtime,
                    baseline=fx.baseline, provenance_reader=provenance,
                    tags=PublicationTags("x-package", "x-intent", "x-result", "x-verification"),
                    retrieval_directory=root / "claim-failure",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(backend.write_count, 0)
            with transport:
                result = executor.execute_sealed_attempt(
                    reference, purpose="normal", runtime=execute_runtime,
                    baseline=fx.baseline, provenance_reader=provenance,
                    tags=PublicationTags(
                        "unused-package", "unused-intent", "result", "verification"
                    ),
                    retrieval_directory=root / "execute",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(result.state, "verified")
            writes_after_first_execution = backend.write_count
            with transport, self.assertRaisesRegex(Exception, "exactly once"):
                executor.execute_sealed_attempt(
                    reference, purpose="normal", runtime=execute_runtime,
                    baseline=fx.baseline, provenance_reader=provenance,
                    tags=PublicationTags(
                        "unused-package", "unused-intent", "result", "verification"
                    ),
                    retrieval_directory=root / "replay",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(backend.write_count, writes_after_first_execution)
            new_run_approval = FakeGitHubApprovalReader(
                {**execute_approval.run_value, "id": 404},
                execute_approval.approval_values,
            )
            new_run_executor = PublicationCoordinator(
                provider=FirebasePublicationAdapter(TARGET, backend),
                archive=archive, repository=REPOSITORY,
                approval_reader=new_run_approval,
            )
            with transport, self.assertRaisesRegex(Exception, "exactly once"):
                new_run_executor.execute_sealed_attempt(
                    reference, purpose="normal",
                    runtime=GitHubRuntimeContext(
                        REPOSITORY, "404", "1", "refs/heads/main",
                        fx.package.candidate_commit, "workflow_dispatch",
                    ),
                    baseline=fx.baseline, provenance_reader=provenance,
                    tags=PublicationTags(
                        "unused-package-4", "unused-intent-4",
                        "replay-result", "replay-verification",
                    ),
                    retrieval_directory=root / "new-run-replay",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(backend.write_count, writes_after_first_execution)

            with transport:
                claim_lost_reference = coordinator.seal_publication_attempt(
                    fx.package, purpose="normal", prepared=fx.prepared,
                    commit_reader=fx.reader, runtime=runtime,
                    baseline=fx.baseline, evidence_references=evidence,
                    preparation_manifest=manifest, preparation_origin=origin,
                    candidate_bundle=bundle, provenance_reader=provenance,
                    tags=PublicationTags(
                        "claim-lost-package", "claim-lost-intent",
                        "unused-claim-lost-result", "unused-claim-lost-verification",
                    ),
                    attempt_id="claim-lost",
                    retrieval_directory=root / "claim-lost-seal",
                    allow_unknown_historical_baseline=True,
                )
            claim_lost_backend = CountingBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            claim_lost_executor = PublicationCoordinator(
                provider=FirebasePublicationAdapter(TARGET, claim_lost_backend),
                archive=archive, repository=REPOSITORY,
                approval_reader=execute_approval,
            )
            actual_exclusive = archive.seal_exclusive

            def persist_then_lose_response(spec):
                actual_exclusive(spec)
                raise ArchiveError("claim response lost after persistence")

            with patch.object(
                archive, "seal_exclusive", side_effect=persist_then_lose_response
            ), transport, self.assertRaisesRegex(Exception, "exactly once"):
                claim_lost_executor.execute_sealed_attempt(
                    claim_lost_reference, purpose="normal",
                    runtime=execute_runtime, baseline=fx.baseline,
                    provenance_reader=provenance,
                    tags=PublicationTags(
                        "unused-package-6", "unused-intent-6",
                        "claim-lost-result", "claim-lost-verification",
                    ),
                    retrieval_directory=root / "claim-lost-execute",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(claim_lost_backend.write_count, 0)
            with transport, self.assertRaisesRegex(Exception, "exactly once"):
                claim_lost_executor.execute_sealed_attempt(
                    claim_lost_reference, purpose="normal",
                    runtime=execute_runtime, baseline=fx.baseline,
                    provenance_reader=provenance,
                    tags=PublicationTags(
                        "unused-package-7", "unused-intent-7",
                        "claim-lost-result-2", "claim-lost-verification-2",
                    ),
                    retrieval_directory=root / "claim-lost-replay",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(claim_lost_backend.write_count, 0)

            with transport:
                interrupted_reference = coordinator.seal_publication_attempt(
                    fx.package, purpose="normal", prepared=fx.prepared,
                    commit_reader=fx.reader, runtime=runtime,
                    baseline=fx.baseline, evidence_references=evidence,
                    preparation_manifest=manifest, preparation_origin=origin,
                    candidate_bundle=bundle, provenance_reader=provenance,
                    tags=PublicationTags(
                        "interrupted-package", "interrupted-intent",
                        "unused-interrupted-result", "unused-interrupted-verification",
                    ),
                    attempt_id="interrupted",
                    retrieval_directory=root / "interrupted-seal",
                    allow_unknown_historical_baseline=True,
                )
            interrupted_backend = CountingBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY, lose_response_at="release",
            )
            interrupted_executor = PublicationCoordinator(
                provider=FirebasePublicationAdapter(TARGET, interrupted_backend),
                archive=archive, repository=REPOSITORY,
                approval_reader=execute_approval,
            )
            with patch.object(
                interrupted_executor, "_failure_result",
                side_effect=RuntimeError("runner died after provider write"),
            ), transport, self.assertRaisesRegex(RuntimeError, "runner died"):
                interrupted_executor.execute_sealed_attempt(
                    interrupted_reference, purpose="normal",
                    runtime=execute_runtime, baseline=fx.baseline,
                    provenance_reader=provenance,
                    tags=PublicationTags(
                        "unused-package-2", "unused-intent-2",
                        "interrupted-result", "interrupted-verification",
                    ),
                    retrieval_directory=root / "interrupted-execute",
                    allow_unknown_historical_baseline=True,
                )
            interrupted_writes = interrupted_backend.write_count
            with transport:
                reconciliation = interrupted_executor.verify_sealed_attempt(
                    interrupted_reference, baseline=fx.baseline,
                    provenance_reader=provenance,
                    tags=ReconciliationTags(
                        "interrupted-observation", "reconciled-result",
                        "reconciled-verification",
                    ),
                    retrieval_directory=root / "interrupted-reconcile",
                )
            self.assertEqual(reconciliation.state, "reconciled_verified")
            self.assertEqual(interrupted_backend.write_count, interrupted_writes)

            with transport:
                prewrite_reference = coordinator.seal_publication_attempt(
                    fx.package, purpose="normal", prepared=fx.prepared,
                    commit_reader=fx.reader, runtime=runtime,
                    baseline=fx.baseline, evidence_references=evidence,
                    preparation_manifest=manifest, preparation_origin=origin,
                    candidate_bundle=bundle, provenance_reader=provenance,
                    tags=PublicationTags(
                        "prewrite-package", "prewrite-intent",
                        "unused-prewrite-result", "unused-prewrite-verification",
                    ),
                    attempt_id="prewrite",
                    retrieval_directory=root / "prewrite-seal",
                    allow_unknown_historical_baseline=True,
                )

            class PrewriteCrashBackend(CountingBackend):
                crash = True

                def observe(self, target):
                    if self.crash:
                        self.crash = False
                        raise FirebasePublicationError("runner died after claim")
                    return super().observe(target)

            prewrite_backend = PrewriteCrashBackend(
                TARGET, fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY,
            )
            prewrite_executor = PublicationCoordinator(
                provider=FirebasePublicationAdapter(TARGET, prewrite_backend),
                archive=archive, repository=REPOSITORY,
                approval_reader=execute_approval,
            )
            with transport, self.assertRaisesRegex(
                FirebasePublicationError, "after claim"
            ):
                prewrite_executor.execute_sealed_attempt(
                    prewrite_reference, purpose="normal",
                    runtime=execute_runtime, baseline=fx.baseline,
                    provenance_reader=provenance,
                    tags=PublicationTags(
                        "unused-package-3", "unused-intent-3",
                        "prewrite-result", "prewrite-verification",
                    ),
                    retrieval_directory=root / "prewrite-execute",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(prewrite_backend.write_count, 0)
            with transport:
                prewrite_reconciliation = prewrite_executor.reconcile_sealed_attempt(
                    prewrite_reference, baseline=fx.baseline,
                    provenance_reader=provenance,
                    tags=ReconciliationTags(
                        "prewrite-observation", "prewrite-reconciled-result",
                        "prewrite-reconciled-verification",
                    ),
                    retrieval_directory=root / "prewrite-reconcile",
                )
            self.assertEqual(prewrite_reconciliation.state, "prewrite_interrupted")
            self.assertEqual(
                prewrite_reconciliation.permitted_next_operations,
                ("new_owner_approved_attempt",),
            )
            self.assertEqual(prewrite_backend.write_count, 0)
            with transport:
                fresh_reference = coordinator.seal_publication_attempt(
                    fx.package, purpose="normal", prepared=fx.prepared,
                    commit_reader=fx.reader, runtime=runtime,
                    baseline=fx.baseline, evidence_references=evidence,
                    preparation_manifest=manifest, preparation_origin=origin,
                    candidate_bundle=bundle, provenance_reader=provenance,
                    tags=PublicationTags(
                        "fresh-package", "fresh-intent",
                        "unused-fresh-result", "unused-fresh-verification",
                    ),
                    attempt_id="fresh-after-prewrite",
                    retrieval_directory=root / "fresh-seal",
                    allow_unknown_historical_baseline=True,
                )
            fresh_approval = FakeGitHubApprovalReader(
                {**execute_approval.run_value, "id": 505},
                execute_approval.approval_values,
            )
            fresh_executor = PublicationCoordinator(
                provider=FirebasePublicationAdapter(TARGET, prewrite_backend),
                archive=archive, repository=REPOSITORY,
                approval_reader=fresh_approval,
            )
            with transport:
                fresh_result = fresh_executor.execute_sealed_attempt(
                    fresh_reference, purpose="normal",
                    runtime=GitHubRuntimeContext(
                        REPOSITORY, "505", "1", "refs/heads/main",
                        fx.package.candidate_commit, "workflow_dispatch",
                    ),
                    baseline=fx.baseline, provenance_reader=provenance,
                    tags=PublicationTags(
                        "unused-package-5", "unused-intent-5",
                        "fresh-result", "fresh-verification",
                    ),
                    retrieval_directory=root / "fresh-execute",
                    allow_unknown_historical_baseline=True,
                )
            self.assertEqual(fresh_result.state, "verified")
            self.assertGreater(prewrite_backend.write_count, 0)
            foreign = SealedAttemptReference(replace(
                reference.intent_reference, repository="attacker/example"
            ))
            with self.assertRaisesRegex(Exception, "another repository"):
                retrieve_sealed_attempt(
                    foreign, archive=archive, repository=REPOSITORY,
                    provenance_reader=provenance,
                    retrieval_directory=root / "foreign",
                )
            original_intent = recorded.attempt.retrieved_intent.read_bytes()
            archive.corrupt(reference.intent_reference, b"{}\n")
            with self.assertRaises(Exception):
                retrieve_sealed_attempt(
                    reference, archive=archive, repository=REPOSITORY,
                    provenance_reader=provenance,
                    retrieval_directory=root / "corrupt-intent",
                )
            archive.corrupt(reference.intent_reference, original_intent)
            manifest_reference = recorded.attempt.intent.preparation_manifest_reference
            archive.corrupt(manifest_reference, b"corrupt")
            with self.assertRaises(Exception):
                retrieve_sealed_attempt(
                    reference, archive=archive, repository=REPOSITORY,
                    provenance_reader=provenance,
                    retrieval_directory=root / "corrupt",
                )
            self.assertEqual(backend.write_count, writes_after_first_execution)


if __name__ == "__main__":
    unittest.main()
