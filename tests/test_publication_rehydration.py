from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from cfb.publication import (
    FakeCommitTreeReader,
    GitCommitTreeReader,
    PublicationPreparationError,
    _deterministic_package,
    _inventory,
    _inventory_digest,
    _validation_evidence,
    bind_merged_candidate,
    rehydrate_prepared_package,
)
from cfb.publication_authorization import (
    FakeGitHubPreparationProvenanceReader,
    GitHubPreparationProvenanceReader,
    PreparationProvenanceError,
    preparation_manifest_bytes,
)
from cfb.publication_records import (
    ProviderIdentity,
    ProviderTarget,
    ValidatedPackageRecord,
    canonical_json,
)


REPOSITORY = "arkar16/sportsrank"
WORKFLOW = ".github/workflows/firebase-hosting-publish.yml"
COMMIT_ZERO = "0" * 40


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class RehydrationFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repository = root / "repository"
        self.repository.mkdir()
        subprocess.run(["git", "init", "--quiet", str(self.repository)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repository), "config", "user.email", "fixture@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repository), "config", "user.name", "Fixture"],
            check=True,
        )
        self.site = self.repository / "website"
        self.site.mkdir()
        (self.site / "index.html").write_bytes(b"<h1>prepared</h1>\n")
        self.config = self.repository / "firebase.json"
        self.config.write_bytes(b'{"hosting":{"public":"website"}}\n')
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "website", "firebase.json"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repository), "commit", "--quiet", "-m", "candidate"],
            check=True,
        )
        self.commit = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode().strip()
        self.archive = root / "package.tar.gz"
        bundle = _deterministic_package(self.site, self.config, self.archive)
        inventory = _inventory_digest(_inventory(self.site))
        config = sha(self.config.read_bytes())
        baseline = "b" * 64
        inputs = "c" * 64
        validation = sha(_validation_evidence(
            inventory_sha256=inventory,
            configuration_sha256=config,
            expected_baseline_sha256=baseline,
            retained_inputs_sha256=inputs,
        ))
        target = ProviderTarget("sportsrank-837af", "sportsrank-837af", "live")
        predecessor = ProviderIdentity(
            target,
            "sites/sportsrank-837af/channels/live/releases/previous",
            "sites/sportsrank-837af/versions/previous",
        )
        self.package = ValidatedPackageRecord.create(
            candidate_commit=self.commit,
            bundle_sha256=bundle,
            inventory_sha256=inventory,
            configuration_sha256=config,
            expected_baseline_sha256=baseline,
            retained_inputs_sha256=inputs,
            validation_sha256=validation,
            expected_predecessor=predecessor.to_dict(),
        )
        self.package_record = root / "package.json"
        self.package_record.write_bytes(canonical_json(self.package.to_dict()))
        self.manifest = root / "publication-preparation-manifest.json"
        self.write_manifest()

    def write_manifest(self, **overrides: object) -> None:
        values: dict[str, object] = {
            "repository": REPOSITORY,
            "workflow_path": WORKFLOW,
            "event": "workflow_dispatch",
            "ref": "refs/heads/main",
            "head_sha": self.commit,
            "run_id": "101",
            "run_attempt": "1",
            "package_archive_sha256": sha(self.archive.read_bytes()),
            "package_record_sha256": sha(self.package_record.read_bytes()),
        }
        values.update(overrides)
        self.manifest.write_bytes(preparation_manifest_bytes(**values))

    def run(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "id": 101,
            "run_attempt": 1,
            "path": WORKFLOW,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": self.commit,
            "status": "completed",
            "conclusion": "success",
        }
        value.update(overrides)
        return value

    def provenance(self, *, run: dict[str, object] | None = None):
        return FakeGitHubPreparationProvenanceReader(
            {sha(self.manifest.read_bytes())}, run or self.run()
        )

    def rehydrate(self, *, provenance=None, reader=None, destination: Path | None = None):
        return rehydrate_prepared_package(
            package_record=self.package_record,
            package_archive=self.archive,
            preparation_manifest=self.manifest,
            candidate_reader=reader or GitCommitTreeReader(self.repository),
            provenance_reader=provenance or self.provenance(),
            materialize_to=destination or self.root / "materialized",
        )


