from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

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


class StubHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.status = 200
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False

    def read(self, limit: int) -> bytes:
        return self.body


class ProvenanceTransport:
    """Offline command/API transport beneath the concrete verifier."""

    def __init__(
        self,
        manifest: Path,
        run: dict[str, object],
        *,
        verified_sha256: str | None = None,
        returncode: int = 0,
        stderr: bytes = b"",
        mutate_after_command: bool = False,
    ) -> None:
        self.manifest = manifest
        self.run = dict(run)
        self.verified_sha256 = (
            sha(manifest.read_bytes())
            if verified_sha256 is None else verified_sha256
        )
        self.returncode = returncode
        self.stderr = stderr
        self.mutate_after_command = mutate_after_command
        self.commands: list[list[str]] = []
        self.urls: list[str] = []
        self._command_patch = None
        self._url_patch = None

    def _command(self, command: list[str]):
        self.commands.append(command)
        current = sha(self.manifest.read_bytes())
        if self.returncode != 0 or current != self.verified_sha256:
            return subprocess.CompletedProcess(
                command, self.returncode or 1, b"", self.stderr
            )
        body = json.dumps([{
            "verificationResult": {
                "statement": {
                    "subject": [{"digest": {"sha256": current}}]
                }
            }
        }]).encode()
        if self.mutate_after_command:
            self.manifest.write_bytes(self.manifest.read_bytes() + b"\n")
        return subprocess.CompletedProcess(command, 0, body, b"")

    def _urlopen(self, request, *, timeout: float):
        self.urls.append(request.full_url)
        return StubHttpResponse(json.dumps(self.run).encode())

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

    def transport(
        self,
        *,
        run: dict[str, object] | None = None,
        verified_sha256: str | None = None,
        returncode: int = 0,
        stderr: bytes = b"",
        mutate_after_command: bool = False,
    ) -> ProvenanceTransport:
        return ProvenanceTransport(
            self.manifest,
            run or self.run(),
            verified_sha256=verified_sha256,
            returncode=returncode,
            stderr=stderr,
            mutate_after_command=mutate_after_command,
        )

    def rehydrate(
        self,
        *,
        provenance_reader=None,
        provenance=None,
        transport: ProvenanceTransport | None = None,
        reader=None,
        destination: Path | None = None,
    ):
        if provenance_reader is not None and provenance is not None:
            raise ValueError("only one provenance reader may be supplied")
        concrete = provenance_reader or provenance or (
            GitHubPreparationProvenanceReader.from_github_token(
                {"GITHUB_TOKEN": "offline-fixture"}
            )
        )
        resolved_transport = transport or self.transport()
        with resolved_transport:
            return rehydrate_prepared_package(
                package_record=self.package_record,
                package_archive=self.archive,
                preparation_manifest=self.manifest,
                candidate_reader=reader or GitCommitTreeReader(self.repository),
                provenance_reader=concrete,
                materialize_to=destination or self.root / "materialized",
            )


class PublicationRehydrationTests(unittest.TestCase):
    def test_caller_supplied_noop_provenance_reader_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))

            class CallerSuppliedProof:
                def verify_attestation(self, artifact: Path, **unused) -> None:
                    return None

                def run(self, repository: str, run_id: str):
                    return fixture.run()

            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate(provenance_reader=CallerSuppliedProof())

    def test_provenance_reader_subclass_and_direct_construction_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))

            class ReaderSubclass(GitHubPreparationProvenanceReader):
                pass

            subclass = object.__new__(ReaderSubclass)
            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate(provenance_reader=subclass)
            with self.assertRaises(PreparationProvenanceError):
                GitHubPreparationProvenanceReader(object())

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
            transport = fixture.transport()
            forged = replace(
                fixture.package,
                expected_baseline_sha256="d" * 64,
                validation_sha256="e" * 64,
            )
            fixture.package_record.write_bytes(canonical_json(forged.to_dict()))
            fixture.write_manifest()

            with self.assertRaises(PreparationProvenanceError):
                fixture.rehydrate(transport=transport)

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
                with self.assertRaises(PreparationProvenanceError):
                    fixture.rehydrate()

    def test_mismatched_manifest_artifact_digest_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            fixture.write_manifest(package_record_sha256="f" * 64)
            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate()

    def test_tampered_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            transport = fixture.transport()
            fixture.archive.write_bytes(fixture.archive.read_bytes() + b"tamper")
            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate(transport=transport)

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
                fixture.rehydrate()

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
            transport = fixture.transport(
                run=fixture.run(head_sha=tree_sha),
            )

            with self.assertRaises(PublicationPreparationError):
                fixture.rehydrate(transport=transport)

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
                transport = fixture.transport(run=fixture.run(**changes))
                with self.assertRaises(PreparationProvenanceError):
                    fixture.rehydrate(transport=transport)

    def test_manifest_must_remain_exact_during_provenance_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            transport = fixture.transport(mutate_after_command=True)

            with self.assertRaises(PreparationProvenanceError):
                fixture.rehydrate(transport=transport)
            self.assertEqual(transport.urls, [])

    def test_concrete_attestation_failure_does_not_expose_stderr(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            transport = fixture.transport(
                returncode=1,
                stderr=b"private transport detail",
            )
            with self.assertRaises(PreparationProvenanceError) as raised:
                fixture.rehydrate(transport=transport)
            self.assertNotIn("private transport detail", str(raised.exception))

    def test_concrete_reader_pins_attestation_and_run_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RehydrationFixture(Path(temporary))
            transport = fixture.transport()
            prepared = fixture.rehydrate(transport=transport)

            self.assertEqual(prepared.bundle_sha256, fixture.package.bundle_sha256)
            self.assertEqual(len(transport.commands), 1)
            self.assertEqual(
                transport.urls,
                [f"https://api.github.com/repos/{REPOSITORY}/actions/runs/101"],
            )
            command = transport.commands[0]
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
