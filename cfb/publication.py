"""Prepare one exact publication package and bind it to an immutable commit.

Preparation is review evidence. Only :func:`bind_merged_candidate` returns the
strict record that later publication state machines may consume.
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
from typing import Mapping, Protocol

from .baseline import VerifiedBaseline
from .github_archive import ArchiveSpec, ImmutableArchive
from .publication_records import (
    ArchiveReference,
    AttemptIntentRecord,
    ProviderIdentity,
    ValidatedPackageRecord,
    canonical_json,
)
from .recovery_inputs import RecoveryInputBundle
from .release import Release, ReleaseValidationError, validate_release


class PublicationPreparationError(ValueError):
    """Candidate validation, packaging, or commit binding failed."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _site(candidate: str | Path | Release) -> Path:
    if isinstance(candidate, Release):
        return candidate.site
    path = Path(candidate)
    return path / "site" if (path / "site").is_dir() else path


def _inventory(root: Path, prefix: str = "website") -> tuple[tuple[str, str, int], ...]:
    if not root.is_dir():
        raise PublicationPreparationError("candidate site must be an existing directory")
    return tuple(
        (f"{prefix}/{path.relative_to(root).as_posix()}", _sha256_file(path), path.stat().st_size)
        for path in sorted(root.rglob("*")) if path.is_file()
    )


def _inventory_digest(entries: tuple[tuple[str, str, int], ...]) -> str:
    return _sha256_bytes(canonical_json({"files": [{"path": p, "sha256": h, "size": s} for p, h, s in entries]}))


def _validation_evidence(
    *,
    inventory_sha256: str,
    configuration_sha256: str,
    expected_baseline_sha256: str,
    retained_inputs_sha256: str,
) -> bytes:
    """Return the reconstructible record emitted by the fresh package gate."""

    return canonical_json({
        "schema_version": 1,
        "record_type": "package_validation",
        "outcome": "valid",
        "inventory_sha256": inventory_sha256,
        "configuration_sha256": configuration_sha256,
        "expected_baseline_sha256": expected_baseline_sha256,
        "retained_inputs_sha256": retained_inputs_sha256,
    })


