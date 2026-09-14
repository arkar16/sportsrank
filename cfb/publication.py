"""Prepare one exact publication package and bind it to an immutable commit.

Preparation is review evidence. Only :func:`bind_merged_candidate` returns the
strict record that later publication state machines may consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import threading
from typing import Any, Callable, Mapping, Protocol

from .baseline import BaselineValidationError, VerifiedBaseline
from .firebase import (
    FirebaseDeployArtifact,
    FirebasePublicationError,
    FirebasePublicationAdapter,
    FirebaseReadAdapter,
    ProviderRejectedError,
    ProviderWriteUncertain,
)
from .github_archive import ArchiveError, ArchiveSpec, ImmutableArchive
from .publication_authorization import (
    ApprovalReader,
    GitHubRuntimeContext,
    authorize_protected_execution,
)
from .publication_records import (
    ArchiveReference,
    AttemptIntentRecord,
    BaselineRecord,
    ExternalPredecessorRecord,
    ProviderIdentity,
    ProviderResultRecord,
    ReconciliationRecord,
    SanitizedBaselineArchiveRecord,
    ValidatedPackageRecord,
    VerificationRecord,
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
    def require_commit(self, commit: str) -> None: ...
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

    def require_commit(self, commit: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise PublicationPreparationError(
                "candidate commit must be an exact immutable SHA"
            )
        if self._run("cat-file", "-t", commit).strip() != b"commit":
            raise PublicationPreparationError(
                "candidate identity does not name a Git commit object"
            )

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

    def require_commit(self, commit: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or commit not in self.commits:
            raise PublicationPreparationError(
                "candidate commit must be an exact immutable commit SHA"
            )

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
    reader.require_commit(candidate_commit)
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


class PublicationExecutionError(RuntimeError):
    """The protected publication operation could not safely make a write."""


_PREDECESSOR_TOKEN = object()


@dataclass(frozen=True, init=False)
class PriorVerifiedPublication:
    """Coordinator-issued capability for one fully reconciled publication."""

    repository: str
    intent: AttemptIntentRecord
    provider_result: ProviderResultRecord
    verification: VerificationRecord
    reconciliation: ReconciliationRecord
    provider_result_reference: ArchiveReference
    provider_result_source_reference: ArchiveReference
    verification_reference: ArchiveReference
    verification_source_reference: ArchiveReference
    reconciliation_reference: ArchiveReference
    reconciliation_source_reference: ArchiveReference

    def __init__(self, *values: object, _token: object | None = None) -> None:
        if _token is not _PREDECESSOR_TOKEN:
            raise PublicationExecutionError(
                "predecessor capabilities require coordinator reconstruction"
            )
        for name, value in zip(self.__dataclass_fields__, values, strict=True):
            object.__setattr__(self, name, value)

    @classmethod
    def _create(cls, *values: object) -> "PriorVerifiedPublication":
        return cls(*values, _token=_PREDECESSOR_TOKEN)


@dataclass(frozen=True, init=False)
class ExternalVerifiedPredecessor:
    """Coordinator-issued capability for a complete external live capture."""

    repository: str
    baseline_sha256: str
    observed_identity: ProviderIdentity
    observation: ExternalPredecessorRecord
    capture_reference: ArchiveReference
    sanitizer_reference: ArchiveReference
    observation_reference: ArchiveReference
    observation_source_reference: ArchiveReference

    def __init__(self, *values: object, _token: object | None = None) -> None:
        if _token is not _PREDECESSOR_TOKEN:
            raise PublicationExecutionError(
                "external predecessor capabilities require coordinator reconstruction"
            )
        for name, value in zip(self.__dataclass_fields__, values, strict=True):
            object.__setattr__(self, name, value)

    @classmethod
    def _create(cls, *values: object) -> "ExternalVerifiedPredecessor":
        return cls(*values, _token=_PREDECESSOR_TOKEN)


VerifiedPublicationPredecessor = (
    PriorVerifiedPublication | ExternalVerifiedPredecessor
)


@dataclass(frozen=True)
class PublicationTags:
    package: str
    intent: str
    provider_result: str
    verification: str


@dataclass(frozen=True)
class SealedRecordEvidence:
    record_reference: ArchiveReference
    source_reference: ArchiveReference
    retrieved_record: Path
    retrieved_source: Path


@dataclass(frozen=True)
class RecordedPublicationAttempt:
    """An interrupted attempt reconstructed from append-only record evidence."""

    attempt: SealedAttempt
    provider_result: ProviderResultRecord | None = None
    provider_evidence: SealedRecordEvidence | None = None
    verification: VerificationRecord | None = None
    verification_evidence: SealedRecordEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, SealedAttempt):
            raise PublicationExecutionError("recorded attempt is invalid")
        if (self.provider_result is None) != (self.provider_evidence is None):
            raise PublicationExecutionError(
                "provider result and evidence must be supplied together"
            )
        if (self.verification is None) != (self.verification_evidence is None):
            raise PublicationExecutionError(
                "verification and evidence must be supplied together"
            )
        if self.provider_result is not None and (
            self.provider_result.attempt_id != self.attempt.intent.attempt_id
            or self.provider_result.intent_sha256 != self.attempt.intent.digest
        ):
            raise PublicationExecutionError(
                "provider result does not link to the recorded intent"
            )
        if self.verification is not None and (
            self.provider_result is None
            or self.verification.provider_result_sha256
            != self.provider_result.digest
        ):
            raise PublicationExecutionError(
                "verification does not link to the recorded provider result"
            )


@dataclass(frozen=True)
class ReconciliationTags:
    observation: str
    provider_result: str
    verification: str


@dataclass(frozen=True)
class ReconciliationRun:
    state: str
    intent: AttemptIntentRecord
    observed_identity: ProviderIdentity
    observation: ReconciliationRecord
    observation_evidence: SealedRecordEvidence | None
    provider_result: ProviderResultRecord | None
    provider_evidence: SealedRecordEvidence | None
    verification: VerificationRecord | None
    verification_evidence: SealedRecordEvidence | None
    permitted_next_operations: tuple[str, ...]
    _predecessor: PriorVerifiedPublication | None = None

    @property
    def ordinary_successor_allowed(self) -> bool:
        return self.state == "reconciled_verified"

    def as_prior(self) -> PriorVerifiedPublication:
        if (
            not self.ordinary_successor_allowed
            or self.observation_evidence is None
            or self.provider_result is None
            or self.provider_evidence is None
            or self.verification is None
            or self.verification_evidence is None
            or self._predecessor is None
            or self.observation_evidence.record_reference
            != self._predecessor.reconciliation_reference
            or self.observation_evidence.source_reference
            != self._predecessor.reconciliation_source_reference
            or self.provider_evidence.record_reference
            != self._predecessor.provider_result_reference
            or self.provider_evidence.source_reference
            != self._predecessor.provider_result_source_reference
            or self.verification_evidence.record_reference
            != self._predecessor.verification_reference
            or self.verification_evidence.source_reference
            != self._predecessor.verification_source_reference
        ):
            raise PublicationExecutionError(
                "only a fully sealed verified reconciliation is a predecessor"
            )
        return self._predecessor


@dataclass(frozen=True)
class ExternalReconciliationRun:
    """A complete immutable capture of a previously unknown live predecessor."""

    state: str
    baseline: VerifiedBaseline
    observed_identity: ProviderIdentity
    observation: ExternalPredecessorRecord
    observation_evidence: SealedRecordEvidence | None
    permitted_next_operations: tuple[str, ...]
    _predecessor: ExternalVerifiedPredecessor | None = None

    @property
    def ordinary_successor_allowed(self) -> bool:
        return self.state == "external_verified"

    def as_prior(self) -> ExternalVerifiedPredecessor:
        if (
            not self.ordinary_successor_allowed
            or self.observation_evidence is None
            or self._predecessor is None
            or self.observation_evidence.record_reference
            != self._predecessor.observation_reference
            or self.observation_evidence.source_reference
            != self._predecessor.observation_source_reference
        ):
            raise PublicationExecutionError(
                "only a fully sealed external reconciliation is a predecessor"
            )
        return self._predecessor


@dataclass(frozen=True)
class PublicationRun:
    """Truthful terminal state from SR-12; SR-13 owns state resolution."""

    state: str
    attempt: SealedAttempt
    provider_result: ProviderResultRecord
    provider_evidence: SealedRecordEvidence | None
    verification: VerificationRecord | None
    verification_evidence: SealedRecordEvidence | None
    deployment_may_have_changed: bool
    permitted_next_operations: tuple[str, ...]

    @property
    def ordinary_successor_allowed(self) -> bool:
        return (
            self.state == "verified"
            and "normal_successor" in self.permitted_next_operations
        )


_PUBLICATION_LOCK = threading.Lock()


def _timestamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime):
        raise PublicationExecutionError("publication clock did not return a datetime")
    if value.tzinfo is None:
        raise PublicationExecutionError("publication clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _source_digest(source: Mapping[str, object]) -> str:
    return _sha256_bytes(canonical_json(source))


def _seal_record(
    *,
    archive: ImmutableArchive,
    repository: str,
    tag: str,
    candidate_commit: str,
    record_name: str,
    record: (
        ProviderResultRecord | VerificationRecord | ReconciliationRecord
        | ExternalPredecessorRecord
    ),
    source_name: str,
    source: Mapping[str, object],
    destination: Path,
) -> SealedRecordEvidence:
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sportsrank-publication-record-") as directory:
        work = Path(directory)
        record_path = work / record_name
        source_path = work / source_name
        record_path.write_bytes(canonical_json(record.to_dict()))
        source_path.write_bytes(canonical_json(source))
        references = archive.seal_or_reconcile(ArchiveSpec(
            repository, tag, candidate_commit,
            {record_name: record_path, source_name: source_path},
            f"SportsRank {record_name.removesuffix('.json')}",
        ))
        try:
            record_reference = references[record_name]
            source_reference = references[source_name]
        except KeyError as exc:
            raise PublicationExecutionError(
                "sealed publication record evidence is incomplete"
            ) from exc
        if record_reference.sha256 != record.digest:
            raise PublicationExecutionError(
                "sealed publication record digest differs from its logical record"
            )
        if source_reference.sha256 != _source_digest(source):
            raise PublicationExecutionError(
                "sealed provider source digest differs from its logical evidence"
            )
        retrieved_record = archive.retrieve_and_verify(
            record_reference, destination / record_name
        )
        retrieved_source = archive.retrieve_and_verify(
            source_reference, destination / source_name
        )
    return SealedRecordEvidence(
        record_reference, source_reference, retrieved_record, retrieved_source
    )


def _json_object(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationExecutionError(f"retrieved {name} is invalid") from exc
    if not isinstance(value, Mapping):
        raise PublicationExecutionError(f"retrieved {name} is not an object")
    return value


def _retrieve_recorded_attempt(
    recorded: RecordedPublicationAttempt,
    *,
    archive: ImmutableArchive,
    repository: str,
    destination: Path,
) -> FirebaseDeployArtifact:
    """Re-read the exact package and intent; local prior paths carry no trust."""

    attempt = recorded.attempt
    references = (
        attempt.package_reference,
        attempt.package_record_reference,
        attempt.validation_reference,
        attempt.intent_reference,
        *attempt.intent.evidence_references.values(),
    )
    if any(reference.repository != repository for reference in references):
        raise PublicationExecutionError(
            "attempt evidence must belong to the configured repository"
        )
    destination.mkdir(parents=True, exist_ok=True)
    package_path = archive.retrieve_and_verify(
        attempt.package_reference,
        destination / attempt.package_reference.asset_name,
    )
    package_record_path = archive.retrieve_and_verify(
        attempt.package_record_reference,
        destination / attempt.package_record_reference.asset_name,
    )
    validation_path = archive.retrieve_and_verify(
        attempt.validation_reference,
        destination / attempt.validation_reference.asset_name,
    )
    intent_path = archive.retrieve_and_verify(
        attempt.intent_reference,
        destination / attempt.intent_reference.asset_name,
    )
    for role, reference in attempt.intent.evidence_references.items():
        archive.retrieve_and_verify(
            reference, destination / f"{role}-{reference.asset_name}"
        )
    package = ValidatedPackageRecord.from_dict(
        _json_object(package_record_path, "validated package record")
    )
    intent = AttemptIntentRecord.from_dict(
        _json_object(intent_path, "attempt intent"), package=package
    )
    if package != attempt.package or intent != attempt.intent:
        raise PublicationExecutionError(
            "retrieved attempt records differ from the supplied identities"
        )
    if validation_path.read_bytes() != _validation_evidence(
        inventory_sha256=package.inventory_sha256,
        configuration_sha256=package.configuration_sha256,
        expected_baseline_sha256=package.expected_baseline_sha256,
        retained_inputs_sha256=package.retained_inputs_sha256,
    ):
        raise PublicationExecutionError(
            "retrieved validation evidence differs from the package"
        )
    return FirebaseDeployArtifact.from_archive(package_path, package)


def _record_evidence_is_valid(
    record: ProviderResultRecord | VerificationRecord | None,
    evidence: SealedRecordEvidence | None,
    *,
    archive: ImmutableArchive,
    repository: str,
    destination: Path,
) -> bool:
    if record is None or evidence is None:
        return False
    if (
        evidence.record_reference.repository != repository
        or evidence.source_reference.repository != repository
    ):
        return False
    if (
        evidence.record_reference.sha256 != record.digest
        or evidence.source_reference.sha256 != record.source_sha256
    ):
        return False
    try:
        retrieved_record = archive.retrieve_and_verify(
            evidence.record_reference,
            destination / evidence.record_reference.asset_name,
        )
        archive.retrieve_and_verify(
            evidence.source_reference,
            destination / evidence.source_reference.asset_name,
        )
    except ArchiveError:
        return False
    return retrieved_record.read_bytes() == canonical_json(record.to_dict())


def _evidence_json(
    archive: ImmutableArchive,
    reference: ArchiveReference,
    *,
    repository: str,
    destination: Path,
    name: str,
) -> Mapping[str, Any]:
    if reference.repository != repository:
        raise PublicationExecutionError(
            "predecessor evidence must belong to the configured repository"
        )
    try:
        retrieved = archive.retrieve_and_verify(reference, destination)
    except ArchiveError as exc:
        raise PublicationExecutionError(
            f"{name} immutable evidence is unavailable"
        ) from exc
    return _json_object(retrieved, name)


def _exact_source(
    source: Mapping[str, Any], expected: Mapping[str, Any], name: str
) -> None:
    if dict(source) != dict(expected):
        raise PublicationExecutionError(
            f"{name} source is not the allowlisted provider observation"
        )


def _verify_prior_publication(
    package: ValidatedPackageRecord,
    baseline: BaselineRecord,
    prior: VerifiedPublicationPredecessor | None,
    *,
    archive: ImmutableArchive,
    repository: str,
    destination: Path,
    require_prior: bool = False,
) -> None:
    if baseline.digest != package.expected_baseline_sha256:
        raise PublicationExecutionError(
            "baseline record does not match the validated package"
        )
    if (
        prior is None
        and not require_prior
        and baseline.observed == package.expected_predecessor
    ):
        # First publication: the complete stable baseline is authoritative even
        # when its historical source commit is explicitly unknown.
        return
    if prior is None:
        raise PublicationExecutionError(
            "a successor requires sealed verified predecessor evidence"
        )
    if getattr(prior, "repository", None) != repository:
        raise PublicationExecutionError(
            "predecessor is not a coordinator-issued repository capability"
        )
    if isinstance(prior, ExternalVerifiedPredecessor):
        if (
            prior.baseline_sha256 != baseline.digest
            or prior.observed_identity != package.expected_predecessor
            or prior.observation.baseline_sha256 != baseline.digest
            or prior.observation.consumed_archive_sha256
            != prior.capture_reference.sha256
            or prior.observation.archive_reference_sha256
            != prior.capture_reference.digest
            or prior.observation.sanitizer_reference_sha256
            != prior.sanitizer_reference.digest
            or prior.observation.inventory_sha256 != baseline.inventory_sha256
            or prior.observation.configuration_sha256
            != baseline.configuration_sha256
            or prior.observation.application_tree_sha256
            != baseline.application_tree_sha256
        ):
            raise PublicationExecutionError(
                "external predecessor does not match the validated package baseline"
            )
        for reference in (
            prior.capture_reference, prior.sanitizer_reference,
            prior.observation_reference, prior.observation_source_reference,
        ):
            if reference.repository != repository:
                raise PublicationExecutionError(
                    "external predecessor evidence must belong to the configured repository"
                )
        if len({
            (reference.release_id, reference.asset_id)
            for reference in (
                prior.capture_reference, prior.sanitizer_reference,
                prior.observation_reference,
                prior.observation_source_reference,
            )
        }) != 4:
            raise PublicationExecutionError(
                "external predecessor evidence roles must use distinct assets"
            )
        archive.retrieve_and_verify(
            prior.capture_reference, destination / "external-capture.tar.gz"
        )
        sanitizer_raw = _evidence_json(
            archive, prior.sanitizer_reference, repository=repository,
            destination=destination / "external-sanitizer.json",
            name="external sanitizer",
        )
        sanitizer = SanitizedBaselineArchiveRecord.from_dict(sanitizer_raw)
        if (
            sanitizer.digest != prior.sanitizer_reference.sha256
            or sanitizer.source_baseline_record_sha256 != baseline.digest
            or sanitizer.source_archive_sha256 != baseline.archive_sha256
            or sanitizer.derivative_archive_sha256
            != prior.capture_reference.sha256
        ):
            raise PublicationExecutionError(
                "external sanitizer does not reconstruct the predecessor baseline"
            )
        observation_raw = _evidence_json(
            archive, prior.observation_reference, repository=repository,
            destination=destination / "external-predecessor.json",
            name="external predecessor",
        )
        observation = ExternalPredecessorRecord.from_dict(observation_raw)
        if (
            observation != prior.observation
            or prior.observation_reference.sha256 != observation.digest
            or prior.observation_source_reference.sha256
            != observation.source_sha256
        ):
            raise PublicationExecutionError(
                "external predecessor record differs from its immutable evidence"
            )
        source = _evidence_json(
            archive, prior.observation_source_reference,
            repository=repository,
            destination=destination / "external-predecessor-source.json",
            name="external predecessor source",
        )
        _exact_source(source, {
            "schema_version": 1,
            "record_type": "firebase_external_predecessor_observation",
            "target": prior.observed_identity.target.to_dict(),
            "release": prior.observed_identity.release,
            "version": prior.observed_identity.version,
            "baseline_sha256": baseline.digest,
            "archive_reference_sha256": prior.capture_reference.digest,
            "sanitizer_reference_sha256": prior.sanitizer_reference.digest,
            "consumed_archive_sha256": prior.capture_reference.sha256,
            "inventory_sha256": baseline.inventory_sha256,
            "configuration_sha256": baseline.configuration_sha256,
            "application_tree_sha256": baseline.application_tree_sha256,
            "fresh_capture_correspondence": "verified",
        }, "external predecessor")
        return
    if not isinstance(prior, PriorVerifiedPublication):
        raise PublicationExecutionError(
            "predecessor requires coordinator-reconstructed evidence"
        )
    if prior.repository != repository:
        raise PublicationExecutionError(
            "predecessor belongs to another repository"
        )
    result = ProviderResultRecord.from_dict(
        prior.provider_result.to_dict(), intent=prior.intent
    )
    verification = VerificationRecord.from_dict(
        prior.verification.to_dict(), intent=prior.intent,
        provider_result=result,
    )
    if (
        result.outcome != "accepted"
        or verification.outcome != "verified"
        or result.redaction_method != "allowlisted-fields-v1"
        or verification.redaction_method != "allowlisted-findings-v1"
        or prior.reconciliation.redaction_method
        != "allowlisted-reconciliation-v1"
        or result.observed_target != package.expected_predecessor.target
        or result.observed_release != package.expected_predecessor.release
        or result.observed_version != package.expected_predecessor.version
        or verification.observed_release != package.expected_predecessor.release
        or verification.observed_version != package.expected_predecessor.version
        or prior.reconciliation.disposition != "candidate_verified"
        or prior.reconciliation.observed_target
        != package.expected_predecessor.target
        or prior.reconciliation.provider_result_sha256 != result.digest
        or prior.reconciliation.verification_sha256 != verification.digest
        or prior.reconciliation.observed_release != result.observed_release
        or prior.reconciliation.observed_version != result.observed_version
        or prior.provider_result_reference.sha256 != result.digest
        or prior.provider_result_source_reference.sha256 != result.source_sha256
        or prior.verification_reference.sha256 != verification.digest
        or prior.verification_source_reference.sha256
        != verification.source_sha256
        or prior.reconciliation_reference.sha256
        != prior.reconciliation.digest
        or prior.reconciliation_source_reference.sha256
        != prior.reconciliation.source_sha256
    ):
        raise PublicationExecutionError(
            "successor predecessor evidence is incomplete or does not match"
        )
    references = (
        prior.intent.artifact_reference,
        *prior.intent.evidence_references.values(),
        prior.provider_result_reference,
        prior.provider_result_source_reference,
        prior.verification_reference,
        prior.verification_source_reference,
        prior.reconciliation_reference,
        prior.reconciliation_source_reference,
    )
    if any(reference.repository != repository for reference in references):
        raise PublicationExecutionError(
            "predecessor evidence must belong to the configured repository"
        )
    record_references = references[-6:]
    if len({
        (reference.release_id, reference.asset_id)
        for reference in record_references
    }) != 6:
        raise PublicationExecutionError(
            "predecessor evidence roles must use distinct archive assets"
        )
    retrieved_result = _evidence_json(
        archive, prior.provider_result_reference, repository=repository,
        destination=destination / "prior-provider-result.json",
        name="provider result",
    )
    provider_source = _evidence_json(
        archive, prior.provider_result_source_reference, repository=repository,
        destination=destination / "prior-provider-result-source.json",
        name="provider result source",
    )
    retrieved_verification = _evidence_json(
        archive, prior.verification_reference, repository=repository,
        destination=destination / "prior-verification.json",
        name="verification",
    )
    verification_source = _evidence_json(
        archive, prior.verification_source_reference, repository=repository,
        destination=destination / "prior-verification-source.json",
        name="verification source",
    )
    retrieved_reconciliation = _evidence_json(
        archive, prior.reconciliation_reference, repository=repository,
        destination=destination / "prior-reconciliation.json",
        name="reconciliation",
    )
    reconciliation_source = _evidence_json(
        archive, prior.reconciliation_source_reference, repository=repository,
        destination=destination / "prior-reconciliation-source.json",
        name="reconciliation source",
    )
    if (
        ProviderResultRecord.from_dict(retrieved_result, intent=prior.intent)
        != result
        or VerificationRecord.from_dict(
            retrieved_verification, intent=prior.intent,
            provider_result=result,
        ) != verification
        or ReconciliationRecord.from_dict(
            retrieved_reconciliation, intent=prior.intent,
            provider_result=result, verification=verification,
        ) != prior.reconciliation
    ):
        raise PublicationExecutionError(
            "retrieved predecessor records differ from their immutable references"
        )
    _exact_source(provider_source, {
        "schema_version": 1,
        "record_type": "firebase_reconciled_provider_result",
        "outcome": "accepted",
        "release": result.observed_release,
        "version": result.observed_version,
        "artifact_sha256": prior.intent.artifact_reference.sha256,
        "content_correspondence": "verified",
        "prior_result_evidence": provider_source.get("prior_result_evidence"),
        "prior_verification_evidence": provider_source.get(
            "prior_verification_evidence"
        ),
    }, "provider result")
    if provider_source["prior_result_evidence"] not in {
        "archive_consistent_untrusted", "missing_or_invalid"
    } or provider_source["prior_verification_evidence"] not in {
        "archive_consistent_untrusted", "missing_or_invalid"
    }:
        raise PublicationExecutionError(
            "provider result source has an invalid prior-evidence status"
        )
    _exact_source(verification_source, {
        "schema_version": 1,
        "record_type": "firebase_verification_observation",
        "outcome": "verified",
        "release": verification.observed_release,
        "version": verification.observed_version,
        "inventory_sha256": verification.inventory_sha256,
        "configuration_sha256": verification.configuration_sha256,
        "managed_resource_findings": dict(
            verification.managed_resource_findings
        ),
        "public_page_findings": dict(verification.public_page_findings),
        "findings": list(verification.findings),
    }, "verification")
    _exact_source(reconciliation_source, {
        "schema_version": 1,
        "record_type": "firebase_reconciliation_observation",
        "disposition": "candidate_verified",
        "release": prior.reconciliation.observed_release,
        "version": prior.reconciliation.observed_version,
        "provider_result_sha256": result.digest,
        "verification_sha256": verification.digest,
        "findings": list(prior.reconciliation.findings),
    }, "reconciliation")


class PublicationCoordinator:
    """One process-serialized normal publication; external writers remain possible."""

    serialization_scope = (
        "process-only; the final provider read cannot exclude external publishers"
    )

    def __init__(
        self,
        *,
        provider: FirebasePublicationAdapter,
        provider_reader: FirebaseReadAdapter | None = None,
        archive: ImmutableArchive,
        repository: str,
        approval_reader: ApprovalReader,
        clock=lambda: datetime.now(timezone.utc),
        operation_lock: threading.Lock = _PUBLICATION_LOCK,
    ) -> None:
        self.provider = provider
        if (
            provider_reader is not None
            and provider_reader.target != provider.target
        ):
            raise PublicationExecutionError(
                "publication and read adapters must use the same provider target"
            )
        self.provider_reader = provider_reader
        self.archive = archive
        self.repository = repository
        self.approval_reader = approval_reader
        self.clock = clock
        self.operation_lock = operation_lock

    def publish_normal(
        self,
        package: ValidatedPackageRecord,
        *,
        prepared: PreparedPackage,
        commit_reader: CommitTreeReader,
        runtime: GitHubRuntimeContext,
        baseline: BaselineRecord,
        evidence_references: Mapping[str, ArchiveReference],
        tags: PublicationTags,
        attempt_id: str,
        retrieval_directory: str | Path,
        prior: VerifiedPublicationPredecessor | None = None,
    ) -> PublicationRun:
        return self._publish(
            package, purpose="normal", prepared=prepared,
            commit_reader=commit_reader, runtime=runtime, baseline=baseline,
            evidence_references=evidence_references, tags=tags,
            attempt_id=attempt_id, retrieval_directory=retrieval_directory,
            prior=prior,
        )

    def publish_recovery(
        self,
        package: ValidatedPackageRecord,
        *,
        purpose: str,
        prior: VerifiedPublicationPredecessor,
        prepared: PreparedPackage,
        commit_reader: CommitTreeReader,
        runtime: GitHubRuntimeContext,
        baseline: BaselineRecord,
        evidence_references: Mapping[str, ArchiveReference],
        tags: PublicationTags,
        attempt_id: str,
        retrieval_directory: str | Path,
    ) -> PublicationRun:
        """Publish a fresh approved rollback/correction; never reuse an attempt."""

        if purpose not in {"rollback", "correction"}:
            raise PublicationExecutionError(
                "recovery publication purpose must be rollback or correction"
            )
        if not isinstance(
            prior, (PriorVerifiedPublication, ExternalVerifiedPredecessor)
        ):
            raise PublicationExecutionError(
                "recovery publication requires a verified current predecessor"
            )
        if getattr(prior, "repository", None) != self.repository:
            raise PublicationExecutionError(
                "recovery predecessor is not a coordinator-issued repository capability"
            )
        if baseline.observed != package.expected_predecessor:
            raise PublicationExecutionError(
                "recovery package must be validated against a capture of the "
                "current predecessor"
            )
        return self._publish(
            package, purpose=purpose, prior=prior, prepared=prepared,
            commit_reader=commit_reader, runtime=runtime, baseline=baseline,
            evidence_references=evidence_references, tags=tags,
            attempt_id=attempt_id, retrieval_directory=retrieval_directory,
        )

    def _publish(
        self,
        package: ValidatedPackageRecord,
        *,
        purpose: str,
        prepared: PreparedPackage,
        commit_reader: CommitTreeReader,
        runtime: GitHubRuntimeContext,
        baseline: BaselineRecord,
        evidence_references: Mapping[str, ArchiveReference],
        tags: PublicationTags,
        attempt_id: str,
        retrieval_directory: str | Path,
        prior: VerifiedPublicationPredecessor | None = None,
    ) -> PublicationRun:
        """Publish once or return a truthful paused state; never retry or roll back."""

        with self.operation_lock:
            destination = Path(retrieval_directory)
            _verify_prior_publication(
                package, baseline, prior, archive=self.archive,
                repository=self.repository,
                destination=destination / "predecessor",
                require_prior=purpose != "normal",
            )
            authorization = authorize_protected_execution(
                package, runtime=runtime, github=self.approval_reader,
                expected_repository=self.repository,
            )
            attempt = seal_attempt_evidence(
                package,
                prepared=prepared,
                commit_reader=commit_reader,
                archive=self.archive,
                repository=self.repository,
                package_tag=tags.package,
                intent_tag=tags.intent,
                attempt_id=attempt_id,
                purpose=purpose,
                evidence_references=evidence_references,
                protected_context=authorization.context,
                retrieval_directory=destination / "attempt",
            )
            artifact = FirebaseDeployArtifact.from_archive(
                attempt.retrieved_package, package
            )
            # This is deliberately the last operation before the first provider
            # write. The process lock does not lock Firebase against outsiders.
            live = self.provider.observe()
            if live != package.expected_predecessor:
                raise PublicationExecutionError(
                    "live provider identity changed after approval and archive work"
                )

            try:
                receipt = self.provider.deploy(artifact, attempt_id=attempt_id)
            except ProviderRejectedError as exc:
                result, provider_source = self._failure_result(
                    attempt.intent, "rejected", type(exc).__name__
                )
                return self._finish_without_verification(
                    attempt, package, tags, destination, result, provider_source,
                    state="rejected", may_have_changed=False,
                    next_operations=("new_owner_approved_attempt",),
                )
            except ProviderWriteUncertain as exc:
                result, provider_source = self._failure_result(
                    attempt.intent, "unknown", type(exc).__name__
                )
                return self._finish_without_verification(
                    attempt, package, tags, destination, result, provider_source,
                    state="provider_unknown", may_have_changed=True,
                    next_operations=("reconcile",),
                )

            provider_source = dict(receipt.source)
            result = ProviderResultRecord.create(
                intent=attempt.intent,
                outcome="accepted",
                observed_target=receipt.identity.target.to_dict(),
                observed_release=receipt.identity.release,
                observed_version=receipt.identity.version,
                observed_at=_timestamp(self.clock),
                source_sha256=_source_digest(provider_source),
            )
            try:
                provider_evidence = _seal_record(
                    archive=self.archive, repository=self.repository,
                    tag=tags.provider_result,
                    candidate_commit=package.candidate_commit,
                    record_name="provider-result.json", record=result,
                    source_name="provider-result-source.json", source=provider_source,
                    destination=destination / "provider-result",
                )
            except (ArchiveError, PublicationExecutionError, PublicationPreparationError):
                return PublicationRun(
                    "provider_result_unsealed", attempt, result, None, None, None,
                    True, ("reconcile",),
                )

            observation = self.provider.verify(
                artifact, receipt.identity,
                expected_managed=baseline.managed_resources,
            )
            verification_source = dict(observation.source)
            verification = VerificationRecord.create(
                intent=attempt.intent,
                provider_result=result,
                outcome=observation.outcome,
                inventory_sha256=observation.inventory_sha256,
                configuration_sha256=observation.configuration_sha256,
                managed_resource_findings=observation.managed_findings,
                public_page_findings=observation.public_findings,
                findings=observation.findings,
                observed_at=_timestamp(self.clock),
                source_sha256=_source_digest(verification_source),
            )
            try:
                verification_evidence = _seal_record(
                    archive=self.archive, repository=self.repository,
                    tag=tags.verification,
                    candidate_commit=package.candidate_commit,
                    record_name="verification.json", record=verification,
                    source_name="verification-source.json",
                    source=verification_source,
                    destination=destination / "verification",
                )
            except (ArchiveError, PublicationExecutionError, PublicationPreparationError):
                return PublicationRun(
                    "verification_unsealed", attempt, result, provider_evidence,
                    verification, None, True, ("reconcile",),
                )
            state = {
                "verified": "verified",
                "failed": "verification_failed",
                "unknown": "verification_unknown",
            }[verification.outcome]
            next_operations = ("reconcile",)
            return PublicationRun(
                state, attempt, result, provider_evidence, verification,
                verification_evidence, True, next_operations,
            )

    def reconcile(
        self,
        recorded: RecordedPublicationAttempt,
        *,
        baseline: BaselineRecord,
        tags: ReconciliationTags,
        retrieval_directory: str | Path,
    ) -> ReconciliationRun:
        """Observe and append evidence for one interrupted publication attempt."""

        with self.operation_lock:
            destination = Path(retrieval_directory)
            artifact = _retrieve_recorded_attempt(
                recorded, archive=self.archive,
                repository=self.repository,
                destination=destination / "attempt",
            )
            package = recorded.attempt.package
            if baseline.digest != package.expected_baseline_sha256:
                raise PublicationExecutionError(
                    "reconciliation baseline does not match the attempt package"
                )
            result_evidence_valid = _record_evidence_is_valid(
                recorded.provider_result, recorded.provider_evidence,
                archive=self.archive,
                repository=self.repository,
                destination=destination / "prior-provider-result",
            )
            verification_evidence_valid = _record_evidence_is_valid(
                recorded.verification, recorded.verification_evidence,
                archive=self.archive,
                repository=self.repository,
                destination=destination / "prior-verification",
            )
            observed = self.provider.observe()
            if (
                observed == package.expected_predecessor
                and recorded.provider_result is None
                and recorded.verification is None
            ):
                disposition = "prewrite_interrupted"
                state = "prewrite_interrupted"
                findings = (
                    "live identity remains the sealed expected predecessor; "
                    "no candidate deployment is evidenced",
                )
                permitted = ("new_owner_approved_attempt",)
            elif observed != package.expected_predecessor:
                return self._reconcile_changed_live(
                    recorded, artifact, observed, baseline,
                    tags=tags, destination=destination,
                    result_evidence_valid=result_evidence_valid,
                    verification_evidence_valid=verification_evidence_valid,
                )
            else:
                disposition = "evidence_conflict"
                state = "reconciliation_required"
                findings = (
                    "live identity or retained records require content reconciliation",
                )
                permitted = ("reconcile",)
            source = {
                "schema_version": 1,
                "record_type": "firebase_reconciliation_observation",
                "disposition": disposition,
                "release": observed.release,
                "version": observed.version,
                "provider_result_sha256": None,
                "verification_sha256": None,
                "findings": list(findings),
            }
            observation = ReconciliationRecord.create(
                intent=recorded.attempt.intent,
                observed_identity=observed,
                disposition=disposition,
                findings=findings,
                observed_at=_timestamp(self.clock),
                source_sha256=_source_digest(source),
            )
            try:
                evidence = _seal_record(
                    archive=self.archive,
                    repository=self.repository,
                    tag=tags.observation,
                    candidate_commit=package.candidate_commit,
                    record_name="reconciliation.json",
                    record=observation,
                    source_name="reconciliation-source.json",
                    source=source,
                    destination=destination / "observation",
                )
            except (
                ArchiveError, PublicationExecutionError,
                PublicationPreparationError,
            ):
                return ReconciliationRun(
                    "reconciliation_unsealed", recorded.attempt.intent,
                    observed, observation, None,
                    None, None, None, None, ("reconcile",),
                )
            return ReconciliationRun(
                state, recorded.attempt.intent, observed, observation, evidence,
                None, None, None, None, permitted,
            )

    def _reconcile_changed_live(
        self,
        recorded: RecordedPublicationAttempt,
        artifact: FirebaseDeployArtifact,
        observed: ProviderIdentity,
        baseline: BaselineRecord,
        *,
        tags: ReconciliationTags,
        destination: Path,
        result_evidence_valid: bool,
        verification_evidence_valid: bool,
    ) -> ReconciliationRun:
        observation = self.provider.verify(
            artifact, observed, expected_managed=baseline.managed_resources
        )
        candidate_verified = observation.outcome == "verified"
        prior_claims_observed = (
            result_evidence_valid
            and recorded.provider_result is not None
            and recorded.provider_result.outcome == "accepted"
            and recorded.provider_result.observed_release == observed.release
            and recorded.provider_result.observed_version == observed.version
        )
        if candidate_verified:
            disposition = "candidate_verified"
            state = "reconciled_verified"
            permitted = ("normal_successor", "rollback", "correction")
            result_outcome = "accepted"
        elif prior_claims_observed:
            disposition = "candidate_unverified"
            state = "candidate_unverified"
            permitted = ("verification_only", "reconcile")
            result_outcome = recorded.provider_result.outcome
        else:
            disposition = "external_unverified"
            state = "external_unverified"
            permitted = ("capture_external", "reconcile")
            result_outcome = "unknown"

        provider_source = {
            "schema_version": 1,
            "record_type": "firebase_reconciled_provider_result",
            "outcome": result_outcome,
            "release": observed.release,
            "version": observed.version,
            "artifact_sha256": artifact.package.bundle_sha256,
            "content_correspondence": observation.outcome,
            "prior_result_evidence": (
                "archive_consistent_untrusted"
                if result_evidence_valid else "missing_or_invalid"
            ),
            "prior_verification_evidence": (
                "archive_consistent_untrusted"
                if verification_evidence_valid
                else "missing_or_invalid"
            ),
        }
        provider_result = ProviderResultRecord.create(
            intent=recorded.attempt.intent,
            outcome=result_outcome,
            observed_target=observed.target.to_dict(),
            observed_release=observed.release,
            observed_version=observed.version,
            observed_at=_timestamp(self.clock),
            source_sha256=_source_digest(provider_source),
        )
        verification_source = dict(observation.source)
        verification = VerificationRecord.create(
            intent=recorded.attempt.intent,
            provider_result=provider_result,
            outcome=observation.outcome,
            inventory_sha256=observation.inventory_sha256,
            configuration_sha256=observation.configuration_sha256,
            managed_resource_findings=observation.managed_findings,
            public_page_findings=observation.public_findings,
            findings=observation.findings,
            observed_at=_timestamp(self.clock),
            source_sha256=_source_digest(verification_source),
        )
        findings = (
            "live identity and candidate correspondence were observed directly",
            "prior provider-result evidence: "
            + (
                "archive_consistent_untrusted"
                if result_evidence_valid else "missing_or_invalid"
            ),
            "prior verification evidence: "
            + (
                "archive_consistent_untrusted"
                if verification_evidence_valid else "missing_or_invalid"
            ),
        )
        reconciliation_source = {
            "schema_version": 1,
            "record_type": "firebase_reconciliation_observation",
            "disposition": disposition,
            "release": observed.release,
            "version": observed.version,
            "provider_result_sha256": provider_result.digest,
            "verification_sha256": verification.digest,
            "findings": list(findings),
        }
        reconciliation = ReconciliationRecord.create(
            intent=recorded.attempt.intent,
            observed_identity=observed,
            disposition=disposition,
            provider_result=provider_result,
            verification=verification,
            observed_at=_timestamp(self.clock),
            findings=findings,
            source_sha256=_source_digest(reconciliation_source),
        )
        provider_evidence: SealedRecordEvidence | None = None
        verification_evidence: SealedRecordEvidence | None = None
        reconciliation_evidence: SealedRecordEvidence | None = None
        try:
            provider_evidence = _seal_record(
                archive=self.archive, repository=self.repository,
                tag=tags.provider_result,
                candidate_commit=artifact.package.candidate_commit,
                record_name="provider-result.json", record=provider_result,
                source_name="provider-result-source.json",
                source=provider_source,
                destination=destination / "provider-result",
            )
            verification_evidence = _seal_record(
                archive=self.archive, repository=self.repository,
                tag=tags.verification,
                candidate_commit=artifact.package.candidate_commit,
                record_name="verification.json", record=verification,
                source_name="verification-source.json",
                source=verification_source,
                destination=destination / "verification",
            )
            reconciliation_evidence = _seal_record(
                archive=self.archive, repository=self.repository,
                tag=tags.observation,
                candidate_commit=artifact.package.candidate_commit,
                record_name="reconciliation.json", record=reconciliation,
                source_name="reconciliation-source.json",
                source=reconciliation_source,
                destination=destination / "observation",
            )
        except (
            ArchiveError, PublicationExecutionError,
            PublicationPreparationError,
        ):
            return ReconciliationRun(
                "reconciliation_unsealed", recorded.attempt.intent,
                observed, reconciliation,
                reconciliation_evidence, provider_result, provider_evidence,
                verification, verification_evidence, ("reconcile",),
            )
        predecessor = None
        if state == "reconciled_verified":
            predecessor = PriorVerifiedPublication._create(
                self.repository,
                recorded.attempt.intent,
                provider_result,
                verification,
                reconciliation,
                provider_evidence.record_reference,
                provider_evidence.source_reference,
                verification_evidence.record_reference,
                verification_evidence.source_reference,
                reconciliation_evidence.record_reference,
                reconciliation_evidence.source_reference,
            )
        return ReconciliationRun(
            state, recorded.attempt.intent, observed, reconciliation,
            reconciliation_evidence,
            provider_result, provider_evidence,
            verification, verification_evidence, permitted, predecessor,
        )

    def reconcile_external(
        self,
        captured: VerifiedBaseline,
        *,
        archive_reference: ArchiveReference,
        sanitizer_reference: ArchiveReference,
        observation_tag: str,
        retrieval_directory: str | Path,
    ) -> ExternalReconciliationRun:
        """Bind an unknown live deployment to a complete immutable capture.

        Provider identity alone is deliberately insufficient. The capture must
        still validate, its exact consumed archive must already be sealed, and
        the live identity must still equal the capture after archive retrieval.
        """

        with self.operation_lock:
            captured.assert_current()
            if (
                archive_reference.repository != self.repository
                or sanitizer_reference.repository != self.repository
            ):
                raise PublicationExecutionError(
                    "external evidence must belong to the configured repository"
                )
            if captured.sanitizer_record is None:
                raise PublicationExecutionError(
                    "external reconciliation requires the sanitized public "
                    "capture derivative"
                )
            expected_archive_sha = (
                captured.sanitizer_record.derivative_archive_sha256
            )
            if archive_reference.sha256 != expected_archive_sha:
                raise PublicationExecutionError(
                    "external capture reference does not bind the consumed archive"
                )
            destination = Path(retrieval_directory)
            retrieved = self.archive.retrieve_and_verify(
                archive_reference,
                destination / archive_reference.asset_name,
            )
            retrieved_sanitizer = self.archive.retrieve_and_verify(
                sanitizer_reference,
                destination / sanitizer_reference.asset_name,
            )
            sanitizer = SanitizedBaselineArchiveRecord.from_dict(
                _json_object(retrieved_sanitizer, "sanitizer record")
            )
            if (
                sanitizer != captured.sanitizer_record
                or sanitizer_reference.sha256 != sanitizer.digest
                or sanitizer_reference.repository != archive_reference.repository
                or sanitizer_reference.target_commit
                != archive_reference.target_commit
            ):
                raise PublicationExecutionError(
                    "external sanitizer evidence does not bind the capture"
                )
            consumed_sha = _sha256_file(retrieved)
            if consumed_sha != expected_archive_sha:
                raise PublicationExecutionError(
                    "retrieved external capture differs from the verified baseline"
                )
            if self.provider_reader is None:
                raise PublicationExecutionError(
                    "external reconciliation requires a configured provider reader"
                )
            try:
                fresh = self.provider_reader.capture(
                    destination / "fresh-provider-capture"
                )
            except BaselineValidationError as exc:
                raise PublicationExecutionError(
                    "fresh complete provider capture is unavailable"
                ) from exc
            content_fields = (
                "target", "observed", "before", "after",
                "inventory_sha256", "configuration_sha256",
                "application_tree_sha256", "file_count", "total_bytes",
                "source", "managed_resources",
            )
            if any(
                getattr(fresh.record, name) != getattr(captured.record, name)
                for name in content_fields
            ):
                raise PublicationExecutionError(
                    "fresh provider capture differs from the archived external baseline"
                )
            live = self.provider.observe()
            if live != fresh.record.observed or live != captured.record.observed:
                raise PublicationExecutionError(
                    "live provider identity changed after fresh external capture"
                )
            source = {
                "schema_version": 1,
                "record_type": "firebase_external_predecessor_observation",
                "target": live.target.to_dict(),
                "release": live.release,
                "version": live.version,
                "baseline_sha256": captured.record.digest,
                "archive_reference_sha256": archive_reference.digest,
                "sanitizer_reference_sha256": sanitizer_reference.digest,
                "consumed_archive_sha256": consumed_sha,
                "inventory_sha256": captured.record.inventory_sha256,
                "configuration_sha256": captured.record.configuration_sha256,
                "application_tree_sha256": (
                    captured.record.application_tree_sha256
                ),
                "fresh_capture_correspondence": "verified",
            }
            observation = ExternalPredecessorRecord.create(
                baseline=captured.record,
                archive_reference=archive_reference,
                sanitizer_reference=sanitizer_reference,
                consumed_archive_sha256=consumed_sha,
                observed_at=_timestamp(self.clock),
                source_sha256=_source_digest(source),
            )
            try:
                evidence = _seal_record(
                    archive=self.archive,
                    repository=self.repository,
                    tag=observation_tag,
                    candidate_commit=archive_reference.target_commit,
                    record_name="external-predecessor.json",
                    record=observation,
                    source_name="external-predecessor-source.json",
                    source=source,
                    destination=destination / "observation",
                )
            except (
                ArchiveError, PublicationExecutionError,
                PublicationPreparationError,
            ):
                return ExternalReconciliationRun(
                    "external_reconciliation_unsealed", captured, live,
                    observation, None, ("reconcile_external",),
                )
            predecessor = ExternalVerifiedPredecessor._create(
                self.repository,
                captured.record.digest,
                live,
                observation,
                archive_reference,
                sanitizer_reference,
                evidence.record_reference,
                evidence.source_reference,
            )
            return ExternalReconciliationRun(
                "external_verified", captured, live, observation, evidence,
                ("normal_successor", "rollback", "correction"), predecessor,
            )

    def verify_only(
        self,
        recorded: RecordedPublicationAttempt,
        *,
        baseline: BaselineRecord,
        verification_tag: str,
        retrieval_directory: str | Path,
    ) -> PublicationRun:
        """Append verification for one accepted deployment without deploying."""

        with self.operation_lock:
            destination = Path(retrieval_directory)
            artifact = _retrieve_recorded_attempt(
                recorded, archive=self.archive,
                repository=self.repository,
                destination=destination / "attempt",
            )
            result = recorded.provider_result
            if (
                baseline.digest != artifact.package.expected_baseline_sha256
                or result is None
                or result.outcome != "accepted"
                or not _record_evidence_is_valid(
                    result, recorded.provider_evidence,
                    archive=self.archive,
                    repository=self.repository,
                    destination=destination / "provider-result",
                )
            ):
                raise PublicationExecutionError(
                    "verification-only requires a sealed accepted provider result"
                )
            identity = ProviderIdentity(
                result.observed_target,
                result.observed_release,  # type: ignore[arg-type]
                result.observed_version,  # type: ignore[arg-type]
            )
            if self.provider.observe() != identity:
                raise PublicationExecutionError(
                    "verification-only live identity differs from the accepted result"
                )
            observation = self.provider.verify(
                artifact, identity,
                expected_managed=baseline.managed_resources,
            )
            source = dict(observation.source)
            verification = VerificationRecord.create(
                intent=recorded.attempt.intent,
                provider_result=result,
                outcome=observation.outcome,
                inventory_sha256=observation.inventory_sha256,
                configuration_sha256=observation.configuration_sha256,
                managed_resource_findings=observation.managed_findings,
                public_page_findings=observation.public_findings,
                findings=observation.findings,
                observed_at=_timestamp(self.clock),
                source_sha256=_source_digest(source),
            )
            try:
                evidence = _seal_record(
                    archive=self.archive, repository=self.repository,
                    tag=verification_tag,
                    candidate_commit=artifact.package.candidate_commit,
                    record_name="verification.json", record=verification,
                    source_name="verification-source.json", source=source,
                    destination=destination / "verification",
                )
            except (
                ArchiveError, PublicationExecutionError,
                PublicationPreparationError,
            ):
                return PublicationRun(
                    "verification_unsealed", recorded.attempt, result,
                    recorded.provider_evidence, verification, None,
                    True, ("reconcile",),
                )
            state = {
                "verified": "verified",
                "failed": "verification_failed",
                "unknown": "verification_unknown",
            }[verification.outcome]
            permitted = (
                ("reconcile",) if state == "verified"
                else ("verification_only", "reconcile")
            )
            return PublicationRun(
                state, recorded.attempt, result, recorded.provider_evidence,
                verification, evidence, True, permitted,
            )

    def _failure_result(
        self, intent: AttemptIntentRecord, outcome: str, failure: str
    ) -> tuple[ProviderResultRecord, Mapping[str, object]]:
        try:
            observed = self.provider.observe()
        except FirebasePublicationError:
            observed = None
        source: dict[str, object] = {
            "schema_version": 1,
            "record_type": "firebase_deployment_observation",
            "outcome": outcome,
            "failure": failure,
            "release": observed.release if observed is not None else None,
            "version": observed.version if observed is not None else None,
        }
        result = ProviderResultRecord.create(
            intent=intent,
            outcome=outcome,
            observed_target=intent.expected_predecessor.target.to_dict(),
            observed_release=observed.release if observed is not None else None,
            observed_version=observed.version if observed is not None else None,
            observed_at=_timestamp(self.clock),
            source_sha256=_source_digest(source),
        )
        return result, source

    def _finish_without_verification(
        self,
        attempt: SealedAttempt,
        package: ValidatedPackageRecord,
        tags: PublicationTags,
        destination: Path,
        result: ProviderResultRecord,
        source: Mapping[str, object],
        *,
        state: str,
        may_have_changed: bool,
        next_operations: tuple[str, ...],
    ) -> PublicationRun:
        try:
            evidence = _seal_record(
                archive=self.archive, repository=self.repository,
                tag=tags.provider_result,
                candidate_commit=package.candidate_commit,
                record_name="provider-result.json", record=result,
                source_name="provider-result-source.json", source=source,
                destination=destination / "provider-result",
            )
        except (ArchiveError, PublicationExecutionError, PublicationPreparationError):
            return PublicationRun(
                "provider_result_unsealed", attempt, result, None, None, None,
                may_have_changed, ("reconcile",),
            )
        return PublicationRun(
            state, attempt, result, evidence, None, None,
            may_have_changed, next_operations,
        )
