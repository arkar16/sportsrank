import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

from cfb.github_archive import (
    ArchiveError,
    ArchiveSpec,
    FakeImmutableArchive,
    GitHubReleaseArchive,
)
from cfb.publication import (
    FakeCommitTreeReader,
    GitCommitTreeReader,
    PreparedPackage,
    PublicationPreparationError,
    bind_merged_candidate,
    seal_attempt_evidence,
)
from cfb.publication_records import (
    ArchiveReference,
    AttemptIntentRecord,
    ProviderIdentity,
    ProviderTarget,
    RecordValidationError,
    SanitizedBaselineArchiveRecord,
    ValidatedPackageRecord,
    canonical_json,
)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def inventory(files: dict[str, bytes]) -> str:
    entries = [
        {"path": path, "sha256": sha(value), "size": len(value)}
        for path, value in sorted(files.items()) if path.startswith("website/")
    ]
    return sha(canonical_json({"files": entries}))


def validation(
    inventory_sha256: str,
    configuration_sha256: str,
    baseline_sha256: str,
    inputs_sha256: str,
) -> str:
    return sha(canonical_json({
        "schema_version": 1,
        "record_type": "package_validation",
        "outcome": "valid",
        "inventory_sha256": inventory_sha256,
        "configuration_sha256": configuration_sha256,
        "expected_baseline_sha256": baseline_sha256,
        "retained_inputs_sha256": inputs_sha256,
    }))