def _deterministic_package(site: Path, firebase_json: Path, output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = [(firebase_json, "firebase.json")]
    entries.extend((path, f"website/{path.relative_to(site).as_posix()}") for path in sorted(site.rglob("*")) if path.is_file())
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path, relative in entries:
                    info = archive.gettarinfo(str(path), arcname=relative)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    info.mode = 0o644
                    with path.open("rb") as source:
                        archive.addfile(info, source)
    return _sha256_file(output)


_PREPARED_PACKAGE_TOKEN = object()


@dataclass(frozen=True, init=False)
class PreparedPackage:
    """Revalidated staged bytes; deliberately carries no commit claim."""

    archive: Path
    bundle_sha256: str
    inventory_sha256: str
    configuration_sha256: str
    expected_baseline_sha256: str
    expected_predecessor: ProviderIdentity
    retained_inputs_sha256: str
    validation_sha256: str
    site: Path
    firebase_json: Path

    def __init__(
        self, archive: Path, bundle_sha256: str, inventory_sha256: str,
        configuration_sha256: str, expected_baseline_sha256: str,
        expected_predecessor: ProviderIdentity, retained_inputs_sha256: str,
        validation_sha256: str, site: Path, firebase_json: Path, *,
        _token: object | None = None,
    ) -> None:
        if _token is not _PREPARED_PACKAGE_TOKEN:
            raise PublicationPreparationError("PreparedPackage values can only be created by candidate revalidation")
        for name, value in locals().copy().items():
            if name not in {"self", "_token"}:
                object.__setattr__(self, name, value)

    @classmethod
    def _create(cls, *values: object) -> "PreparedPackage":
        return cls(*values, _token=_PREPARED_PACKAGE_TOKEN)  # type: ignore[arg-type]

    def assert_current(self) -> None:
        if not self.archive.is_file() or _sha256_file(self.archive) != self.bundle_sha256:
            raise PublicationPreparationError("prepared package bytes changed after validation")
        if _inventory_digest(_inventory(self.site)) != self.inventory_sha256:
            raise PublicationPreparationError("prepared candidate site changed after validation")
        if not self.firebase_json.is_file() or _sha256_file(self.firebase_json) != self.configuration_sha256:
            raise PublicationPreparationError("prepared serving configuration changed after validation")


def prepare_review_package(
    candidate: str | Path | Release,
    *,
    baseline: VerifiedBaseline,
    firebase_json: str | Path,
    source_inputs: RecoveryInputBundle,
    retained_inputs_sha256: str,
    output: str | Path,
) -> PreparedPackage:
    """Revalidate and package staged bytes without asserting Git eligibility."""

    baseline.assert_current()
    source_inputs.assert_current()
    if source_inputs.bundle_sha256 != retained_inputs_sha256:
        raise PublicationPreparationError("retained input archive does not match the supplied provenance")
    report = validate_release(candidate, published_site=baseline, source_inputs=source_inputs)
    if not report.ok:
        raise ReleaseValidationError("publication candidate failed independent release validation", report)
    site = _site(candidate).resolve()
    config_path = Path(firebase_json).resolve()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationPreparationError("firebase.json is unreadable") from exc
    if not isinstance(config, Mapping) or not isinstance(config.get("hosting"), Mapping) or config["hosting"].get("public") != "website":
        raise PublicationPreparationError("firebase.json must publish the packaged website directory")
    inventory = _inventory(site)
    inventory_sha = _inventory_digest(inventory)
    configuration_sha = _sha256_file(config_path)
    validation_sha = _sha256_bytes(_validation_evidence(
        inventory_sha256=inventory_sha,
        configuration_sha256=configuration_sha,
        expected_baseline_sha256=baseline.record.digest,
        retained_inputs_sha256=retained_inputs_sha256,
    ))
    output_path = Path(output).resolve()
    bundle_sha = _deterministic_package(site, config_path, output_path)
    prepared = PreparedPackage._create(
        output_path, bundle_sha, inventory_sha, configuration_sha,
        baseline.record.digest, baseline.record.observed, retained_inputs_sha256,
        validation_sha, site, config_path,
    )
    prepared.assert_current()
    return prepared


class CommitTreeReader(Protocol):
    def list_files(self, commit: str, prefix: str) -> tuple[str, ...]: ...
    def read_file(self, commit: str, path: str) -> bytes: ...


class GitCommitTreeReader:
    """Read-only exact-byte access to committed repository trees."""

    def __init__(self, repository: str | Path = ".") -> None:
        self.repository = Path(repository)

    def _run(self, *arguments: str) -> bytes:
        try:
            return subprocess.run(
                ["git", "-C", str(self.repository), *arguments], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).stdout
        except subprocess.CalledProcessError as exc:
            raise PublicationPreparationError("could not read immutable candidate commit") from exc

    def list_files(self, commit: str, prefix: str) -> tuple[str, ...]:
        raw = self._run("ls-tree", "-r", "--name-only", "-z", commit, "--", prefix)
        return tuple(value.decode("utf-8") for value in raw.split(b"\0") if value)

    def read_file(self, commit: str, path: str) -> bytes:
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise PublicationPreparationError("commit path is unsafe")
        return self._run("show", f"{commit}:{path}")


class FakeCommitTreeReader:
    def __init__(self, commits: Mapping[str, Mapping[str, bytes]]) -> None:
        self.commits = {commit: dict(files) for commit, files in commits.items()}

    def list_files(self, commit: str, prefix: str) -> tuple[str, ...]:
        return tuple(sorted(path for path in self.commits.get(commit, {}) if path == prefix or path.startswith(prefix.rstrip("/") + "/")))

    def read_file(self, commit: str, path: str) -> bytes:
        try:
            return self.commits[commit][path]
        except KeyError as exc:
            raise PublicationPreparationError("candidate commit is missing a packaged path") from exc


def bind_merged_candidate(
    prepared: PreparedPackage,
    *,
    candidate_commit: str,
    reader: CommitTreeReader,
) -> ValidatedPackageRecord:
    """Require the merged commit to contain the exact prepared site and config."""

    prepared.assert_current()
    expected = _inventory(prepared.site)
    committed_paths = reader.list_files(candidate_commit, "website")
    if committed_paths != tuple(path for path, _digest, _size in expected):
        raise PublicationPreparationError("merged candidate inventory differs from reviewed bytes")
    for path, digest, size in expected:
        value = reader.read_file(candidate_commit, path)
        if len(value) != size or _sha256_bytes(value) != digest:
            raise PublicationPreparationError(f"merged candidate substituted {path}")
    if reader.read_file(candidate_commit, "firebase.json") != prepared.firebase_json.read_bytes():
        raise PublicationPreparationError("merged serving configuration differs from reviewed bytes")
    return ValidatedPackageRecord.create(
        candidate_commit=candidate_commit,
        bundle_sha256=prepared.bundle_sha256,
        inventory_sha256=prepared.inventory_sha256,
        configuration_sha256=prepared.configuration_sha256,
        expected_baseline_sha256=prepared.expected_baseline_sha256,
        retained_inputs_sha256=prepared.retained_inputs_sha256,
        validation_sha256=prepared.validation_sha256,
        expected_predecessor=prepared.expected_predecessor.to_dict(),
    )


@dataclass(frozen=True)
class SealedAttempt:
    package: ValidatedPackageRecord
    intent: AttemptIntentRecord
    package_reference: ArchiveReference
    package_record_reference: ArchiveReference
    validation_reference: ArchiveReference
    intent_reference: ArchiveReference
    retrieved_package: Path
    retrieved_package_record: Path
    retrieved_validation: Path
    retrieved_intent: Path
    retrieved_evidence: Mapping[str, Path]

    def __post_init__(self) -> None:
        from types import MappingProxyType
        object.__setattr__(self, "retrieved_evidence", MappingProxyType(dict(self.retrieved_evidence)))


def seal_attempt_evidence(
    package: ValidatedPackageRecord,
    *,
    prepared: PreparedPackage,
    commit_reader: CommitTreeReader,
    archive: ImmutableArchive,
    repository: str,
    package_tag: str,
    intent_tag: str,
    attempt_id: str,
    purpose: str,
    evidence_references: Mapping[str, ArchiveReference],
    protected_context: Mapping[str, str],
    retrieval_directory: str | Path,
) -> SealedAttempt:
    """Seal and retrieve package, retained evidence, then the separate intent."""

    prepared.assert_current()
    rebound = bind_merged_candidate(prepared, candidate_commit=package.candidate_commit, reader=commit_reader)
    if rebound.digest != package.digest:
        raise PublicationPreparationError("validated package does not bind the prepared review bytes")
    destination = Path(retrieval_directory)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sportsrank-attempt-seal-") as directory:
        work = Path(directory)
        package_record_path = work / "validated-package.json"
        package_record_path.write_bytes(canonical_json(package.to_dict()))
        validation_path = work / "package-validation.json"
        validation_path.write_bytes(_validation_evidence(
            inventory_sha256=prepared.inventory_sha256,
            configuration_sha256=prepared.configuration_sha256,
            expected_baseline_sha256=prepared.expected_baseline_sha256,
            retained_inputs_sha256=prepared.retained_inputs_sha256,
        ))
        package_assets = {
            prepared.archive.name: prepared.archive,
            package_record_path.name: package_record_path,
            validation_path.name: validation_path,
        }
        package_refs = archive.seal_or_reconcile(ArchiveSpec(
            repository, package_tag, package.candidate_commit, package_assets,
            f"SportsRank package {attempt_id}",
        ))
        try:
            package_ref = package_refs[prepared.archive.name]
            package_record_ref = package_refs[package_record_path.name]
            validation_ref = package_refs[validation_path.name]
        except KeyError as exc:
            raise PublicationPreparationError("sealed package evidence is incomplete") from exc
        if package_ref.sha256 != package.bundle_sha256:
            raise PublicationPreparationError("sealed package digest differs from the validated package")
        if package_record_ref.sha256 != package.digest:
            raise PublicationPreparationError("sealed package record digest differs from its logical record")
        if validation_ref.sha256 != package.validation_sha256:
            raise PublicationPreparationError("sealed validation evidence differs from the validated package")
        retrieved_package = archive.retrieve_and_verify(package_ref, destination / prepared.archive.name)
        retrieved_package_record = archive.retrieve_and_verify(
            package_record_ref, destination / package_record_path.name
        )
        retrieved_validation = archive.retrieve_and_verify(
            validation_ref, destination / validation_path.name
        )
        retrieved_evidence = {
            role: archive.retrieve_and_verify(ArchiveReference.from_value(reference), destination / f"{role}-{reference.asset_name}")
            for role, reference in evidence_references.items()
        }
        intent = AttemptIntentRecord.create(
            package=package, attempt_id=attempt_id, purpose=purpose,
            expected_predecessor=package.expected_predecessor.to_dict(),
            artifact_reference=package_ref.to_dict(),
            evidence_references={name: value.to_dict() for name, value in evidence_references.items()},
            protected_context=dict(protected_context),
        )
        intent_path = work / "attempt-intent.json"
        intent_path.write_bytes(canonical_json(intent.to_dict()))
        intent_refs = archive.seal_or_reconcile(ArchiveSpec(
            repository, intent_tag, package.candidate_commit,
            {intent_path.name: intent_path}, f"SportsRank intent {attempt_id}",
        ))
        intent_ref = intent_refs[intent_path.name]
        if intent_ref.sha256 != intent.digest:
            # canonical_json is the record digest's exact byte representation.
            raise PublicationPreparationError("sealed intent digest differs from its logical record")
        retrieved_intent = archive.retrieve_and_verify(intent_ref, destination / intent_path.name)
    return SealedAttempt(
        package, intent, package_ref, package_record_ref, validation_ref,
        intent_ref, retrieved_package, retrieved_package_record,
        retrieved_validation, retrieved_intent, dict(retrieved_evidence),
    )
