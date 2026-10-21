"""Independent final-caller checks for the authenticated candidate-tree receipt."""

from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cfb.candidate_tree import create_candidate_tree_archive
from cfb.publication import (
    GitCommitTreeReader,
    PublicationPreparationError,
    PublicationExecutionError,
    RecordedPublicationAttempt,
    _retrieve_recorded_attempt,
    bind_merged_candidate,
    prepare_reviewed_package,
    retrieve_sealed_attempt,
    seal_attempt_evidence,
)
from cfb.github_archive import ArchiveSpec, FakeImmutableArchive
from cfb.publication_authorization import (
    GitHubPreparationProvenanceReader,
    preparation_manifest_bytes,
)
from cfb.publication_records import RecordValidationError, SealedAttemptReference, canonical_json
from tests.test_publication_rehydration import ProvenanceTransport
from cfb.publication_cli import (
    PublicationCLIError,
    _candidate_commit,
    _verify_candidate_tree,
    prepare_reviewed_operation,
)
import tests.test_progression_public_contract_independent as public_contract


class PublicationTreeBindingIndependentTests(unittest.TestCase):
    """Keep draft self-consistency separate from final immutable-tree authority."""

    def setUp(self) -> None:
        self.public_fixture = public_contract.ProgressionPublicContractIndependentTests()
        self.public_fixture.setUp()
        (
            self.site,
            self.receipt_path,
            self.firebase,
            self.source,
            self.trust,
            self.receipt,
        ) = self.public_fixture._build_current_2025_export()
        self.root = Path(self.public_fixture.temporary.name) / "tree-binding"
        self.root.mkdir()
        self.repository = self._make_repository(
            self.root / "candidate", self.site, self.firebase, self.receipt_path
        )
        self.commit = self._commit(self.repository, "candidate")
        self.tree_archive = self.root / "candidate-tree.tar.gz"
        create_candidate_tree_archive(self.repository, self.commit, self.tree_archive)

    def tearDown(self) -> None:
        self.public_fixture.tearDown()

    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def _make_repository(
        self, repository: Path, site: Path, firebase: Path, receipt_path: Path
    ) -> Path:
        repository.mkdir()
        self._git(repository, "init", "--quiet", "--object-format=sha1")
        self._git(repository, "config", "user.name", "SportsRank tree fixture")
        self._git(repository, "config", "user.email", "sportsrank@example.invalid")
        shutil.copytree(site, repository / "website")
        shutil.copyfile(firebase, repository / "firebase.json")
        config = repository / "config"
        config.mkdir()
        shutil.copyfile(receipt_path, config / "sr7-local-validation-receipt.json")
        shutil.copyfile(self.trust, config / "sr7-recovery-inputs.json")
        return repository

    def _commit(self, repository: Path, message: str) -> str:
        self._git(repository, "add", "website", "firebase.json", "config")
        self._git(repository, "-c", "user.name=SportsRank tree fixture",
                  "-c", "user.email=sportsrank@example.invalid", "commit", "--quiet", "-m", message)
        return self._git(repository, "rev-parse", "HEAD")

    def _prepare(self, site: Path, firebase: Path, receipt) -> object:
        return prepare_reviewed_package(
            site,
            firebase_json=firebase,
            local_validation_receipt=receipt,
            output=self.root / "prepared.tar.gz",
        )

    def test_final_binding_accepts_the_exact_committed_receipt_blob(self):
        prepared = self._prepare(self.repository / "website", self.repository / "firebase.json", self.receipt)
        bound = bind_merged_candidate(
            prepared,
            candidate_commit=self.commit,
            reader=GitCommitTreeReader(self.repository),
        )
        self.assertEqual(bound.candidate_commit, self.commit)
        self.assertEqual(
            GitCommitTreeReader(self.repository).read_file(
                self.commit, "config/sr7-local-validation-receipt.json"
            ),
            self.receipt.to_bytes(),
        )

    def _seal_current_package(self):
        prepared = self._prepare(
            self.repository / "website", self.repository / "firebase.json", self.receipt
        )
        reader = GitCommitTreeReader(self.repository)
        package = bind_merged_candidate(
            prepared, candidate_commit=self.commit, reader=reader
        )
        origin = {
            "schema_version": 1, "record_type": "publication_preparation_origin",
            "repository": "arkar16/sportsrank",
            "workflow_path": ".github/workflows/firebase-hosting-publish.yml",
            "event": "workflow_dispatch", "ref": "refs/heads/main",
            "head_sha": self.commit, "run_id": "101", "run_attempt": "1",
        }
        origin_path = self.root / "preparation-origin.json"
        origin_path.write_bytes(canonical_json(origin))
        manifest = self.root / "publication-preparation-manifest.json"
        manifest.write_bytes(preparation_manifest_bytes(
            **{key: value for key, value in origin.items() if key not in ("schema_version", "record_type")},
            package_archive_sha256=package.bundle_sha256,
            package_record_sha256=package.digest,
        ))
        provenance = GitHubPreparationProvenanceReader.from_github_token({"GITHUB_TOKEN": "fixture"})
        transport = ProvenanceTransport(manifest, {
            "id": 101, "run_attempt": 1, "path": origin["workflow_path"],
            "event": "workflow_dispatch", "head_branch": "main", "head_sha": self.commit,
            "status": "completed", "conclusion": "success",
        })
        archive = FakeImmutableArchive()
        with transport:
            sealed = seal_attempt_evidence(
                package,
                prepared=prepared,
                commit_reader=reader,
                archive=archive,
                repository="arkar16/sportsrank",
                package_tag="current-package",
                intent_tag="current-intent",
                attempt_id="current",
                purpose="normal",
                evidence_references={},
                protected_context={
                    "repository": "arkar16/sportsrank",
                    "workflow_ref": "arkar16/sportsrank/.github/workflows/firebase-hosting-publish.yml@refs/heads/main",
                    "workflow_sha": self.commit, "run_id": "102", "run_attempt": "1",
                    "environment": "production", "event": "workflow_dispatch",
                    "ref": "refs/heads/main", "head_sha": self.commit,
                    "approval_state": "approved", "approver_login": "arkar16",
                    "approver_id": "18407890",
                },
                retrieval_directory=self.root / "sealed",
                preparation_manifest=manifest, preparation_origin=origin_path,
                candidate_tree=self.tree_archive, provenance_reader=provenance,
            )
        return sealed, archive, provenance, transport

    def test_sealing_current_package_preserves_both_receipt_identities(self):
        sealed, archive, provenance, transport = self._seal_current_package()
        validation = json.loads(sealed.retrieved_validation.read_bytes())
        self.assertEqual(validation["schema_version"], 2)
        self.assertEqual(validation["local_validation_receipt_sha256"], self.receipt.digest)
        self.assertEqual(validation["used_coverage_sha256"], self.receipt.used_coverage_sha256)
        self.assertEqual(
            hashlib.sha256(sealed.retrieved_validation.read_bytes()).hexdigest(),
            sealed.package.validation_sha256,
        )
        with transport:
            recovered = retrieve_sealed_attempt(
                SealedAttemptReference(sealed.intent_reference),
                archive=archive, repository="arkar16/sportsrank",
                provenance_reader=provenance,
                retrieval_directory=self.root / "recovered",
            )
        self.assertEqual(recovered.attempt.package, sealed.package)
        self.assertEqual(recovered.attempt.retrieved_validation.read_bytes(), sealed.retrieved_validation.read_bytes())
        artifact = _retrieve_recorded_attempt(
            recovered, archive=archive, repository="arkar16/sportsrank",
            destination=self.root / "recorded",
        )
        self.assertEqual(artifact.package, sealed.package)
        self.assertEqual(artifact.files["index.html"], (self.site / "index.html").read_bytes())

    def test_recovery_rejects_resealed_missing_or_replaced_receipt_hashes(self):
        sealed, archive, provenance, transport = self._seal_current_package()
        original = json.loads(sealed.retrieved_validation.read_bytes())
        for field in ("local_validation_receipt_sha256", "used_coverage_sha256"):
            for replacement in (None, "0" * 64):
                name = f"{field}-{replacement is None}"
                with self.subTest(field=field, replacement=replacement):
                    value = dict(original)
                    if replacement is None:
                        value.pop(field)
                    else:
                        value[field] = replacement
                    path = self.root / "tampered" / "package-validation.json"
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(canonical_json(value))
                    reference = archive.seal_or_reconcile(ArchiveSpec(
                        "arkar16/sportsrank", name, self.commit,
                        {path.name: path}, "tampered validation",
                    ))[path.name]
                    intent = replace(sealed.intent, validation_reference=reference)
                    intent_path = self.root / "tampered" / "attempt-intent.json"
                    intent_path.write_bytes(canonical_json(intent.to_dict()))
                    intent_reference = archive.seal_or_reconcile(ArchiveSpec(
                        "arkar16/sportsrank", f"{name}-intent", self.commit,
                        {intent_path.name: intent_path}, "tampered intent",
                    ))[intent_path.name]
                    # Every archive hash is valid. The unchanged authenticated
                    # package must still reject a changed receipt identity.
                    with transport, self.assertRaises((PublicationExecutionError, RecordValidationError)):
                        retrieve_sealed_attempt(
                            SealedAttemptReference(intent_reference),
                            archive=archive, repository="arkar16/sportsrank",
                            provenance_reader=provenance,
                            retrieval_directory=self.root / f"recovered-{name}",
                        )
                    with self.assertRaises((PublicationExecutionError, RecordValidationError)):
                        _retrieve_recorded_attempt(
                            RecordedPublicationAttempt(replace(
                                sealed, validation_reference=reference,
                                intent=intent, intent_reference=intent_reference,
                            )),
                            archive=archive, repository="arkar16/sportsrank",
                            destination=self.root / f"recorded-{name}",
                        )

    def test_final_binding_rejects_missing_or_replaced_committed_receipt(self):
        prepared = self._prepare(self.repository / "website", self.repository / "firebase.json", self.receipt)
        receipt = self.repository / "config/sr7-local-validation-receipt.json"

        receipt.unlink()
        missing_commit = self._commit(self.repository, "remove committed receipt")
        with self.assertRaises(PublicationPreparationError):
            bind_merged_candidate(
                prepared,
                candidate_commit=missing_commit,
                reader=GitCommitTreeReader(self.repository),
            )

        receipt.write_bytes(self.receipt.to_bytes())
        forged = json.loads(receipt.read_bytes())
        forged["private_evidence"]["candidate_tree_sha256"] = "0" * 64
        receipt.write_bytes(public_contract._canonical(forged))
        replaced_commit = self._commit(self.repository, "replace committed receipt")
        with self.assertRaises(PublicationPreparationError):
            bind_merged_candidate(
                prepared,
                candidate_commit=replaced_commit,
                reader=GitCommitTreeReader(self.repository),
            )

    def test_final_binding_rejects_fully_resealed_alternate_public_coverage(self):
        alternate_site = self.root / "alternate-site"
        shutil.copytree(self.site, alternate_site)
        for relative in (
            "cfb/years/2025/history/2025_FBS_progression.html",
            "cfb/years/2025/history/2025_FBS_progression.json",
            "cfb/years/2025/2025_CFB.html",
        ):
            (alternate_site / relative).unlink()
        alternate_receipt_path = self.root / "alternate-receipt.json"
        alternate_receipt_path.write_bytes(self.receipt.to_bytes())
        alternate_receipt = self.public_fixture._reseal_public_records(
            alternate_site,
            alternate_receipt_path,
            self.receipt,
            strip_snapshot_provenance=True,
        )
        forged_receipt = alternate_receipt.to_dict()
        forged_receipt["used_coverage"] = {
            "schema": "ranking-progression/used-coverage/v1",
            "status": "none",
        }
        alternate_receipt_path.write_bytes(public_contract._canonical(forged_receipt))
        from cfb.public_site import LocalValidationReceipt
        alternate_receipt = LocalValidationReceipt.from_bytes(
            alternate_receipt_path.read_bytes()
        )
        alternate_firebase = self.root / "alternate-firebase.json"
        shutil.copyfile(self.firebase, alternate_firebase)
        # The draft seam may accept a self-consistent reseal; that is not final
        # origin authentication. The original validated package must not bind
        # to the alternate candidate tree.
        alternate_prepared = prepare_reviewed_package(
            alternate_site,
            firebase_json=alternate_firebase,
            local_validation_receipt=alternate_receipt,
            output=self.root / "alternate-package.tar.gz",
        )
        self.assertTrue(alternate_prepared.archive.is_file())

        # Pin the resealed alternate draft to the independently retained
        # original candidate tree as well.  A reverse-direction check is
        # necessary because a draft can be internally self-consistent while
        # still being the wrong tree for the reviewed commit.
        with self.assertRaises(PublicationPreparationError):
            bind_merged_candidate(
                alternate_prepared,
                candidate_commit=self.commit,
                reader=GitCommitTreeReader(self.repository),
            )

        alternate_repository = self._make_repository(
            self.root / "alternate-candidate",
            alternate_site,
            alternate_firebase,
            alternate_receipt_path,
        )
        alternate_commit = self._commit(alternate_repository, "resealed alternate")
        original_prepared = self._prepare(
            self.repository / "website", self.repository / "firebase.json", self.receipt
        )
        with self.assertRaises(PublicationPreparationError):
            bind_merged_candidate(
                original_prepared,
                candidate_commit=alternate_commit,
                reader=GitCommitTreeReader(alternate_repository),
            )

    def test_final_caller_uses_runtime_commit_and_verified_archive_reader(self):
        with patch.dict("os.environ", {"GITHUB_SHA": self.commit}, clear=False):
            self.assertEqual(_candidate_commit(self.commit), self.commit)
            with _verify_candidate_tree(self.tree_archive, self.commit) as reader:
                self.assertIs(type(reader), GitCommitTreeReader)
                self.assertEqual(
                    reader.read_file(
                        self.commit, "config/sr7-local-validation-receipt.json"
                    ),
                    self.receipt.to_bytes(),
                )

            with self.assertRaises(PublicationCLIError):
                _candidate_commit("0" * 40)

        alternate_archive = self.root / "alternate-tree.tar.gz"
        alternate_repository = self._make_repository(
            self.root / "archive-substitute",
            self.site,
            self.firebase,
            self.receipt_path,
        )
        alternate_commit = self._commit(alternate_repository, "archive substitute")
        create_candidate_tree_archive(alternate_repository, alternate_commit, alternate_archive)
        with self.assertRaises(PublicationCLIError):
            with _verify_candidate_tree(alternate_archive, self.commit):
                pass

    def test_final_caller_rejects_receipt_bytes_different_from_verified_tree(self):
        """The hosted caller must compare the supplied receipt to the archive."""

        supplied_receipt = self.root / "caller-replacement-receipt.json"
        forged = json.loads(self.receipt.to_bytes())
        forged["private_evidence"]["candidate_tree_sha256"] = "0" * 64
        supplied_receipt.write_bytes(public_contract._canonical(forged))

        args = SimpleNamespace(
            candidate_root=self.repository,
            candidate_commit=self.commit,
            firebase_json=self.repository / "firebase.json",
            output_directory=self.root / "caller-output",
            candidate_tree_archive=self.tree_archive,
            candidate_tree_sha256=hashlib.sha256(
                self.tree_archive.read_bytes()
            ).hexdigest(),
            local_validation_receipt=supplied_receipt,
            # This is the repository's complete trusted manifest; the
            # mismatch must fail before any downstream baseline work.
            trusted_input_manifest=(
                Path(__file__).resolve().parents[1]
                / "config"
                / "sr7-recovery-inputs.json"
            ),
            baseline_public_archive=self.root / "unused-baseline.tar.gz",
            baseline_sanitizer_record=self.root / "unused-sanitizer.json",
        )
        with patch.dict("os.environ", {"GITHUB_SHA": self.commit}, clear=False):
            with self.assertRaises(PublicationCLIError) as raised:
                prepare_reviewed_operation(args)
        self.assertIn("supplied local-validation receipt differs", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