class PublicationRehydrationTests(unittest.TestCase):
    def test_genuine_attested_round_trip_uses_real_commit_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            prepared = fixture.rehydrate()

            self.assertEqual((prepared.site / "index.html").read_bytes(), b"<h1>prepared</h1>\n")
            self.assertEqual(prepared.firebase_json.read_bytes(), fixture.config.read_bytes())
            self.assertEqual(
                bind_merged_candidate(
                    prepared,
                    candidate_commit=fixture.commit,
                    reader=GitCommitTreeReader(fixture.repository),
                ),
                fixture.package,
            )

    def test_forged_record_and_self_consistent_manifest_are_not_authenticated(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            provenance = fixture.provenance()
            forged = replace(
                fixture.package,
                expected_baseline_sha256="d" * 64,
                validation_sha256="e" * 64,
            )
            fixture.package_record.write_bytes(canonical_json(forged.to_dict()))
            fixture.write_manifest()

            with self.assertRaises(PreparationProvenanceError):
                fixture.rehydrate(provenance=provenance)

    def test_authenticated_wrong_origin_fields_fail_closed(self):
        cases = {
            "repository": {"repository": "attacker/example"},
            "workflow": {"workflow_path": ".github/workflows/other.yml"},
            "event": {"event": "push"},
            "ref": {"ref": "refs/heads/other"},
            "run": {"run_id": "102"},
            "commit": {"head_sha": COMMIT_ZERO},
        }
        for name, changes in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                fixture = RehydrationFixture(Path(temporary))
                fixture.write_manifest(**changes)
                provenance = fixture.provenance()
                with self.assertRaises(PreparationProvenanceError):
                    fixture.rehydrate(provenance=provenance)

    def test_mismatched_manifest_artifact_digest_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            fixture.write_manifest(package_record_sha256="f" * 64)
            provenance = fixture.provenance()
            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate(provenance=provenance)

    def test_tampered_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            provenance = fixture.provenance()
            fixture.archive.write_bytes(fixture.archive.read_bytes() + b"tamper")
            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate(provenance=provenance)

    def test_fake_commit_reader_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            files = {
                "website/index.html": b"<h1>prepared</h1>\n",
                "firebase.json": fixture.config.read_bytes(),
            }
            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate(
                    reader=FakeCommitTreeReader({fixture.commit: files})
                )

    def test_attested_package_bytes_must_equal_the_real_candidate_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            (fixture.site / "index.html").write_bytes(b"<h1>substituted</h1>\n")
            bundle = _deterministic_package(fixture.site, fixture.config, fixture.archive)
            inventory = _inventory_digest(_inventory(fixture.site))
            validation = sha(_validation_evidence(
                inventory_sha256=inventory,
                configuration_sha256=fixture.package.configuration_sha256,
                expected_baseline_sha256=fixture.package.expected_baseline_sha256,
                retained_inputs_sha256=fixture.package.retained_inputs_sha256,
            ))
            fixture.package = replace(
                fixture.package,
                bundle_sha256=bundle,
                inventory_sha256=inventory,
                validation_sha256=validation,
            )
            fixture.package_record.write_bytes(canonical_json(fixture.package.to_dict()))
            fixture.write_manifest()

            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate(provenance=fixture.provenance())

    def test_git_tree_object_is_not_accepted_as_candidate_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            tree_sha = subprocess.run(
                ["git", "-C", str(fixture.repository), "rev-parse", "HEAD^{tree}"],
                check=True,
                stdout=subprocess.PIPE,
            ).stdout.decode().strip()
            fixture.package = replace(fixture.package, candidate_commit=tree_sha)
            fixture.package_record.write_bytes(canonical_json(fixture.package.to_dict()))
            fixture.write_manifest(head_sha=tree_sha)
            provenance = FakeGitHubPreparationProvenanceReader(
                {sha(fixture.manifest.read_bytes())},
                fixture.run(head_sha=tree_sha),
            )

            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate(provenance=provenance)

    def test_authenticated_manifest_still_requires_matching_completed_run(self):
        cases = {
            "attempt": {"run_attempt": 2},
            "path": {"path": f"{WORKFLOW}@other"},
            "commit": {"head_sha": COMMIT_ZERO},
            "status": {"status": "in_progress", "conclusion": None},
            "failure": {"conclusion": "failure"},
        }
        for name, changes in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                fixture = RehydrationFixture(Path(temporary))
                provenance = fixture.provenance(run=fixture.run(**changes))
                with self.assertRaises(PreparationProvenanceError):
                    fixture.rehydrate(provenance=provenance)

    def test_manifest_must_remain_exact_during_provenance_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))

            class MutatingReader:
                def verify_attestation(self, artifact: Path, **unused) -> None:
                    artifact.write_bytes(artifact.read_bytes() + b"\n")

                def run(self, repository: str, run_id: str):
                    raise AssertionError("run lookup must not follow a manifest race")

            with self.assertRaisesRegex(
                PreparationProvenanceError, "changed during"
            ):
                fixture.rehydrate(provenance=MutatingReader())

    def test_concrete_attestation_failure_does_not_expose_stderr(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))

            def runner(command, **options):
                return subprocess.CompletedProcess(
                    command, 1, b"", b"private transport detail"
                )

            reader = GitHubPreparationProvenanceReader(
                fixture.provenance(), runner=runner
            )
            with self.assertRaises(PreparationProvenanceError) as raised:
                fixture.rehydrate(provenance=reader)
            self.assertNotIn("private transport detail", str(raised.exception))

    def test_concrete_reader_pins_attestation_and_run_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            calls: list[list[str]] = []

            def runner(command, **options):
                calls.append(command)
                digest = sha(fixture.manifest.read_bytes())
                body = json.dumps([{
                    "verificationResult": {
                        "statement": {"subject": [{"digest": {"sha256": digest}}]}
                    }
                }]).encode()
                return subprocess.CompletedProcess(command, 0, body, b"")

            reader = GitHubPreparationProvenanceReader(
                fixture.provenance(), runner=runner
            )
            prepared = fixture.rehydrate(provenance=reader)

            self.assertEqual(prepared.bundle_sha256, fixture.package.bundle_sha256)
            self.assertEqual(len(calls), 1)
            command = calls[0]
            self.assertIn("--deny-self-hosted-runners", command)
            self.assertEqual(command[command.index("--repo") + 1], REPOSITORY)
            self.assertEqual(
                command[command.index("--source-digest") + 1], fixture.commit
            )
            self.assertEqual(
                command[command.index("--source-ref") + 1], "refs/heads/main"
            )
            self.assertEqual(
                command[command.index("--signer-workflow") + 1],
                f"{REPOSITORY}/{WORKFLOW}",
            )


if __name__ == "__main__":
    unittest.main()