class PublicationArchiveTests(unittest.TestCase):
    def test_git_reader_accepts_only_an_exact_commit_object_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()

            def git(*arguments):
                return subprocess.run(
                    ["git", "-C", str(repository), *arguments], check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                ).stdout.decode().strip()

            git("init", "--object-format=sha1", "-q")
            site = repository / "website"
            site.mkdir()
            (site / "index.html").write_bytes(b"reviewed")
            config = repository / "firebase.json"
            config.write_bytes(b'{"hosting":{"public":"website"}}')
            git("add", "website/index.html", "firebase.json")
            git(
                "-c", "user.name=SportsRank Test",
                "-c", "user.email=sportsrank@example.invalid",
                "commit", "-q", "-m", "candidate",
            )
            git(
                "-c", "user.name=SportsRank Test",
                "-c", "user.email=sportsrank@example.invalid",
                "tag", "-a", "candidate-tag", "-m", "candidate tag",
            )
            commit = git("rev-parse", "HEAD")
            tree = git("rev-parse", "HEAD^{tree}")
            blob = git("rev-parse", "HEAD:website/index.html")
            annotated_tag = git("rev-parse", "refs/tags/candidate-tag")

            package_path = root / "candidate.tar.gz"
            package_path.write_bytes(b"package")
            committed = {
                "website/index.html": b"reviewed",
                "firebase.json": config.read_bytes(),
            }
            target = ProviderTarget("fixture-project", "fixture-site", "live")
            predecessor = ProviderIdentity(
                target,
                "sites/fixture-site/channels/live/releases/r1",
                "sites/fixture-site/versions/v1",
            )
            inventory_sha = inventory(committed)
            configuration_sha = sha(config.read_bytes())
            prepared = PreparedPackage._create(
                package_path, sha(b"package"), inventory_sha,
                configuration_sha, "4" * 64, predecessor, "5" * 64,
                validation(inventory_sha, configuration_sha, "4" * 64, "5" * 64),
                site, config,
            )
            reader = GitCommitTreeReader(repository)

            accepted = bind_merged_candidate(
                prepared, candidate_commit=commit, reader=reader
            )
            self.assertEqual(accepted.candidate_commit, commit)
            for label, object_sha in {
                "tree": tree, "blob": blob, "annotated-tag": annotated_tag,
            }.items():
                with self.subTest(label=label), self.assertRaises(
                    PublicationPreparationError
                ):
                    bind_merged_candidate(
                        prepared, candidate_commit=object_sha, reader=reader
                    )

    def test_interrupted_draft_is_found_on_a_later_release_page_and_resumed_by_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "asset.tar.gz"
            asset.write_bytes(b"asset bytes")
            draft = {
                "id": 77,
                "tag_name": "evidence-1",
                "target_commitish": "c" * 40,
                "draft": True,
                "immutable": False,
                "assets": [{
                    "id": 78, "name": asset.name,
                    "digest": f"sha256:{sha(asset.read_bytes())}",
                    "size": asset.stat().st_size, "state": "uploaded",
                }],
            }
            published = {**draft, "draft": False, "immutable": True}
            creations = []
            mutations = []
            published_state = False

            def gh(arguments, **kwargs):
                nonlocal published_state
                endpoint = arguments[2] if len(arguments) > 2 else ""
                if endpoint.endswith("/releases/tags/evidence-1"):
                    return subprocess.CompletedProcess(arguments, 1, b"", b"HTTP 404")
                if "/releases?per_page=100&page=1" in endpoint:
                    decoys = [{"id": index, "tag_name": f"other-{index}", "draft": True} for index in range(100)]
                    return subprocess.CompletedProcess(arguments, 0, json.dumps(decoys).encode(), b"")
                if "/releases?per_page=100&page=2" in endpoint:
                    return subprocess.CompletedProcess(arguments, 0, json.dumps([draft]).encode(), b"")
                if endpoint.endswith("/releases/77"):
                    return subprocess.CompletedProcess(arguments, 0, json.dumps(published if published_state else draft).encode(), b"")
                if endpoint.endswith("/git/ref/tags/evidence-1"):
                    tag = {"object": {"type": "commit", "sha": "c" * 40}}
                    return subprocess.CompletedProcess(arguments, 0, json.dumps(tag).encode(), b"")
                if arguments[1:3] == ["release", "create"]:
                    creations.append(arguments)
                    return subprocess.CompletedProcess(arguments, 1, b"", b"duplicate tag")
                if arguments[1:4] == ["api", "--method", "PATCH"]:
                    mutations.append(arguments)
                    published_state = True
                    return subprocess.CompletedProcess(arguments, 0, b"", b"")
                raise AssertionError(arguments)

            with patch("cfb.github_archive.subprocess.run", side_effect=gh):
                reference = GitHubReleaseArchive().seal_or_reconcile(ArchiveSpec(
                    "owner/repository", "evidence-1", "c" * 40,
                    {asset.name: asset}, "title",
                ))[asset.name]
            self.assertEqual(reference.release_id, "77")
            self.assertEqual(creations, [])
            self.assertEqual(
                mutations[0][4], "repos/owner/repository/releases/77"
            )

    def test_draft_upload_and_publish_use_the_exact_numeric_release_id(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "asset.tar.gz"
            asset.write_bytes(b"asset bytes")
            empty = {
                "id": 77, "tag_name": "evidence-1", "draft": True,
                "immutable": False, "assets": [],
            }
            complete = {
                **empty,
                "assets": [{
                    "id": 78, "name": asset.name,
                    "digest": f"sha256:{sha(asset.read_bytes())}",
                    "size": asset.stat().st_size, "state": "uploaded",
                }],
            }
            published = {**complete, "draft": False, "immutable": True}
            by_id_reads = 0
            commands = []

            def gh(arguments, **kwargs):
                nonlocal by_id_reads
                commands.append(arguments)
                if arguments[1:4] == ["api", "--hostname", "uploads.github.com"]:
                    return subprocess.CompletedProcess(arguments, 0, b"{}", b"")
                if arguments[1:4] == ["api", "--method", "PATCH"]:
                    return subprocess.CompletedProcess(arguments, 0, b"{}", b"")
                endpoint = arguments[2]
                if endpoint.endswith("/releases/tags/evidence-1"):
                    return subprocess.CompletedProcess(arguments, 1, b"", b"HTTP 404")
                if "/releases?per_page=100&page=1" in endpoint:
                    return subprocess.CompletedProcess(arguments, 0, json.dumps([empty]).encode(), b"")
                if endpoint.endswith("/releases/77"):
                    states = [empty, complete, published]
                    result = states[by_id_reads]
                    by_id_reads += 1
                    return subprocess.CompletedProcess(arguments, 0, json.dumps(result).encode(), b"")
                if endpoint.endswith("/git/ref/tags/evidence-1"):
                    tag = {"object": {"type": "commit", "sha": "c" * 40}}
                    return subprocess.CompletedProcess(arguments, 0, json.dumps(tag).encode(), b"")
                raise AssertionError(arguments)

            with patch("cfb.github_archive.subprocess.run", side_effect=gh):
                reference = GitHubReleaseArchive().seal_or_reconcile(ArchiveSpec(
                    "owner/repository", "evidence-1", "c" * 40,
                    {asset.name: asset}, "title",
                ))[asset.name]

            upload = next(command for command in commands if "uploads.github.com" in command)
            publish = next(command for command in commands if command[1:4] == ["api", "--method", "PATCH"])
            self.assertIn("repos/owner/repository/releases/77/assets?name=asset.tar.gz", upload)
            self.assertEqual(publish[4], "repos/owner/repository/releases/77")
            self.assertEqual(reference.release_id, "77")

    def test_incomplete_or_changed_draft_inventory_is_never_published(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "asset.tar.gz"
            asset.write_bytes(b"asset bytes")
            expected = {
                "id": 78, "name": asset.name,
                "digest": f"sha256:{sha(asset.read_bytes())}",
                "size": asset.stat().st_size, "state": "uploaded",
            }
            cases = {
                "missing": [],
                "concurrent": [expected, {
                    "id": 79, "name": "unexpected.tar.gz",
                    "digest": f"sha256:{sha(b'unexpected')}",
                    "size": len(b"unexpected"), "state": "uploaded",
                }],
                "substituted": [{**expected, "digest": f"sha256:{sha(b'substituted')}"}],
                "wrong-size": [{**expected, "size": asset.stat().st_size + 1}],
                "not-uploaded": [{**expected, "state": "new"}],
            }
            for label, refreshed_assets in cases.items():
                with self.subTest(label=label):
                    initial = {
                        "id": 77, "tag_name": "evidence-1", "draft": True,
                        "immutable": False, "assets": [],
                    }
                    refreshed = {**initial, "assets": refreshed_assets}
                    by_id_reads = 0
                    publishes = []

                    def gh(arguments, **kwargs):
                        nonlocal by_id_reads
                        if arguments[1:4] == ["api", "--hostname", "uploads.github.com"]:
                            return subprocess.CompletedProcess(arguments, 0, b"{}", b"")
                        if arguments[1:4] == ["api", "--method", "PATCH"]:
                            publishes.append(arguments)
                            return subprocess.CompletedProcess(arguments, 0, b"{}", b"")
                        endpoint = arguments[2]
                        if endpoint.endswith("/releases/tags/evidence-1"):
                            return subprocess.CompletedProcess(arguments, 1, b"", b"HTTP 404")
                        if "/releases?per_page=100&page=1" in endpoint:
                            return subprocess.CompletedProcess(arguments, 0, json.dumps([initial]).encode(), b"")
                        if endpoint.endswith("/releases/77"):
                            result = initial if by_id_reads == 0 else refreshed
                            by_id_reads += 1
                            return subprocess.CompletedProcess(arguments, 0, json.dumps(result).encode(), b"")
                        if endpoint.endswith("/git/ref/tags/evidence-1"):
                            tag = {"object": {"type": "commit", "sha": "c" * 40}}
                            return subprocess.CompletedProcess(arguments, 0, json.dumps(tag).encode(), b"")
                        raise AssertionError(arguments)

                    with patch("cfb.github_archive.subprocess.run", side_effect=gh):
                        with self.assertRaises(ArchiveError):
                            GitHubReleaseArchive().seal_or_reconcile(ArchiveSpec(
                                "owner/repository", "evidence-1", "c" * 40,
                                {asset.name: asset}, "title",
                            ))
                    self.assertEqual(publishes, [])

    def test_release_target_hint_cannot_substitute_for_the_actual_tag_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "asset.tar.gz"
            asset.write_bytes(b"asset bytes")
            release = {
                "id": 91, "tag_name": "evidence-1",
                "target_commitish": "c" * 40, "draft": False,
                "immutable": True,
                "assets": [{
                    "id": 92, "name": asset.name,
                    "digest": f"sha256:{sha(asset.read_bytes())}",
                    "size": asset.stat().st_size, "state": "uploaded",
                }],
            }

            def gh(arguments, **kwargs):
                endpoint = arguments[2]
                if endpoint.endswith("/git/ref/tags/evidence-1"):
                    value = {"object": {"type": "commit", "sha": "d" * 40}}
                else:
                    value = release
                return subprocess.CompletedProcess(arguments, 0, json.dumps(value).encode(), b"")

            with patch("cfb.github_archive.subprocess.run", side_effect=gh):
                with self.assertRaises(ArchiveError):
                    GitHubReleaseArchive().seal_or_reconcile(ArchiveSpec(
                        "owner/repository", "evidence-1", "c" * 40,
                        {asset.name: asset}, "title",
                    ))

    def test_annotated_tag_chain_resolves_to_the_exact_commit(self):
        tag_object = "a" * 40
        responses = {
            "repos/owner/repository/git/ref/tags/evidence-1": {
                "object": {"type": "tag", "sha": tag_object}
            },
            f"repos/owner/repository/git/tags/{tag_object}": {
                "object": {"type": "commit", "sha": "c" * 40}
            },
        }

        def gh(arguments, **kwargs):
            return subprocess.CompletedProcess(
                arguments, 0, json.dumps(responses[arguments[2]]).encode(), b""
            )

        with patch("cfb.github_archive.subprocess.run", side_effect=gh):
            self.assertEqual(
                GitHubReleaseArchive()._tag_commit(
                    "owner/repository", "evidence-1"
                ),
                "c" * 40,
            )

    def test_archive_errors_do_not_echo_raw_cli_stderr(self):
        failure = subprocess.CompletedProcess(
            ["gh", "api", "x"], 1, b"", b"authorization token=private-value"
        )
        with patch("cfb.github_archive.subprocess.run", return_value=failure):
            with self.assertRaises(ArchiveError) as raised:
                GitHubReleaseArchive()._run(["api", "x"])
        self.assertNotIn("private-value", str(raised.exception))

    def test_reviewed_bytes_only_become_eligible_when_the_commit_matches_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_bytes(b"reviewed")
            config = root / "firebase.json"
            config.write_text('{"hosting":{"public":"website"}}')
            package = root / "package.tar.gz"
            package.write_bytes(b"sealed package")
            target = ProviderTarget("fixture-project", "fixture-site", "live")
            predecessor = ProviderIdentity(
                target,
                "sites/fixture-site/channels/live/releases/r1",
                "sites/fixture-site/versions/v1",
            )
            files = {
                "website/index.html": b"reviewed",
                "firebase.json": config.read_bytes(),
            }
            prepared = PreparedPackage._create(
                package, sha(package.read_bytes()), inventory(files), sha(config.read_bytes()),
                "4" * 64, predecessor, "5" * 64, "6" * 64, site, config,
            )
            record = bind_merged_candidate(
                prepared, candidate_commit="c" * 40,
                reader=FakeCommitTreeReader({"c" * 40: files}),
            )
            self.assertEqual(record.expected_predecessor, predecessor)
            self.assertEqual(record.bundle_sha256, sha(package.read_bytes()))

            substituted = dict(files)
            substituted["website/index.html"] = b"different"
            with self.assertRaises(PublicationPreparationError):
                bind_merged_candidate(
                    prepared, candidate_commit="d" * 40,
                    reader=FakeCommitTreeReader({"d" * 40: substituted}),
                )
            substituted_config = dict(files)
            substituted_config["firebase.json"] = b'{"hosting":{"public":"other"}}'
            with self.assertRaises(PublicationPreparationError):
                bind_merged_candidate(
                    prepared, candidate_commit="e" * 40,
                    reader=FakeCommitTreeReader({"e" * 40: substituted_config}),
                )

    def test_archive_seals_once_and_retrieval_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "candidate.tar.gz"
            asset.write_bytes(b"exact candidate")
            spec = ArchiveSpec(
                "owner/repository", "sportsrank-attempt-1-package", "c" * 40,
                {asset.name: asset}, "SportsRank attempt 1 package",
            )
            archive = FakeImmutableArchive()
            reference = archive.seal_or_reconcile(spec)[asset.name]
            downloaded = archive.retrieve_and_verify(reference, root / "downloaded.tar.gz")
            self.assertEqual(downloaded.read_bytes(), asset.read_bytes())
            self.assertEqual(archive.seal_or_reconcile(spec)[asset.name], reference)

            asset.write_bytes(b"replacement")
            with self.assertRaises(ArchiveError):
                archive.seal_or_reconcile(spec)
            archive.corrupt(reference, b"corrupt")
            with self.assertRaises(ArchiveError):
                archive.retrieve_and_verify(reference, root / "bad.tar.gz")

            forged = reference.to_dict()
            forged["asset_id"] = "999"
            with self.assertRaises(ArchiveError):
                archive.retrieve_and_verify(
                    ArchiveReference.from_dict(forged), root / "forged.tar.gz"
                )

    def test_github_adapter_pins_release_commit_asset_identity_and_exact_bytes(self):
        class OfflineGitHubReleaseArchive(GitHubReleaseArchive):
            def __init__(self, release, content):
                self.release = release
                self.content = content

            def _release(self, repository, tag):
                return self.release

            def _release_by_id(self, repository, release_id):
                return self.release

            def _tag_commit(self, repository, tag):
                return self.release["actual_tag_commit"]

            def _run(self, arguments, *, binary=False):
                return self.content

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"immutable bytes"
            asset = root / "candidate.tar.gz"
            asset.write_bytes(content)
            release = {
                "id": 41,
                "tag_name": "sportsrank-attempt-1-package",
                "target_commitish": "c" * 40,
                "draft": False,
                "immutable": True,
                "actual_tag_commit": "c" * 40,
                "assets": [{
                    "id": 91,
                    "name": asset.name,
                    "digest": f"sha256:{sha(content)}",
                    "size": len(content),
                    "state": "uploaded",
                }],
            }
            archive = OfflineGitHubReleaseArchive(release, content)
            spec = ArchiveSpec(
                "owner/repository", release["tag_name"], "c" * 40,
                {asset.name: asset}, "title",
            )
            reference = archive.seal_or_reconcile(spec)[asset.name]
            self.assertEqual(reference.target_commit, "c" * 40)
            self.assertEqual(
                archive.retrieve_and_verify(reference, root / "download").read_bytes(),
                content,
            )

            release["target_commitish"] = "d" * 40
            release["actual_tag_commit"] = "d" * 40
            with self.assertRaises(ArchiveError):
                archive.retrieve_and_verify(reference, root / "wrong-commit")
            release["actual_tag_commit"] = "c" * 40
            archive.content = b"substituted"
            with self.assertRaises(ArchiveError):
                archive.retrieve_and_verify(reference, root / "substituted")

    def test_attempt_is_ready_only_after_package_evidence_and_intent_are_separately_retrieved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"; site.mkdir(); (site / "index.html").write_bytes(b"site")
            config = root / "firebase.json"; config.write_bytes(b'{"hosting":{"public":"website"}}')
            package_path = root / "candidate.tar.gz"; package_path.write_bytes(b"package")
            target = ProviderTarget("fixture-project", "fixture-site", "live")
            predecessor = ProviderIdentity(target, "sites/fixture-site/channels/live/releases/r1", "sites/fixture-site/versions/v1")
            committed = {"website/index.html": b"site", "firebase.json": config.read_bytes()}
            reader = FakeCommitTreeReader({"c" * 40: committed})
            inventory_sha = inventory(committed)
            configuration_sha = sha(config.read_bytes())
            prepared = PreparedPackage._create(
                package_path, sha(b"package"), inventory_sha, configuration_sha,
                "4" * 64, predecessor, "5" * 64,
                validation(inventory_sha, configuration_sha, "4" * 64, "5" * 64),
                site, config,
            )
            package = bind_merged_candidate(prepared, candidate_commit="c" * 40, reader=reader)
            archive = FakeImmutableArchive()
            evidence = {}
            for role in ("baseline", "source_inputs", "original_prepared"):
                path = root / f"{role}.tar.gz"; path.write_bytes(role.encode())
                evidence[role] = archive.seal_or_reconcile(ArchiveSpec("owner/repository", f"retained-{role}", "c" * 40, {path.name: path}, role))[path.name]
            sealed = seal_attempt_evidence(
                package, prepared=prepared, commit_reader=reader, archive=archive, repository="owner/repository",
                package_tag="attempt-1-package", intent_tag="attempt-1-intent",
                attempt_id="attempt-1", purpose="normal", evidence_references=evidence,
                protected_context={
                    "repository": "owner/repository", "workflow_ref": "owner/repository/.github/workflows/publish.yml@refs/heads/main",
                    "workflow_sha": "c" * 40, "run_id": "123", "run_attempt": "1", "environment": "production",
                }, retrieval_directory=root / "retrieved",
            )
            self.assertEqual(sealed.retrieved_package.read_bytes(), b"package")
            self.assertEqual(
                json.loads(sealed.retrieved_package_record.read_bytes()),
                sealed.package.to_dict(),
            )
            self.assertEqual(
                sha(sealed.retrieved_validation.read_bytes()),
                sealed.package.validation_sha256,
            )
            validation_record = json.loads(sealed.retrieved_validation.read_bytes())
            self.assertEqual(validation_record["outcome"], "valid")
            self.assertEqual(
                validation_record["inventory_sha256"], package.inventory_sha256
            )
            self.assertEqual(json.loads(sealed.retrieved_intent.read_bytes()), sealed.intent.to_dict())
            self.assertEqual(set(sealed.retrieved_evidence), set(evidence))
            self.assertNotEqual(sealed.package_reference.release_id, sealed.intent_reference.release_id)
            self.assertEqual(
                sealed.package_record_reference.release_id,
                sealed.package_reference.release_id,
            )
            self.assertEqual(
                sealed.validation_reference.release_id,
                sealed.package_reference.release_id,
            )

            with self.assertRaises(PublicationPreparationError):
                PreparedPackage(package_path, sha(b"package"), inventory(committed), sha(config.read_bytes()), "4" * 64, predecessor, "5" * 64, "6" * 64, site, config)

            wrong_predecessor = ProviderIdentity(
                target,
                "sites/fixture-site/channels/live/releases/r2",
                "sites/fixture-site/versions/v2",
            )
            with self.assertRaises(RecordValidationError):
                AttemptIntentRecord.create(
                    package=package,
                    attempt_id="attempt-2",
                    purpose="normal",
                    expected_predecessor=wrong_predecessor.to_dict(),
                    artifact_reference=sealed.package_reference.to_dict(),
                    evidence_references={
                        name: reference.to_dict()
                        for name, reference in evidence.items()
                    },
                    protected_context={
                        "repository": "owner/repository",
                        "workflow_ref": "owner/repository/.github/workflows/publish.yml@refs/heads/main",
                        "workflow_sha": "c" * 40,
                        "run_id": "123",
                        "run_attempt": "1",
                        "environment": "production",
                    },
                )

            wrong_commit_reference = sealed.package_reference.to_dict()
            wrong_commit_reference["target_commit"] = "d" * 40
            with self.assertRaises(RecordValidationError):
                AttemptIntentRecord.create(
                    package=package,
                    attempt_id="attempt-3",
                    purpose="normal",
                    expected_predecessor=predecessor.to_dict(),
                    artifact_reference=wrong_commit_reference,
                    evidence_references={
                        name: reference.to_dict()
                        for name, reference in evidence.items()
                    },
                    protected_context={
                        "repository": "owner/repository",
                        "workflow_ref": "owner/repository/.github/workflows/publish.yml@refs/heads/main",
                        "workflow_sha": "c" * 40,
                        "run_id": "123",
                        "run_attempt": "1",
                        "environment": "production",
                    },
                )

    def test_intent_sealing_failure_never_returns_a_ready_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_bytes(b"site")
            config = root / "firebase.json"
            config.write_bytes(b'{"hosting":{"public":"website"}}')
            package_path = root / "candidate.tar.gz"
            package_path.write_bytes(b"package")
            target = ProviderTarget("fixture-project", "fixture-site", "live")
            predecessor = ProviderIdentity(
                target,
                "sites/fixture-site/channels/live/releases/r1",
                "sites/fixture-site/versions/v1",
            )
            committed = {
                "website/index.html": b"site",
                "firebase.json": config.read_bytes(),
            }
            reader = FakeCommitTreeReader({"c" * 40: committed})
            inventory_sha = inventory(committed)
            configuration_sha = sha(config.read_bytes())
            prepared = PreparedPackage._create(
                package_path, sha(b"package"), inventory_sha,
                configuration_sha, "4" * 64, predecessor,
                "5" * 64,
                validation(inventory_sha, configuration_sha, "4" * 64, "5" * 64),
                site, config,
            )
            package = bind_merged_candidate(
                prepared, candidate_commit="c" * 40, reader=reader
            )
            class IntentFailureArchive(FakeImmutableArchive):
                def seal_or_reconcile(self, spec):
                    if spec.tag.endswith("-intent"):
                        raise ArchiveError("intent seal failed")
                    return super().seal_or_reconcile(spec)

            failing = IntentFailureArchive()
            failing_evidence = {}
            for role in ("baseline", "source_inputs", "original_prepared"):
                path = root / f"retry-{role}.tar.gz"; path.write_bytes(role.encode())
                failing_evidence[role] = failing.seal_or_reconcile(ArchiveSpec("owner/repository", f"retry-{role}", "c" * 40, {path.name: path}, role))[path.name]
            with self.assertRaisesRegex(ArchiveError, "intent seal failed"):
                seal_attempt_evidence(
                    package, prepared=prepared, commit_reader=reader, archive=failing,
                    repository="owner/repository", package_tag="retry-package",
                    intent_tag="retry-intent", attempt_id="retry", purpose="normal",
                    evidence_references=failing_evidence,
                    protected_context={
                        "repository": "owner/repository", "workflow_ref": "owner/repository/.github/workflows/publish.yml@refs/heads/main",
                        "workflow_sha": "c" * 40, "run_id": "124", "run_attempt": "1", "environment": "production",
                    }, retrieval_directory=root / "retry-retrieved",
                )

    def test_archive_reference_rejects_bool_version_and_unavailable_immutability(self):
        raw = ArchiveReference(
            "owner/repository", "1", "tag", "c" * 40,
            "2", "asset", "a" * 64, 1, True,
        ).to_dict()
        raw["schema_version"] = True
        with self.assertRaises(RecordValidationError):
            ArchiveReference.from_dict(raw)
        canonical = ArchiveReference(
            "owner/repository", "1", "tag", "c" * 40,
            "2", "asset", "a" * 64, 1, True,
        ).to_dict()
        for field in ("release_id", "asset_id"):
            for invalid in (True, "0", "-1", "not-numeric"):
                with self.subTest(field=field, invalid=invalid):
                    malformed = dict(canonical)
                    malformed[field] = invalid
                    with self.assertRaises(RecordValidationError):
                        ArchiveReference.from_dict(malformed)
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "asset"
            asset.write_bytes(b"x")
            with self.assertRaises(ArchiveError):
                FakeImmutableArchive(immutability_available=False).seal_or_reconcile(
                    ArchiveSpec("owner/repository", "tag", "c" * 40, {"asset": asset}, "title")
                )

    def test_attempt_evidence_roles_cannot_alias_one_archive_asset(self):
        target = ProviderTarget("fixture-project", "fixture-site", "live")
        predecessor = ProviderIdentity(
            target,
            "sites/fixture-site/channels/live/releases/r1",
            "sites/fixture-site/versions/v1",
        )
        package = ValidatedPackageRecord.create(
            candidate_commit="c" * 40,
            bundle_sha256="1" * 64,
            inventory_sha256="2" * 64,
            configuration_sha256="3" * 64,
            expected_baseline_sha256="4" * 64,
            retained_inputs_sha256="5" * 64,
            validation_sha256="6" * 64,
            expected_predecessor=predecessor.to_dict(),
        )
        reference = ArchiveReference(
            "owner/repository", "1", "shared", "c" * 40,
            "2", "shared.tar.gz", "1" * 64, 1, True,
        )
        with self.assertRaises(RecordValidationError):
            AttemptIntentRecord.create(
                package=package, attempt_id="attempt-1", purpose="normal",
                expected_predecessor=predecessor.to_dict(),
                artifact_reference=reference.to_dict(),
                evidence_references={role: reference.to_dict() for role in (
                    "baseline", "source_inputs", "original_prepared"
                )},
                protected_context={
                    "repository": "owner/repository",
                    "workflow_ref": "owner/repository/.github/workflows/publish.yml@refs/heads/main",
                    "workflow_sha": "c" * 40, "run_id": "1",
                    "run_attempt": "1", "environment": "production",
                },
            )

    def test_concrete_github_adapter_requires_rest_immutability_and_asset_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "asset.tar.gz"
            asset.write_bytes(b"asset bytes")
            response = {
                "id": 91,
                "tag_name": "evidence-1",
                "target_commitish": "c" * 40,
                "draft": False,
                "immutable": True,
                "assets": [{
                    "id": 92,
                    "name": asset.name,
                    "digest": f"sha256:{sha(asset.read_bytes())}",
                    "size": asset.stat().st_size,
                    "state": "uploaded",
                }],
            }

            def gh(arguments, **kwargs):
                if "/releases/assets/92" in arguments[2]:
                    return subprocess.CompletedProcess(arguments, 0, asset.read_bytes(), b"")
                if arguments[2].endswith("/git/ref/tags/evidence-1"):
                    tag = {"object": {"type": "commit", "sha": "c" * 40}}
                    return subprocess.CompletedProcess(arguments, 0, json.dumps(tag).encode(), b"")
                return subprocess.CompletedProcess(arguments, 0, json.dumps(response).encode(), b"")

            adapter = GitHubReleaseArchive()
            spec = ArchiveSpec(
                "owner/repository", "evidence-1", "c" * 40,
                {asset.name: asset}, "title",
            )
            with patch("cfb.github_archive.subprocess.run", side_effect=gh):
                reference = adapter.seal_or_reconcile(spec)[asset.name]
                downloaded = adapter.retrieve_and_verify(reference, root / "downloaded")
            self.assertEqual(reference.release_id, "91")
            self.assertEqual(downloaded.read_bytes(), asset.read_bytes())

            response["immutable"] = False
            with patch("cfb.github_archive.subprocess.run", side_effect=gh):
                with self.assertRaises(ArchiveError):
                    adapter.seal_or_reconcile(spec)

            response["immutable"] = True
            for field, invalid in (
                ("id", True), ("id", 0), ("id", -1), ("id", "91"),
                ("asset.id", True), ("asset.id", 0),
                ("asset.id", -1), ("asset.id", "92"),
            ):
                with self.subTest(field=field, invalid=invalid):
                    response["id"] = 91
                    response["assets"][0]["id"] = 92
                    if field == "id":
                        response["id"] = invalid
                    else:
                        response["assets"][0]["id"] = invalid
                    with patch("cfb.github_archive.subprocess.run", side_effect=gh):
                        with self.assertRaises(ArchiveError):
                            adapter.seal_or_reconcile(spec)

    def test_sanitizer_record_keeps_source_and_derivative_digests_distinct(self):
        target = ProviderTarget("fixture-project", "fixture-site", "live")
        observed = ProviderIdentity(
            target,
            "sites/fixture-site/channels/live/releases/r1",
            "sites/fixture-site/versions/v1",
        )
        record = SanitizedBaselineArchiveRecord(
            "1" * 64, "2" * 64, "3" * 64,
            {"capture.json": "4" * 64}, {"capture.json": "5" * 64},
            "firebase-provider-evidence-allowlist-v1",
            ("/channel-before.json/release/createUser",), 1, 2, target, observed,
        )
        self.assertNotEqual(record.source_archive_sha256, record.derivative_archive_sha256)
        self.assertEqual(SanitizedBaselineArchiveRecord.from_dict(record.to_dict()), record)


if __name__ == "__main__":
    unittest.main()
