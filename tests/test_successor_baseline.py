from dataclasses import replace
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

from cfb.candidate_tree import create_candidate_tree_archive
from cfb.firebase import FakeFirebasePublicationBackend, FirebasePublicationAdapter
from cfb.github_archive import ArchiveError, FakeImmutableArchive
from cfb.publication import (
    PublicationCoordinator, PublicationExecutionError, PublicationTags,
    ReconciliationTags, bind_merged_candidate, _verify_prior_publication,
)
from cfb.publication_authorization import (
    FakeGitHubApprovalReader, GitHubPreparationProvenanceReader,
    GitHubRuntimeContext, preparation_manifest_bytes,
)
from cfb.publication_records import ProviderIdentity, RecordValidationError, canonical_json
from cfb.successor import (
    EVIDENCE_ROLES, SuccessorBaselineRecord, authenticate_successor, derive_successor,
    assert_successor_overlay,
)
from tests.test_publication_execution import fixture, APP_IDENTITY, TARGET
from tests.test_publication_rehydration import ProvenanceTransport


REPOSITORY = "arkar16/sportsrank"
WORKFLOW = ".github/workflows/firebase-hosting-publish.yml"


class Generation:
    def __init__(self, root, archive, backend, number, baseline=None, predecessor=None):
        self.root = root
        self.number = number
        self.fx = fixture(root, archive=archive, baseline_record=baseline,
                          predecessor=predecessor, marker=f"generation-{number}".encode())
        fx = self.fx
        trust = fx.reader.repository / "config/sr7-recovery-inputs.json"
        trust.parent.mkdir()
        trust.write_bytes(canonical_json({"evidence_archives": {
            "baseline": "3" * 64, "source_inputs": fx.package.retained_inputs_sha256,
            "original_prepared": "7" * 64,
        }}))
        for arguments in (("add", "config"), ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--amend", "--no-edit", "-q")):
            subprocess.run(["git", "-C", str(fx.reader.repository), *arguments], check=True, capture_output=True)
        commit = subprocess.check_output(["git", "-C", str(fx.reader.repository), "rev-parse", "HEAD"], text=True).strip()
        fx.package = bind_merged_candidate(fx.prepared, candidate_commit=commit, reader=fx.reader)
        self.bundle = root / "candidate-tree.tar.gz"
        create_candidate_tree_archive(fx.reader.repository, commit, self.bundle)
        self.manifest = root / "publication-preparation-manifest.json"
        origin = {"schema_version": 1, "record_type": "publication_preparation_origin",
                  "repository": REPOSITORY, "workflow_path": WORKFLOW,
                  "event": "workflow_dispatch", "ref": "refs/heads/main",
                  "head_sha": commit, "run_id": str(number * 100 + 1), "run_attempt": "1"}
        self.origin = root / "preparation-origin.json"
        self.origin.write_bytes(canonical_json(origin))
        self.manifest.write_bytes(preparation_manifest_bytes(
            **{key: value for key, value in origin.items() if key not in ("schema_version", "record_type")},
            package_archive_sha256=fx.package.bundle_sha256, package_record_sha256=fx.package.digest,
        ))
        self.provenance = GitHubPreparationProvenanceReader.from_github_token({"GITHUB_TOKEN": "fixture"})
        self.transport = ProvenanceTransport(self.manifest, {
            "id": number * 100 + 1, "run_attempt": 1, "path": WORKFLOW,
            "event": "workflow_dispatch", "head_branch": "main", "head_sha": commit,
            "status": "completed", "conclusion": "success",
        })
        self.archive = archive
        self.backend = backend

    def coordinator(self, run):
        commit = self.fx.package.candidate_commit
        approval = FakeGitHubApprovalReader({
            "id": run, "run_attempt": 1, "path": WORKFLOW, "event": "workflow_dispatch",
            "head_branch": "main", "head_sha": commit,
        }, [{"state": "approved", "environments": [{"id": 9, "name": "production"}],
             "user": {"login": "arkar16", "id": 18407890}}])
        coordinator = PublicationCoordinator(
            provider=FirebasePublicationAdapter(TARGET, self.backend), archive=self.archive,
            repository=REPOSITORY, approval_reader=approval,
        )
        runtime = GitHubRuntimeContext(REPOSITORY, str(run), "1", "refs/heads/main", commit, "workflow_dispatch")
        return coordinator, runtime

    def seal(self, prior=None):
        coordinator, runtime = self.coordinator(self.number * 100 + 2)
        with self.transport:
            return coordinator.seal_publication_attempt(
                self.fx.package, purpose="normal", prepared=self.fx.prepared,
                commit_reader=self.fx.reader, runtime=runtime, baseline=self.fx.baseline,
                evidence_references={}, preparation_manifest=self.manifest,
                preparation_origin=self.origin, candidate_tree=self.bundle,
                provenance_reader=self.provenance, tags=self.tags("seal"),
                attempt_id=f"generation-{self.number}", retrieval_directory=self.root / "seal",
                prior=prior, allow_unknown_historical_baseline=prior is None,
            )

    def tags(self, suffix):
        return PublicationTags(*(f"g{self.number}-{suffix}-{role}" for role in ("package", "intent", "result", "verification")))

    def execute(self, reference, suffix="execute", prior=None):
        coordinator, runtime = self.coordinator(self.number * 100 + 3)
        with self.transport:
            return coordinator.execute_sealed_attempt(
                reference, purpose="normal", runtime=runtime, baseline=self.fx.baseline,
                provenance_reader=self.provenance, tags=self.tags(suffix),
                retrieval_directory=self.root / suffix, prior=prior,
                allow_unknown_historical_baseline=prior is None,
            )

    def reconcile(self, reference):
        coordinator, _ = self.coordinator(self.number * 100 + 4)
        with self.transport:
            return coordinator.reconcile_sealed_attempt(
                reference, baseline=self.fx.baseline, provenance_reader=self.provenance,
                tags=ReconciliationTags(*(f"g{self.number}-audit-{role}" for role in ("observation", "result", "verification"))),
                retrieval_directory=self.root / "audit",
            )


class SuccessorBaselineTests(unittest.TestCase):
    def test_two_generations_and_fail_closed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = FakeImmutableArchive()
            historical_identity = ProviderIdentity(TARGET, "sites/fixture-site/channels/live/releases/history", "sites/fixture-site/versions/history")
            backend = FakeFirebasePublicationBackend(TARGET, historical_identity, managed_identity=APP_IDENTITY)
            first = Generation(root / "first", archive, backend, 1, predecessor=historical_identity)
            reference = first.seal()
            deployed = first.execute(reference)
            self.assertEqual(deployed.state, "verified")
            audit = first.reconcile(reference)
            prior = audit.as_prior()
            evidence = dict(zip(EVIDENCE_ROLES, (
                prior.provider_result_reference, prior.provider_result_source_reference,
                prior.verification_reference, prior.verification_source_reference,
                prior.reconciliation_reference, prior.reconciliation_source_reference,
            ), strict=True))
            writes = backend.write_count
            with first.transport:
                verified = derive_successor(
                    first.fx.baseline, reference, evidence, archive=archive, repository=REPOSITORY,
                    provenance_reader=first.provenance, destination=root / "successor",
                )
            baseline = verified.record
            self.assertEqual(backend.write_count, writes)
            self.assertEqual(baseline.historical, first.fx.baseline)
            self.assertNotEqual(baseline.observed, historical_identity)
            self.assertNotEqual(baseline.digest, first.fx.baseline.digest)
            self.assertEqual(SuccessorBaselineRecord.from_dict(baseline.to_dict()), baseline)
            second = Generation(root / "second", archive, backend, 2, baseline=baseline, predecessor=baseline.observed)
            with self.assertRaisesRegex(PublicationExecutionError, "requires"):
                _verify_prior_publication(second.fx.package, baseline, None, archive=archive,
                    repository=REPOSITORY, destination=root / "missing", allow_unknown_historical_baseline=True)
            for name, altered in (
                ("stale", replace(baseline, observed=historical_identity)),
                ("wrong-package", replace(baseline, prior_package=replace(baseline.prior_package, inventory_sha256="f" * 64))),
                ("wrong-reference", replace(baseline, prior_reference=replace(reference,
                    intent_reference=replace(reference.intent_reference, sha256="f" * 64)))),
            ):
                with first.transport, self.subTest(name=name), self.assertRaises((ArchiveError, PublicationExecutionError, ValueError)):
                    authenticate_successor(altered, archive=archive, repository=REPOSITORY,
                        provenance_reader=first.provenance, destination=root / name)
            with self.assertRaises(RecordValidationError):
                replace(baseline, evidence={key: value for key, value in evidence.items() if key != "verification"})
            aliased_prior = type(prior)._create(*(
                replace(prior.intent_reference, asset_id="999999")
                if key == "intent_reference" else getattr(prior, key)
                for key in prior.__dataclass_fields__
            ))
            with self.assertRaisesRegex(PublicationExecutionError, "reference"):
                _verify_prior_publication(second.fx.package, baseline, aliased_prior, archive=archive,
                    repository=REPOSITORY, destination=root / "aliased-reference")
            bad_verification = replace(prior.verification, public_page_findings={"/index.html": "verified:exact-bytes"})
            bad_prior = type(prior)._create(*(bad_verification if key == "verification" else getattr(prior, key) for key in prior.__dataclass_fields__))
            with self.assertRaisesRegex(ValueError, "full package"):
                _verify_prior_publication(second.fx.package, baseline, bad_prior, archive=archive,
                    repository=REPOSITORY, destination=root / "partial")
            original = (root / "successor/authenticated/verification.json").read_bytes()
            archive.corrupt(evidence["verification"], b"tampered")
            with first.transport, self.assertRaises((ArchiveError, PublicationExecutionError)):
                authenticate_successor(baseline, archive=archive, repository=REPOSITORY,
                    provenance_reader=first.provenance, destination=root / "tampered")
            archive.corrupt(evidence["verification"], original)
            overlay = root / "overlay"
            shutil.copytree(first.fx.prepared.site, overlay)
            assert_successor_overlay(verified, overlay, set())
            (overlay / "index.html").write_bytes(b"changed")
            with self.assertRaisesRegex(RecordValidationError, "unowned prior bytes"):
                assert_successor_overlay(verified, overlay, set())
            assert_successor_overlay(verified, overlay, {"index.html"})
            (overlay / "index.html").unlink()
            with self.assertRaisesRegex(RecordValidationError, "removes a prior public URL"):
                assert_successor_overlay(verified, overlay, {"index.html"})
            reference2 = second.seal(prior)
            # A stale live predecessor must stop before any additional write.
            backend.live = historical_identity
            with self.assertRaisesRegex(PublicationExecutionError, "identity changed"):
                second.execute(reference2, suffix="stale-live", prior=prior)
            self.assertEqual(backend.write_count, writes)
            backend.live = baseline.observed
            # Its claim was consumed; restoring identity cannot replay it.
            with self.assertRaisesRegex(PublicationExecutionError, "exactly once|claim"):
                second.execute(reference2, suffix="replay", prior=prior)
            self.assertEqual(backend.write_count, writes)
            # A new sealed attempt, with the same reviewed successor artifact,
            # can execute under a fresh human approval and fresh claim.
            coordinator, runtime = second.coordinator(205)
            with second.transport:
                reference3 = coordinator.seal_publication_attempt(
                    second.fx.package, purpose="normal", prepared=second.fx.prepared,
                    commit_reader=second.fx.reader, runtime=runtime, baseline=baseline,
                    evidence_references={}, preparation_manifest=second.manifest,
                    preparation_origin=second.origin, candidate_tree=second.bundle,
                    provenance_reader=second.provenance, tags=second.tags("fresh"),
                    attempt_id="generation-2-fresh", retrieval_directory=root / "fresh-seal", prior=prior,
                )
            second_run = second.execute(reference3, suffix="fresh-execute", prior=prior)
            self.assertEqual(second_run.state, "verified")
            self.assertNotEqual(second_run.provider_result.observed_version, baseline.observed.version)
            self.assertEqual(baseline.historical.observed, historical_identity)


if __name__ == "__main__":
    unittest.main()
