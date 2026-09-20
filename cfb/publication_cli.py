"""Portable entry points for the protected SportsRank publication path.

The command line surface is intentionally thin.  It loads untrusted JSON and
path hints, revalidates the immutable records and package bytes, then calls
the coordinator in :mod:`cfb.publication`:

``prepare``
    Revalidate a staged candidate against an imported complete baseline and
    retained Schema 3 inputs, bind the exact immutable Git commit, and write
    a portable preparation context plus package.
``seal-only``
    Authenticate and retain one complete immutable attempt intent without
    reading or writing Firebase, then emit the exact canonical sealed reference
    for a later dispatch.  Known predecessors are freshly reconciled before
    sealing when a successor, rollback, or correction is requested.
``execute``
    Consume one exact sealed reference under a fresh owner-approved dispatch.
    The reference rehydrates the package and all retained evidence; no local
    package, manifest, bundle, or Actions state is an authority.  Rollback and
    correction require a freshly reconciled predecessor reference.
``reconcile``
    Read one sealed attempt from its exact reference and append a fresh provider
    observation through the coordinator; it never deploys.
``verify-only``
    Recheck one sealed attempt from its exact reference without invoking a
    provider write.
``reconcile-external``
    Bind an unknown live deployment to a complete, freshly captured provider
    baseline through the configured reader.

The production target and repository are constants.  Credentials are read
only from process environment by the concrete adapters and never from command
line arguments or persisted summaries.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

from .baseline import (
    BaselineValidationError,
    VerifiedBaseline,
    create_sanitized_baseline_archive,
    import_baseline,
    import_sanitized_baseline,
)
from .firebase import (
    FirebasePublicationAdapter,
    FirebasePublicationError,
    FirebaseReadAdapter,
    FirebaseRestPublicationBackend,
    FirebaseRestReadBackend,
)
from .github_archive import (
    ArchiveError,
    GitHubReleaseArchive,
)
from .publication import (
    CommitTreeReader,
    GitCommitTreeReader,
    PreparedPackage,
    PublicationCoordinator,
    PublicationExecutionError,
    PublicationTags,
    ReconciliationTags,
    SealedAttempt,
    SealedRecordEvidence,
    bind_merged_candidate,
    prepare_review_package,
    rehydrate_prepared_package,
)
from .publication_authorization import (
    GitHubApprovalReader,
    GitHubPreparationProvenanceReader,
    GitHubRuntimeContext,
    PREPARATION_MANIFEST_NAME,
    PreparationManifest,
    PreparationProvenanceError,
    PublicationAuthorizationError,
)
from .publication_records import (
    ArchiveReference,
    AttemptIntentRecord,
    BaselineRecord,
    ProviderTarget,
    ProviderResultRecord,
    RecordValidationError,
    SealedAttemptReference,
    ValidatedPackageRecord,
    VerificationRecord,
    canonical_json,
)
from .recovery_inputs import RecoveryInputBundle
from .release import ReleaseValidationError


REPOSITORY = "arkar16/sportsrank"
TARGET = ProviderTarget("sportsrank-837af", "sportsrank-837af", "live")
WORKFLOW_PATH = ".github/workflows/firebase-hosting-publish.yml"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_POSITIVE_ID = re.compile(r"[1-9][0-9]*\Z")
_CONTEXT_FIELDS = {
    "schema_version",
    "record_type",
    "repository",
    "target",
    "candidate_commit",
    "package",
    "baseline",
    "package_archive",
    "candidate_tree_archive",
    "candidate_tree_sha256",
    "evidence_references",
    "baseline_public_archive",
    "baseline_public_archive_sha256",
    "baseline_sanitizer_record",
    "baseline_sanitizer_record_sha256",
}
_LEGACY_CONTEXT_FIELDS = _CONTEXT_FIELDS - {
    "baseline_public_archive",
    "baseline_public_archive_sha256",
    "baseline_sanitizer_record",
    "baseline_sanitizer_record_sha256",
}
class PublicationCLIError(RuntimeError):
    """A caller input or portable publication context failed closed."""


@dataclass(frozen=True)
class PreparationContext:
    """Strict, path-safe context transported from preparation to execution."""

    path: Path
    package: ValidatedPackageRecord
    baseline: BaselineRecord | None
    package_archive: Path
    candidate_tree_archive: Path
    candidate_tree_sha256: str
    evidence_references: Mapping[str, ArchiveReference]
    baseline_public_archive: Path | None = None
    baseline_public_archive_sha256: str | None = None
    baseline_sanitizer_record: Path | None = None
    baseline_sanitizer_record_sha256: str | None = None


@dataclass(frozen=True)
class TrustedRecoveryInputs:
    """Reviewed SR7 input identities loaded from the exact candidate tree.

    The downloaded input artifact remains transport only.  Its bytes are
    accepted only when they match this tracked manifest; no digest or byte
    count from the downloaded artifact becomes an authority.
    """

    path: Path
    baseline_private_archive_sha256: str
    baseline_record_sha256: str
    baseline_public_archive_sha256: str
    baseline_sanitizer_record_sha256: str
    source_archive_sha256: str
    source_manifest_sha256: str
    source_file_sha256: Mapping[str, str]
    source_file_bytes: Mapping[str, int]
    source_snapshot_checksum: Mapping[str, str]
    original_prepared_archive_sha256: str
    evidence_archive_sha256: Mapping[str, str]


@dataclass(frozen=True)
class _RehydratedPackage:
    prepared: PreparedPackage
    reader: CommitTreeReader
    temporary: tempfile.TemporaryDirectory[str]

    def close(self) -> None:
        self.temporary.cleanup()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise PublicationCLIError(f"cannot read {path.name}") from exc
    return digest.hexdigest()


def _json_value(path: str | Path, name: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationCLIError(f"{name} is unreadable") from exc


def _json_object(path: str | Path, name: str) -> Mapping[str, Any]:
    value = _json_value(path, name)
    if not isinstance(value, Mapping):
        raise PublicationCLIError(f"{name} must be a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise PublicationCLIError(f"refusing to overwrite sealed output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _require_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PublicationCLIError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_commit(value: Any, name: str) -> str:
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise PublicationCLIError(f"{name} must be a lowercase 40-character commit SHA")
    return value


def _load_trusted_recovery_inputs(path: str | Path) -> TrustedRecoveryInputs:
    """Load the reviewed SR7 input identities from the candidate source tree.

    This file is intentionally separate from the retained-input artifact.  A
    caller may choose the artifact run and filename, but cannot choose the
    archive, raw snapshot, or public baseline identities accepted by prepare.
    The workflow supplies this path from the attested, immutable execution
    source; callers cannot override it through dispatch text.
    """

    manifest_path = Path(path).resolve()
    raw = _json_object(manifest_path, "trusted recovery-input manifest")
    if set(raw) != {
        "schema_version", "record_type", "target", "baseline",
        "source_inputs", "original_prepared", "evidence_archives",
    }:
        raise PublicationCLIError(
            "trusted recovery-input manifest fields do not match its schema"
        )
    if raw.get("schema_version") != 1 or raw.get("record_type") != "sr7_recovery_input_trust":
        raise PublicationCLIError("trusted recovery-input manifest schema is unsupported")
    try:
        if ProviderTarget.from_value(raw["target"]) != TARGET:
            raise PublicationCLIError("trusted recovery-input target is not SportsRank live")
        baseline = raw["baseline"]
        if not isinstance(baseline, Mapping) or set(baseline) != {
            "private_archive_sha256", "record_sha256", "public_archive_sha256",
            "sanitizer_record_sha256",
        }:
            raise PublicationCLIError("trusted baseline identities are incomplete")
        baseline_private = _require_sha(
            baseline["private_archive_sha256"], "trusted baseline archive"
        )
        baseline_record = _require_sha(
            baseline["record_sha256"], "trusted baseline record"
        )
        baseline_public = _require_sha(
            baseline["public_archive_sha256"], "trusted public baseline archive"
        )
        baseline_sanitizer = _require_sha(
            baseline["sanitizer_record_sha256"], "trusted baseline sanitizer record"
        )

        source = raw["source_inputs"]
        if not isinstance(source, Mapping) or set(source) != {
            "archive_sha256", "manifest_sha256", "files"
        }:
            raise PublicationCLIError("trusted source-input identities are incomplete")
        source_archive = _require_sha(
            source["archive_sha256"], "trusted source-input archive"
        )
        source_manifest = _require_sha(
            source["manifest_sha256"], "trusted source-input manifest"
        )
        files = source["files"]
        if not isinstance(files, Mapping) or not files:
            raise PublicationCLIError("trusted source-input file identities are empty")
        file_sha256: dict[str, str] = {}
        file_bytes: dict[str, int] = {}
        file_checksums: dict[str, str] = {}
        for relative, value in files.items():
            safe = _safe_relative(relative, "trusted source-input path")
            if safe != relative or not isinstance(value, Mapping) or set(value) != {
                "sha256", "bytes", "snapshot_checksum"
            }:
                raise PublicationCLIError(
                    f"trusted source-input identity is invalid: {relative}"
                )
            file_sha256[safe] = _require_sha(
                value["sha256"], f"trusted source-input digest {safe}"
            )
            byte_count = value["bytes"]
            if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
                raise PublicationCLIError(
                    f"trusted source-input byte count is invalid: {safe}"
                )
            file_bytes[safe] = byte_count
            file_checksums[safe] = _require_sha(
                value["snapshot_checksum"],
                f"trusted source-input snapshot checksum {safe}",
            )

        original = raw["original_prepared"]
        if not isinstance(original, Mapping) or set(original) != {"archive_sha256"}:
            raise PublicationCLIError("trusted original-prepared identity is incomplete")
        original_prepared = _require_sha(
            original["archive_sha256"], "trusted original-prepared archive"
        )

        evidence = raw["evidence_archives"]
        required_roles = {"baseline", "source_inputs", "original_prepared"}
        if not isinstance(evidence, Mapping) or set(evidence) != required_roles:
            raise PublicationCLIError("trusted evidence archive roles are incomplete")
        evidence_sha256 = {
            str(role): _require_sha(value, f"trusted evidence archive {role}")
            for role, value in evidence.items()
        }
    except PublicationCLIError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise PublicationCLIError("trusted recovery-input manifest is invalid") from exc

    if evidence_sha256 != {
        "baseline": baseline_private,
        "source_inputs": source_archive,
        "original_prepared": original_prepared,
    }:
        raise PublicationCLIError(
            "trusted evidence archive roles do not match retained input identities"
        )
    return TrustedRecoveryInputs(
        manifest_path,
        baseline_private,
        baseline_record,
        baseline_public,
        baseline_sanitizer,
        source_archive,
        source_manifest,
        file_sha256,
        file_bytes,
        file_checksums,
        original_prepared,
        evidence_sha256,
    )


def _assert_trusted_source_bundle(
    source_inputs: RecoveryInputBundle, trusted: TrustedRecoveryInputs
) -> None:
    """Compare every strict source identity with the tracked trust manifest."""

    if (
        source_inputs.manifest_sha256 != trusted.source_manifest_sha256
        or source_inputs.bundle_sha256 != trusted.source_archive_sha256
        or len(source_inputs.identities) != len(trusted.source_file_sha256)
    ):
        raise PublicationCLIError("source input bundle does not match the trusted manifest")
    for identity in source_inputs.identities:
        relative = identity.relative_path
        if (
            trusted.source_file_sha256.get(relative) != identity.source_file_sha256
            or trusted.source_file_bytes.get(relative) != identity.source_file_bytes
            or trusted.source_snapshot_checksum.get(relative)
            != identity.source_snapshot_checksum
        ):
            raise PublicationCLIError(
                f"source input identity is not the reviewed SR7 input: {relative}"
            )


def _safe_relative(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise PublicationCLIError(f"{name} must be a relative path")
    if "\\" in value or "\x00" in value:
        raise PublicationCLIError(f"{name} must be a safe relative path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise PublicationCLIError(f"{name} must be a safe relative path")
    return path.as_posix()


def _under(root: Path, relative: str, name: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PublicationCLIError(f"{name} escapes its context directory") from exc
    return path


def _context_sibling(context: Path, name: str) -> Path:
    """Resolve a sealed context companion without following it outside its bundle."""

    root = context.parent.resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise PublicationCLIError(f"{name} escapes its context directory")
    return path


def _verify_candidate_bundle(path: Path, candidate_commit: str) -> None:
    """Require a local Git bundle that advertises the exact candidate commit."""

    try:
        subprocess.run(
            ["git", "bundle", "verify", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        heads = subprocess.run(
            ["git", "bundle", "list-heads", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.decode("utf-8")
    except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as exc:
        raise PublicationCLIError("candidate Git bundle is not a valid immutable transport") from exc
    if not any(
        line.split(maxsplit=1)[0] == candidate_commit
        for line in heads.splitlines()
        if line.strip()
    ):
        raise PublicationCLIError("candidate Git bundle does not advertise its commit")


def _load_digest_pins(path: Path) -> Mapping[str, str]:
    raw = _json_object(path, "source input pins")
    result: dict[str, str] = {}
    for relative, digest in raw.items():
        safe = _safe_relative(relative, "source input pin path")
        result[safe] = _require_sha(digest, f"source input pin {safe}")
    if not result:
        raise PublicationCLIError("source input pins are empty")
    return result


def _parse_evidence_references(
    raw: Mapping[str, Any],
    *,
    expected_sha256: Mapping[str, str] | None = None,
) -> Mapping[str, ArchiveReference]:
    required = {"baseline", "source_inputs", "original_prepared"}
    if set(raw) != required:
        raise PublicationCLIError("immutable evidence references must contain the three required roles")
    result = {
        str(role): _archive_reference(value, f"evidence {role}")
        for role, value in raw.items()
    }
    if len({(value.release_id, value.asset_id) for value in result.values()}) != len(result):
        raise PublicationCLIError("immutable evidence roles must identify distinct assets")
    if expected_sha256 is not None:
        for role, reference in result.items():
            if reference.sha256 != expected_sha256[role]:
                raise PublicationCLIError(
                    f"evidence {role} does not match the reviewed retained archive"
                )
    return result


def _load_evidence_references(
    path: Path, *, expected_sha256: Mapping[str, str] | None = None
) -> Mapping[str, ArchiveReference]:
    return _parse_evidence_references(
        _json_object(path, "immutable evidence references"),
        expected_sha256=expected_sha256,
    )


def _load_context(
    path: str | Path,
    *,
    trusted_manifest: str | Path | None = None,
) -> PreparationContext:
    context_path = Path(path).resolve()
    raw = _json_object(context_path, "publication context")
    trusted = (
        _load_trusted_recovery_inputs(trusted_manifest)
        if trusted_manifest is not None else None
    )
    expected_fields = _CONTEXT_FIELDS if trusted is not None else _LEGACY_CONTEXT_FIELDS
    if set(raw) != expected_fields:
        raise PublicationCLIError("publication context fields do not match its schema")
    if raw.get("schema_version") != 1 or raw.get("record_type") != "publication_preparation":
        raise PublicationCLIError("publication context schema is unsupported")
    if raw.get("repository") != REPOSITORY:
        raise PublicationCLIError("publication context repository is not SportsRank")
    try:
        target = ProviderTarget.from_value(raw["target"])
        package = ValidatedPackageRecord.from_dict(raw["package"])
        baseline = BaselineRecord.from_dict(raw["baseline"])
        raw_refs = raw["evidence_references"]
        if not isinstance(raw_refs, Mapping):
            raise PublicationCLIError("publication context evidence references are invalid")
        refs = _parse_evidence_references(
            raw_refs,
            expected_sha256=trusted.evidence_archive_sha256 if trusted else None,
        )
    except PublicationCLIError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise PublicationCLIError("publication context contains an invalid record") from exc
    if target != TARGET or package.expected_predecessor.target != TARGET or baseline.target != TARGET:
        raise PublicationCLIError("publication context targets another Firebase site")
    candidate = _require_commit(raw.get("candidate_commit"), "candidate_commit")
    if package.candidate_commit != candidate:
        raise PublicationCLIError("publication context candidate does not match its package")
    if package.expected_baseline_sha256 != baseline.digest:
        raise PublicationCLIError("publication context baseline does not match its package")
    if trusted is not None:
        if baseline.digest != trusted.baseline_record_sha256:
            raise PublicationCLIError(
                "publication context baseline is not the reviewed SR7 baseline"
            )
        if package.retained_inputs_sha256 != trusted.source_archive_sha256:
            raise PublicationCLIError(
                "publication context retained inputs are not the reviewed SR7 bundle"
            )
    package_record_path = _context_sibling(context_path, "package.json")
    if not package_record_path.is_file():
        raise PublicationCLIError("validated package record is missing")
    try:
        stored_package = ValidatedPackageRecord.from_dict(
            _json_object(package_record_path, "validated package record")
        )
    except (TypeError, ValueError) as exc:
        raise PublicationCLIError("validated package record is invalid") from exc
    if (
        _sha256_file(package_record_path) != package.digest
        or stored_package != package
    ):
        raise PublicationCLIError("validated package record does not match its context")
    baseline_record_path = _context_sibling(context_path, "baseline.json")
    if not baseline_record_path.is_file():
        raise PublicationCLIError("baseline record is missing")
    try:
        stored_baseline = BaselineRecord.from_dict(
            _json_object(baseline_record_path, "baseline record")
        )
    except (TypeError, ValueError) as exc:
        raise PublicationCLIError("baseline record is invalid") from exc
    if _sha256_file(baseline_record_path) != baseline.digest or stored_baseline != baseline:
        raise PublicationCLIError("baseline record does not match its context")
    if trusted is not None and _sha256_file(baseline_record_path) != trusted.baseline_record_sha256:
        raise PublicationCLIError("baseline record is not the reviewed SR7 baseline")

    baseline_public_archive: Path | None = None
    baseline_public_archive_sha256: str | None = None
    baseline_sanitizer_record: Path | None = None
    baseline_sanitizer_record_sha256: str | None = None
    if trusted is not None:
        baseline_public_name = _safe_relative(
            raw.get("baseline_public_archive"), "baseline_public_archive"
        )
        baseline_public_archive = _under(
            context_path.parent, baseline_public_name, "baseline_public_archive"
        )
        if not baseline_public_archive.is_file():
            raise PublicationCLIError("public baseline derivative is missing")
        baseline_public_archive_sha256 = _require_sha(
            raw.get("baseline_public_archive_sha256"),
            "baseline_public_archive_sha256",
        )
        if baseline_public_archive_sha256 != trusted.baseline_public_archive_sha256:
            raise PublicationCLIError(
                "public baseline derivative digest is not reviewed"
            )
        if _sha256_file(baseline_public_archive) != baseline_public_archive_sha256:
            raise PublicationCLIError("public baseline derivative digest does not match")
        baseline_sanitizer_name = _safe_relative(
            raw.get("baseline_sanitizer_record"), "baseline_sanitizer_record"
        )
        baseline_sanitizer_record = _under(
            context_path.parent, baseline_sanitizer_name, "baseline_sanitizer_record"
        )
        if not baseline_sanitizer_record.is_file():
            raise PublicationCLIError("baseline sanitizer record is missing")
        baseline_sanitizer_record_sha256 = _require_sha(
            raw.get("baseline_sanitizer_record_sha256"),
            "baseline_sanitizer_record_sha256",
        )
        if baseline_sanitizer_record_sha256 != trusted.baseline_sanitizer_record_sha256:
            raise PublicationCLIError("baseline sanitizer record digest is not reviewed")
        if _sha256_file(baseline_sanitizer_record) != baseline_sanitizer_record_sha256:
            raise PublicationCLIError("baseline sanitizer record digest does not match")
        try:
            public_baseline = import_sanitized_baseline(
                baseline_public_archive,
                expected_derivative_sha256=baseline_public_archive_sha256,
                sanitizer_record=_json_object(
                    baseline_sanitizer_record, "baseline sanitizer record"
                ),
                expected_sanitizer_record_sha256=baseline_sanitizer_record_sha256,
                baseline_record=baseline,
                expected_baseline_record_sha256=trusted.baseline_record_sha256,
                target=TARGET,
            )
        except (BaselineValidationError, OSError, ValueError) as exc:
            raise PublicationCLIError(
                "public baseline derivative cannot reconstruct the reviewed baseline"
            ) from exc
        else:
            public_baseline.close()
    preparation_manifest_path = _context_sibling(
        context_path, PREPARATION_MANIFEST_NAME
    )
    if not preparation_manifest_path.is_file():
        raise PublicationCLIError("preparation provenance manifest is missing")
    try:
        preparation_manifest = PreparationManifest.from_bytes(
            preparation_manifest_path.read_bytes()
        )
    except (OSError, PreparationProvenanceError) as exc:
        raise PublicationCLIError("preparation provenance manifest is invalid") from exc
    if (
        preparation_manifest.repository != REPOSITORY
        or preparation_manifest.workflow_path != WORKFLOW_PATH
        or preparation_manifest.event != "workflow_dispatch"
        or preparation_manifest.ref != "refs/heads/main"
        or preparation_manifest.head_sha != candidate
        or preparation_manifest.package_archive_sha256 != package.bundle_sha256
        or preparation_manifest.package_record_sha256 != package.digest
    ):
        raise PublicationCLIError(
            "preparation provenance manifest does not bind its context"
        )
    attestation_path = _context_sibling(context_path, "publication-attestation.json")
    if not attestation_path.is_file():
        raise PublicationCLIError("publication attestation is missing")
    attestation = _json_object(attestation_path, "publication attestation")
    if set(attestation) != {
        "schema_version", "record_type", "repository", "target", "candidate_commit",
        "package_record_sha256", "bundle_sha256", "baseline_record_sha256",
        "expected_predecessor",
    }:
        raise PublicationCLIError("publication attestation fields do not match its schema")
    if (
        attestation.get("schema_version") != 1
        or attestation.get("record_type") != "publication_package_attestation"
        or attestation.get("repository") != REPOSITORY
        or attestation.get("target") != TARGET.to_dict()
        or attestation.get("candidate_commit") != candidate
        or attestation.get("package_record_sha256") != package.digest
        or attestation.get("bundle_sha256") != package.bundle_sha256
        or attestation.get("baseline_record_sha256") != baseline.digest
        or attestation.get("expected_predecessor") != package.expected_predecessor.to_dict()
    ):
        raise PublicationCLIError("publication attestation does not bind its package")
    if set(refs) != {"baseline", "source_inputs", "original_prepared"}:
        raise PublicationCLIError("publication context evidence roles are incomplete")
    if any(reference.repository != REPOSITORY for reference in refs.values()):
        raise PublicationCLIError("publication context evidence belongs to another repository")
    if len({(reference.release_id, reference.asset_id) for reference in refs.values()}) != len(refs):
        raise PublicationCLIError("publication context evidence roles must identify distinct assets")
    package_archive_name = _safe_relative(raw.get("package_archive"), "package_archive")
    package_archive = _under(context_path.parent, package_archive_name, "package_archive")
    if not package_archive.is_file():
        raise PublicationCLIError("prepared package archive is missing")
    if _sha256_file(package_archive) != package.bundle_sha256:
        raise PublicationCLIError("prepared package archive does not match its record")
    candidate_tree_name = _safe_relative(
        raw.get("candidate_tree_archive"), "candidate_tree_archive"
    )
    candidate_tree_archive = _under(
        context_path.parent, candidate_tree_name, "candidate_tree_archive"
    )
    if not candidate_tree_archive.is_file():
        raise PublicationCLIError("candidate Git bundle is missing")
    candidate_tree_sha256 = _require_sha(
        raw.get("candidate_tree_sha256"), "candidate_tree_sha256"
    )
    if _sha256_file(candidate_tree_archive) != candidate_tree_sha256:
        raise PublicationCLIError("candidate Git bundle does not match its digest")
    _verify_candidate_bundle(candidate_tree_archive, candidate)
    return PreparationContext(
        context_path,
        package,
        baseline,
        package_archive,
        candidate_tree_archive,
        candidate_tree_sha256,
        refs,
        baseline_public_archive,
        baseline_public_archive_sha256,
        baseline_sanitizer_record,
        baseline_sanitizer_record_sha256,
    )


def load_preparation_context(path: str | Path) -> PreparationContext:
    """Load and strictly revalidate a preparation context at the CLI seam."""

    return _load_context(path)


def _context_dict(
    *,
    package: ValidatedPackageRecord,
    baseline: BaselineRecord,
    package_archive: Path,
    candidate_tree_archive: Path,
    candidate_tree_sha256: str,
    evidence_references: Mapping[str, ArchiveReference],
    output_root: Path,
    baseline_public_archive: Path | None = None,
    baseline_public_archive_sha256: str | None = None,
    baseline_sanitizer_record: Path | None = None,
    baseline_sanitizer_record_sha256: str | None = None,
) -> dict[str, Any]:
    relative_archive = package_archive.resolve().relative_to(output_root.resolve()).as_posix()
    context = {
        "schema_version": 1,
        "record_type": "publication_preparation",
        "repository": REPOSITORY,
        "target": TARGET.to_dict(),
        "candidate_commit": package.candidate_commit,
        "package": package.to_dict(),
        "baseline": baseline.to_dict(),
        "package_archive": relative_archive,
        "candidate_tree_archive": candidate_tree_archive.resolve().relative_to(
            output_root.resolve()
        ).as_posix(),
        "candidate_tree_sha256": candidate_tree_sha256,
        "evidence_references": {
            role: reference.to_dict()
            for role, reference in evidence_references.items()
        },
    }
    public_values = (
        baseline_public_archive,
        baseline_public_archive_sha256,
        baseline_sanitizer_record,
        baseline_sanitizer_record_sha256,
    )
    if any(value is not None for value in public_values):
        if any(value is None for value in public_values):
            raise PublicationCLIError(
                "public baseline derivative fields must be supplied together"
            )
        assert baseline_public_archive is not None
        assert baseline_public_archive_sha256 is not None
        assert baseline_sanitizer_record is not None
        assert baseline_sanitizer_record_sha256 is not None
        context.update(
            {
                "baseline_public_archive": baseline_public_archive.resolve()
                .relative_to(output_root.resolve())
                .as_posix(),
                "baseline_public_archive_sha256": baseline_public_archive_sha256,
                "baseline_sanitizer_record": baseline_sanitizer_record.resolve()
                .relative_to(output_root.resolve())
                .as_posix(),
                "baseline_sanitizer_record_sha256": baseline_sanitizer_record_sha256,
            }
        )
    return context


def _attestation_dict(
    *, package: ValidatedPackageRecord, baseline: BaselineRecord
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "publication_package_attestation",
        "repository": REPOSITORY,
        "target": TARGET.to_dict(),
        "candidate_commit": package.candidate_commit,
        "package_record_sha256": package.digest,
        "bundle_sha256": package.bundle_sha256,
        "baseline_record_sha256": baseline.digest,
        "expected_predecessor": package.expected_predecessor.to_dict(),
    }


def _candidate_commit(explicit: str | None) -> str:
    runtime = os.environ.get("GITHUB_SHA")
    value = explicit if explicit is not None else runtime
    if value is None:
        raise PublicationCLIError("candidate commit must come from GITHUB_SHA or an exact local hint")
    candidate = _require_commit(value, "candidate commit")
    if runtime is not None and candidate != _require_commit(runtime, "GITHUB_SHA"):
        raise PublicationCLIError("candidate commit must match the protected GITHUB_SHA")
    return candidate


def prepare_operation(args: argparse.Namespace) -> Path:
    candidate_root = Path(args.candidate_root).resolve()
    firebase_json = Path(args.firebase_json).resolve()
    output = Path(args.output_directory).resolve()
    if not candidate_root.is_dir() or not firebase_json.is_file():
        raise PublicationCLIError("candidate root and firebase.json must exist")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise PublicationCLIError("preparation output directory must be new and empty")
    baseline_archive = Path(args.baseline_archive).resolve()
    source_root = Path(args.source_input_root).resolve()
    candidate_commit = _candidate_commit(args.candidate_commit)
    trusted = _load_trusted_recovery_inputs(args.trusted_input_manifest)
    candidate_tree_archive = Path(args.candidate_tree_bundle).resolve()
    if not candidate_tree_archive.is_file():
        raise PublicationCLIError("candidate Git bundle is missing")
    candidate_tree_sha256 = _require_sha(
        args.candidate_tree_sha256, "candidate_tree_sha256"
    )
    if _sha256_file(candidate_tree_archive) != candidate_tree_sha256:
        raise PublicationCLIError("candidate Git bundle does not match its digest")
    _verify_candidate_bundle(candidate_tree_archive, candidate_commit)
    source_archive = (
        Path(args.source_input_archive).resolve()
        if args.source_input_archive is not None else None
    )
    evidence = _load_evidence_references(
        Path(args.evidence_references).resolve(),
        expected_sha256=trusted.evidence_archive_sha256,
    )
    retained_sha = _require_sha(args.retained_inputs_sha256, "retained_inputs_sha256")
    if retained_sha != trusted.source_archive_sha256:
        raise PublicationCLIError(
            "retained input digest is not the reviewed SR7 source bundle"
        )
    baseline_sha = _require_sha(args.baseline_sha256, "baseline_sha256")
    if baseline_sha != trusted.baseline_private_archive_sha256:
        raise PublicationCLIError(
            "baseline archive digest is not the reviewed SR7 baseline"
        )
    if source_archive is None or args.source_input_sha256 is None:
        raise PublicationCLIError(
            "trusted preparation requires the complete source-input archive and digest"
        )
    source_sha = _require_sha(args.source_input_sha256, "source_input_sha256")
    if source_sha != trusted.source_archive_sha256:
        raise PublicationCLIError(
            "source input archive digest is not the reviewed SR7 bundle"
        )
    pins = _load_digest_pins(Path(args.source_input_pins).resolve())
    if pins != dict(trusted.source_file_sha256):
        raise PublicationCLIError(
            "source input pins must match the tracked SR7 trust manifest"
        )

    try:
        baseline = import_baseline(
            baseline_archive,
            target=TARGET,
            expected_archive_sha256=trusted.baseline_private_archive_sha256,
        )
        source_inputs = RecoveryInputBundle.from_directory(
            source_root,
            trusted_file_sha256=pins,
            archive=source_archive,
            expected_archive_sha256=trusted.source_archive_sha256,
        )
        _assert_trusted_source_bundle(source_inputs, trusted)
        source_inputs.assert_external_to(candidate_root)
        package_archive = output / "package.tar.gz"
        candidate_site = (
            candidate_root / "website"
            if (candidate_root / "website").is_dir()
            else candidate_root
        )
        prepared = prepare_review_package(
            candidate_site,
            baseline=baseline,
            firebase_json=firebase_json,
            source_inputs=source_inputs,
            retained_inputs_sha256=retained_sha,
            output=package_archive,
        )
        reader = GitCommitTreeReader(candidate_root)
        reader.require_commit(candidate_commit)
        package = bind_merged_candidate(
            prepared, candidate_commit=candidate_commit, reader=reader
        )
        if baseline.record.digest != trusted.baseline_record_sha256:
            raise PublicationCLIError(
                "imported baseline record is not the reviewed SR7 baseline"
            )
        public_baseline_archive = output / "baseline-public.tar.gz"
        sanitizer = create_sanitized_baseline_archive(
            baseline, public_baseline_archive
        )
        if (
            sanitizer.source_baseline_record_sha256 != trusted.baseline_record_sha256
            or sanitizer.source_archive_sha256
            != trusted.baseline_private_archive_sha256
            or sanitizer.derivative_archive_sha256
            != trusted.baseline_public_archive_sha256
            or sanitizer.digest != trusted.baseline_sanitizer_record_sha256
        ):
            raise PublicationCLIError(
                "generated public baseline derivative is not the reviewed SR7 derivative"
            )
        # The preparation output must start empty so a caller cannot smuggle
        # an unreviewed transport file into the package.  Keep the candidate
        # bundle staged outside that directory until every input and package
        # check above has passed, then copy the exact verified bytes into the
        # self-contained preparation transport referenced by its context.
        transported_candidate_tree = output / "candidate.bundle"
        if candidate_tree_archive != transported_candidate_tree:
            shutil.copyfile(candidate_tree_archive, transported_candidate_tree)
        if _sha256_file(transported_candidate_tree) != candidate_tree_sha256:
            raise PublicationCLIError(
                "transported candidate Git bundle does not match its digest"
            )
        transported_candidate_digest = output / "candidate.bundle.sha256"
        if transported_candidate_digest.exists():
            raise PublicationCLIError("refusing to overwrite candidate bundle digest")
        transported_candidate_digest.write_text(
            f"{candidate_tree_sha256}  candidate.bundle\n", encoding="ascii"
        )
        public_sanitizer_record = output / "baseline-sanitizer.json"
        _write_json(output / "package.json", package.to_dict())
        _write_json(output / "baseline.json", baseline.record.to_dict())
        _write_json(public_sanitizer_record, sanitizer.to_dict())
        _write_json(
            output / "publication-context.json",
            _context_dict(
                package=package,
                baseline=baseline.record,
                package_archive=package_archive,
                candidate_tree_archive=transported_candidate_tree,
                candidate_tree_sha256=candidate_tree_sha256,
                evidence_references=evidence,
                output_root=output,
                baseline_public_archive=public_baseline_archive,
                baseline_public_archive_sha256=trusted.baseline_public_archive_sha256,
                baseline_sanitizer_record=public_sanitizer_record,
                baseline_sanitizer_record_sha256=trusted.baseline_sanitizer_record_sha256,
            ),
        )
        _write_json(output / "publication-attestation.json", _attestation_dict(
            package=package, baseline=baseline.record,
        ))
    except (BaselineValidationError, ValueError, OSError) as exc:
        raise PublicationCLIError("preparation failed closed") from exc
    finally:
        if "baseline" in locals() and isinstance(baseline, VerifiedBaseline):
            baseline.close()
    return output / "publication-context.json"


def _materialize_candidate_reader(
    bundle: Path, candidate_commit: str, root: Path
) -> GitCommitTreeReader:
    """Load the transported Git object graph into a read-only bare store."""

    repository = root / "candidate.git"
    try:
        subprocess.run(
            ["git", "init", "--bare", "--quiet", str(repository)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            [
                "git", "--git-dir", str(repository), "fetch", "--quiet",
                "--no-tags", str(bundle),
                f"{candidate_commit}:refs/heads/candidate",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PublicationCLIError(
            "candidate Git bundle could not materialize its immutable object graph"
        ) from exc
    reader = GitCommitTreeReader(repository)
    reader.require_commit(candidate_commit)
    return reader


def _rehydrate_package(context: PreparationContext) -> _RehydratedPackage:
    temporary = tempfile.TemporaryDirectory(prefix="sportsrank-package-")
    root = Path(temporary.name)
    try:
        if _sha256_file(context.candidate_tree_archive) != context.candidate_tree_sha256:
            raise PublicationCLIError("candidate Git bundle changed after context validation")
        _verify_candidate_bundle(
            context.candidate_tree_archive, context.package.candidate_commit
        )
        reader = _materialize_candidate_reader(
            context.candidate_tree_archive,
            context.package.candidate_commit,
            root,
        )
        prepared = rehydrate_prepared_package(
            package_record=_context_sibling(context.path, "package.json"),
            package_archive=context.package_archive,
            preparation_manifest=_context_sibling(
                context.path, PREPARATION_MANIFEST_NAME
            ),
            candidate_reader=reader,
            provenance_reader=GitHubPreparationProvenanceReader.from_github_token(),
            materialize_to=root / "prepared",
        )
        return _RehydratedPackage(
            prepared, reader, temporary
        )
    except (PublicationCLIError, PreparationProvenanceError):
        temporary.cleanup()
        raise
    except (FirebasePublicationError, OSError, ValueError) as exc:
        temporary.cleanup()
        raise PublicationCLIError("prepared package cannot be rehydrated") from exc


def build_coordinator() -> PublicationCoordinator:
    """Construct the concrete production adapters from gated environment state."""

    token = lambda: os.environ.get("FIREBASE_ACCESS_TOKEN", "")
    publication_backend = FirebaseRestPublicationBackend(token)
    read_backend = FirebaseRestReadBackend(token)
    provider = FirebasePublicationAdapter(TARGET, publication_backend)
    provider_reader = FirebaseReadAdapter(TARGET, read_backend)
    return PublicationCoordinator(
        provider=provider,
        provider_reader=provider_reader,
        archive=GitHubReleaseArchive(),
        repository=REPOSITORY,
        approval_reader=GitHubApprovalReader.from_github_token(),
    )


def _tag_values(prefix: str) -> PublicationTags:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", prefix):
        raise PublicationCLIError("attempt tag prefix contains unsafe characters")
    return PublicationTags(
        f"{prefix}-package",
        f"{prefix}-intent",
        f"{prefix}-provider-result",
        f"{prefix}-verification",
    )


def _reconciliation_tags(prefix: str) -> ReconciliationTags:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", prefix):
        raise PublicationCLIError("reconciliation tag prefix contains unsafe characters")
    return ReconciliationTags(
        f"{prefix}-observation",
        f"{prefix}-provider-result",
        f"{prefix}-verification",
    )


def _archive_reference(value: Any, name: str) -> ArchiveReference:
    try:
        reference = ArchiveReference.from_value(value)
    except (TypeError, ValueError) as exc:
        raise PublicationCLIError(f"{name} is not a valid immutable archive reference") from exc
    if reference.repository != REPOSITORY:
        raise PublicationCLIError(f"{name} belongs to another repository")
    return reference


def _evidence_dict(evidence: SealedRecordEvidence | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return {
        "record_reference": evidence.record_reference.to_dict(),
        "source_reference": evidence.source_reference.to_dict(),
    }


def _attempt_dict(attempt: SealedAttempt) -> dict[str, Any]:
    return {
        "package": attempt.package.to_dict(),
        "intent": attempt.intent.to_dict(),
        "package_reference": attempt.package_reference.to_dict(),
        "package_record_reference": attempt.package_record_reference.to_dict(),
        "validation_reference": attempt.validation_reference.to_dict(),
        "intent_reference": attempt.intent_reference.to_dict(),
        "evidence_references": {
            role: reference.to_dict()
            for role, reference in attempt.intent.evidence_references.items()
        },
    }


def _run_dict(
    *,
    operation: str,
    state: str,
    attempt: SealedAttempt,
    provider_result: ProviderResultRecord | None,
    provider_evidence: SealedRecordEvidence | None,
    verification: VerificationRecord | None,
    verification_evidence: SealedRecordEvidence | None,
    deployment_may_have_changed: bool,
    permitted_next_operations: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "publication_run",
        "operation": operation,
        "state": state,
        "deployment_may_have_changed": deployment_may_have_changed,
        "permitted_next_operations": list(permitted_next_operations),
        "attempt": _attempt_dict(attempt),
        "provider_result": provider_result.to_dict() if provider_result is not None else None,
        "provider_evidence": _evidence_dict(provider_evidence),
        "verification": verification.to_dict() if verification is not None else None,
        "verification_evidence": _evidence_dict(verification_evidence),
    }


def _result_path(args: argparse.Namespace, default_root: Path) -> Path:
    if args.result is None:
        return default_root / "publication-run.json"
    return Path(args.result).resolve()


def _runtime() -> GitHubRuntimeContext:
    try:
        return GitHubRuntimeContext.from_environment()
    except Exception as exc:  # pragma: no cover - concrete environment seam
        raise PublicationCLIError("GitHub protected runtime context is incomplete") from exc


def _load_sealed_reference(path: str | Path) -> SealedAttemptReference:
    """Load the exact canonical reference supplied to a later dispatch.

    The newline is part of the wire format.  Reading bytes here, instead of
    parsing text and re-serializing it, makes accidental whitespace or a
    substituted JSON object fail closed before any archive or provider work.
    """

    reference_path = Path(path).resolve()
    try:
        value = reference_path.read_bytes()
    except OSError as exc:
        raise PublicationCLIError("sealed attempt reference is unavailable") from exc
    try:
        reference = SealedAttemptReference.from_bytes(value)
    except (RecordValidationError, TypeError, ValueError) as exc:
        raise PublicationCLIError(
            "sealed attempt reference must be canonical JSON with one trailing newline"
        ) from exc
    if reference.intent_reference.repository != REPOSITORY:
        raise PublicationCLIError("sealed attempt reference belongs to another repository")
    return reference


def _load_baseline_record(path: str | Path) -> BaselineRecord:
    """Load the baseline record transported beside a sealed reference."""

    try:
        baseline = BaselineRecord.from_dict(
            _json_object(Path(path).resolve(), "baseline record")
        )
    except (RecordValidationError, TypeError, ValueError) as exc:
        raise PublicationCLIError("baseline record is invalid") from exc
    if baseline.target != TARGET:
        raise PublicationCLIError("baseline record targets another Firebase site")
    return baseline


def _reference_output_path(args: argparse.Namespace, default_root: Path) -> Path:
    path = getattr(args, "reference_output", None)
    if path is None:
        return default_root / "sealed-attempt-reference.json"
    return Path(path).resolve()


def _write_reference(path: Path, reference: SealedAttemptReference) -> Path:
    """Write only canonical reference bytes; never wrap them in a summary."""

    if path.exists():
        raise PublicationCLIError(f"refusing to overwrite sealed output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(reference.to_bytes())
    return path


def _fresh_prior_reference(
    reference: SealedAttemptReference,
    baseline: BaselineRecord,
    *,
    coordinator: PublicationCoordinator,
    provenance_reader: GitHubPreparationProvenanceReader,
    tag_prefix: str,
    destination: Path,
) -> Any:
    """Reconcile a predecessor from its exact durable reference.

    The returned capability is issued by the coordinator.  No local run
    manifest or Actions state is used to establish predecessor authority.
    """

    reconciliation = coordinator.reconcile_sealed_attempt(
        reference,
        baseline=baseline,
        provenance_reader=provenance_reader,
        tags=_reconciliation_tags(f"fresh-{tag_prefix}"),
        retrieval_directory=destination,
    )
    if not reconciliation.ordinary_successor_allowed:
        raise PublicationCLIError(
            "fresh predecessor reconciliation did not establish a verified capability"
        )
    try:
        return reconciliation.as_prior()
    except PublicationExecutionError as exc:
        raise PublicationCLIError(
            "fresh predecessor reconciliation did not establish a verified capability"
        ) from exc


def _reconciliation_evidence_dict(
    evidence: SealedRecordEvidence | None,
) -> dict[str, Any] | None:
    return _evidence_dict(evidence)


def _reconciliation_dict(
    *,
    operation: str,
    state: str,
    intent: AttemptIntentRecord,
    observed_identity: Any,
    observation: Any,
    observation_evidence: SealedRecordEvidence | None,
    provider_result: ProviderResultRecord | None,
    provider_evidence: SealedRecordEvidence | None,
    verification: VerificationRecord | None,
    verification_evidence: SealedRecordEvidence | None,
    permitted_next_operations: Sequence[str],
) -> dict[str, Any]:
    """Serialize a state-free reconciliation/verification receipt."""

    return {
        "schema_version": 1,
        "record_type": "reconciliation_run",
        "operation": operation,
        "state": state,
        "observed_identity": observed_identity.to_dict(),
        "observation": observation.to_dict(),
        "observation_evidence": _reconciliation_evidence_dict(observation_evidence),
        "intent": intent.to_dict(),
        "provider_result": provider_result.to_dict() if provider_result is not None else None,
        "provider_evidence": _reconciliation_evidence_dict(provider_evidence),
        "verification": verification.to_dict() if verification is not None else None,
        "verification_evidence": _reconciliation_evidence_dict(verification_evidence),
        "permitted_next_operations": list(permitted_next_operations),
    }


def _write_run(path: Path, run: Mapping[str, Any]) -> Path:
    _write_json(path, run)
    return path


def seal_only_operation(
    args: argparse.Namespace,
    *,
    coordinator: PublicationCoordinator | None = None,
) -> Path:
    """Seal an immutable intent and return its exact canonical reference.

    This operation deliberately stops before any Firebase adapter is observed;
    the later execute dispatch is the only operation allowed to consume the
    intent and perform a provider write.
    """

    context = _load_context(
        args.context, trusted_manifest=getattr(args, "trusted_input_manifest", None)
    )
    coordinator = build_coordinator() if coordinator is None else coordinator
    destination = Path(
        args.retrieval_directory or context.path.parent / "sealed-attempt"
    ).resolve()
    rehydrated = _rehydrate_package(context)
    try:
        provenance_reader = GitHubPreparationProvenanceReader.from_github_token()
        prior_reference = getattr(args, "prior_reference", None)
        prior_baseline_record = getattr(args, "prior_baseline_record", None)
        if (prior_reference is None) != (prior_baseline_record is None):
            raise PublicationCLIError(
                "prior reference and prior baseline record must be supplied together"
            )
        if args.purpose != "normal" and prior_reference is None:
            raise PublicationCLIError(
                f"{args.purpose} requires a freshly reconciled predecessor reference"
            )
        prior = None
        if prior_reference is not None:
            prior = _fresh_prior_reference(
                _load_sealed_reference(prior_reference),
                _load_baseline_record(prior_baseline_record),
                coordinator=coordinator,
                provenance_reader=provenance_reader,
                tag_prefix=args.attempt_id,
                destination=destination / "predecessor",
            )
        preparation_manifest = Path(
            args.preparation_manifest
            if args.preparation_manifest is not None
            else context.path.parent / PREPARATION_MANIFEST_NAME
        ).resolve()
        preparation_origin = Path(
            args.preparation_origin
            if args.preparation_origin is not None
            else context.path.parent / "preparation-origin.json"
        ).resolve()
        candidate_bundle = Path(
            args.candidate_bundle
            if args.candidate_bundle is not None
            else context.candidate_tree_archive
        ).resolve()
        for path, label in (
            (preparation_manifest, "preparation manifest"),
            (preparation_origin, "preparation origin"),
            (candidate_bundle, "candidate Git bundle"),
        ):
            if not path.is_file():
                raise PublicationCLIError(f"{label} is missing")
        reference = coordinator.seal_publication_attempt(
            context.package,
            purpose=args.purpose,
            prepared=rehydrated.prepared,
            commit_reader=rehydrated.reader,
            runtime=_runtime(),
            baseline=context.baseline,
            evidence_references=context.evidence_references,
            preparation_manifest=preparation_manifest,
            preparation_origin=preparation_origin,
            candidate_bundle=candidate_bundle,
            provenance_reader=provenance_reader,
            tags=_tag_values(args.attempt_id),
            attempt_id=args.attempt_id,
            retrieval_directory=destination,
            prior=prior,
        )
        return _write_reference(
            _reference_output_path(args, context.path.parent), reference
        )
    finally:
        rehydrated.close()


def execute_operation(
    args: argparse.Namespace,
    *,
    coordinator: PublicationCoordinator | None = None,
) -> Path:
    """Execute one exact sealed intent in a fresh protected dispatch."""

    reference = _load_sealed_reference(args.sealed_reference)
    baseline = _load_baseline_record(args.baseline_record)
    coordinator = build_coordinator() if coordinator is None else coordinator
    destination = Path(args.retrieval_directory or Path(args.baseline_record).parent / "receipts").resolve()
    provenance_reader = GitHubPreparationProvenanceReader.from_github_token()
    prior = None
    prior_reference = getattr(args, "prior_reference", None)
    prior_baseline_record = getattr(args, "prior_baseline_record", None)
    if (prior_reference is None) != (prior_baseline_record is None):
        raise PublicationCLIError(
            "prior reference and prior baseline record must be supplied together"
        )
    if args.purpose != "normal" and prior_reference is None:
        raise PublicationCLIError(
            f"{args.purpose} requires a freshly reconciled predecessor reference"
        )
    if prior_reference is not None:
        prior = _fresh_prior_reference(
            _load_sealed_reference(prior_reference),
            _load_baseline_record(prior_baseline_record),
            coordinator=coordinator,
            provenance_reader=provenance_reader,
            tag_prefix=args.attempt_id,
            destination=destination / "predecessor",
        )
    run = coordinator.execute_sealed_attempt(
        reference,
        purpose=args.purpose,
        runtime=_runtime(),
        baseline=baseline,
        provenance_reader=provenance_reader,
        tags=_tag_values(args.attempt_id),
        retrieval_directory=destination,
        prior=prior,
    )
    payload = _run_dict(
        operation="execute",
        state=run.state,
        attempt=run.attempt,
        provider_result=run.provider_result,
        provider_evidence=run.provider_evidence,
        verification=run.verification,
        verification_evidence=run.verification_evidence,
        deployment_may_have_changed=run.deployment_may_have_changed,
        permitted_next_operations=run.permitted_next_operations,
    )
    return _write_run(_result_path(args, Path(args.baseline_record).parent), payload)


def reconcile_operation(
    args: argparse.Namespace,
    *,
    coordinator: PublicationCoordinator | None = None,
) -> Path:
    """Reconcile one exact sealed intent without Actions-state dependency."""

    reference = _load_sealed_reference(args.sealed_reference)
    baseline = _load_baseline_record(args.baseline_record)
    coordinator = build_coordinator() if coordinator is None else coordinator
    destination = Path(args.retrieval_directory or Path(args.baseline_record).parent / "reconciliation").resolve()
    result = coordinator.reconcile_sealed_attempt(
        reference,
        baseline=baseline,
        provenance_reader=GitHubPreparationProvenanceReader.from_github_token(),
        tags=_reconciliation_tags(args.attempt_id),
        retrieval_directory=destination,
    )
    payload = _reconciliation_dict(
        operation="reconcile",
        state=result.state,
        intent=result.intent,
        observed_identity=result.observed_identity,
        observation=result.observation,
        observation_evidence=result.observation_evidence,
        provider_result=result.provider_result,
        provider_evidence=result.provider_evidence,
        verification=result.verification,
        verification_evidence=result.verification_evidence,
        permitted_next_operations=result.permitted_next_operations,
    )
    return _write_run(_result_path(args, Path(args.baseline_record).parent), payload)


def verify_only_operation(
    args: argparse.Namespace,
    *,
    coordinator: PublicationCoordinator | None = None,
) -> Path:
    """Verify one exact sealed intent with no provider deployment call."""

    reference = _load_sealed_reference(args.sealed_reference)
    baseline = _load_baseline_record(args.baseline_record)
    coordinator = build_coordinator() if coordinator is None else coordinator
    destination = Path(args.retrieval_directory or Path(args.baseline_record).parent / "verification-only").resolve()
    result = coordinator.verify_sealed_attempt(
        reference,
        baseline=baseline,
        provenance_reader=GitHubPreparationProvenanceReader.from_github_token(),
        tags=_reconciliation_tags(args.attempt_id),
        retrieval_directory=destination,
    )
    payload = _reconciliation_dict(
        operation="verify-only",
        state=result.state,
        intent=result.intent,
        observed_identity=result.observed_identity,
        observation=result.observation,
        observation_evidence=result.observation_evidence,
        provider_result=result.provider_result,
        provider_evidence=result.provider_evidence,
        verification=result.verification,
        verification_evidence=result.verification_evidence,
        permitted_next_operations=result.permitted_next_operations,
    )
    return _write_run(_result_path(args, Path(args.baseline_record).parent), payload)


def reconcile_external_operation(
    args: argparse.Namespace,
    *,
    coordinator: PublicationCoordinator | None = None,
) -> Path:
    coordinator = build_coordinator() if coordinator is None else coordinator
    context = _load_context(
        args.context, trusted_manifest=getattr(args, "trusted_input_manifest", None)
    )
    baseline_record = context.baseline
    derivative_archive = Path(args.derivative_archive).resolve()
    sanitizer_record = _json_object(args.sanitizer_record, "sanitizer record")
    try:
        captured = import_sanitized_baseline(
            derivative_archive,
            expected_derivative_sha256=_require_sha(args.derivative_sha256, "derivative_sha256"),
            sanitizer_record=sanitizer_record,
            expected_sanitizer_record_sha256=_require_sha(args.sanitizer_sha256, "sanitizer_sha256"),
            baseline_record=baseline_record,
            expected_baseline_record_sha256=baseline_record.digest,
            target=TARGET,
            source_archive=(
                Path(args.source_baseline_archive).resolve()
                if args.source_baseline_archive is not None else None
            ),
            materialize_to=Path(args.materialized_site).resolve() if args.materialized_site else None,
        )
        archive_reference = _archive_reference(_json_object(args.capture_reference, "capture archive reference"), "capture archive")
        sanitizer_reference = _archive_reference(_json_object(args.sanitizer_reference, "sanitizer archive reference"), "sanitizer archive")
        destination = Path(args.retrieval_directory or context.path.parent / "external-reconciliation").resolve()
        result = coordinator.reconcile_external(
            captured,
            archive_reference=archive_reference,
            sanitizer_reference=sanitizer_reference,
            observation_tag=f"{args.attempt_id}-external-observation",
            retrieval_directory=destination,
        )
        payload = {
            "schema_version": 1,
            "record_type": "external_reconciliation_run",
            "operation": "reconcile-external",
            "state": result.state,
            "observed_identity": result.observed_identity.to_dict(),
            "observation": result.observation.to_dict(),
            "observation_evidence": _evidence_dict(result.observation_evidence),
            "permitted_next_operations": list(result.permitted_next_operations),
        }
        return _write_run(_result_path(args, context.path.parent), payload)
    finally:
        captured.close() if "captured" in locals() and isinstance(captured, VerifiedBaseline) else None


def _common_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--context", type=Path, required=True, help="strict preparation context JSON")
    parser.add_argument(
        "--trusted-input-manifest",
        type=Path,
        help="tracked SR7 input identities from the attested execution source",
    )
    parser.add_argument("--attempt-id", required=True, help="new evidence identity, not authorization")
    parser.add_argument("--retrieval-directory", type=Path, help="local receipt directory")
    parser.add_argument("--result", type=Path, help="new immutable local result manifest")


def _exact_reference_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sealed-reference",
        type=Path,
        required=True,
        help="canonical SealedAttemptReference bytes from the seal-only dispatch",
    )
    parser.add_argument(
        "--baseline-record",
        type=Path,
        required=True,
        help="exact baseline.json transported with the preparation package",
    )
    parser.add_argument("--attempt-id", required=True, help="new evidence tag prefix")
    parser.add_argument("--retrieval-directory", type=Path, help="local receipt directory")
    parser.add_argument("--result", type=Path, help="new immutable local result manifest")


def _purpose_arg(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "--purpose",
        choices=("normal", "rollback", "correction"),
        required=required,
        default=None if required else "normal",
        help="sealed publication purpose; rollback and correction require a fresh predecessor",
    )


def _attempt_context_args(parser: argparse.ArgumentParser) -> None:
    # Kept as a named seam for callers that build compatible parsers.  The
    # shared context arguments make the protected workflow's invocation shape
    # identical for every operation; operation-specific requiredness is
    # enforced by the operation before any archive or provider access.
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    prepare = subparsers.add_parser("prepare", help="validate and bind one immutable candidate")
    prepare.add_argument("--candidate-root", type=Path, default=Path("."))
    prepare.add_argument("--candidate-commit", help="exact local hint; workflow derives GITHUB_SHA")
    prepare.add_argument("--candidate-tree-bundle", type=Path, required=True)
    prepare.add_argument("--candidate-tree-sha256", required=True)
    prepare.add_argument("--firebase-json", type=Path, default=Path("firebase.json"))
    prepare.add_argument("--baseline-archive", type=Path, required=True)
    prepare.add_argument("--baseline-sha256", required=True)
    prepare.add_argument("--source-input-root", type=Path, required=True)
    prepare.add_argument("--source-input-pins", type=Path, required=True)
    prepare.add_argument("--source-input-archive", type=Path)
    prepare.add_argument("--source-input-sha256")
    prepare.add_argument("--retained-inputs-sha256", required=True)
    prepare.add_argument("--evidence-references", type=Path, required=True)
    prepare.add_argument(
        "--trusted-input-manifest",
        type=Path,
        required=True,
        help="tracked SR7 input identities from the exact candidate checkout",
    )
    prepare.add_argument("--output-directory", type=Path, required=True)

    seal = subparsers.add_parser(
        "seal-only", help="seal one immutable intent without observing or writing Firebase"
    )
    seal.add_argument("--context", type=Path, required=True, help="strict preparation context JSON")
    seal.add_argument(
        "--trusted-input-manifest",
        type=Path,
        help="tracked SR7 input identities from the attested execution source",
    )
    seal.add_argument("--preparation-manifest", type=Path)
    seal.add_argument("--preparation-origin", type=Path)
    seal.add_argument("--candidate-bundle", type=Path)
    seal.add_argument("--attempt-id", required=True, help="new immutable intent identity")
    _purpose_arg(seal)
    seal.add_argument("--prior-reference", type=Path)
    seal.add_argument("--prior-baseline-record", type=Path)
    seal.add_argument("--retrieval-directory", type=Path, help="local receipt directory")
    seal.add_argument("--reference-output", type=Path, help="canonical reference output path")

    execute = subparsers.add_parser(
        "execute", help="execute one exact sealed intent under fresh owner approval"
    )
    _exact_reference_args(execute)
    _purpose_arg(execute)
    execute.add_argument("--prior-reference", type=Path)
    execute.add_argument("--prior-baseline-record", type=Path)

    reconcile = subparsers.add_parser("reconcile", help="freshly observe a sealed attempt")
    _exact_reference_args(reconcile)

    verify = subparsers.add_parser(
        "verify-only", help="verify a sealed attempt without deploying"
    )
    _exact_reference_args(verify)

    external = subparsers.add_parser("reconcile-external", help="freshly capture and bind an unknown live predecessor")
    _common_context(external)
    external.add_argument("--derivative-archive", type=Path, required=True)
    external.add_argument("--derivative-sha256", required=True)
    external.add_argument("--sanitizer-record", type=Path, required=True)
    external.add_argument("--sanitizer-sha256", required=True)
    external.add_argument("--capture-reference", type=Path, required=True)
    external.add_argument("--sanitizer-reference", type=Path, required=True)
    external.add_argument("--source-baseline-archive", type=Path)
    external.add_argument("--materialized-site", type=Path)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    coordinator: PublicationCoordinator | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "prepare":
            path = prepare_operation(args)
        elif args.operation == "seal-only":
            path = seal_only_operation(args, coordinator=coordinator)
        elif args.operation == "execute":
            path = execute_operation(args, coordinator=coordinator)
        elif args.operation == "reconcile":
            path = reconcile_operation(args, coordinator=coordinator)
        elif args.operation == "verify-only":
            path = verify_only_operation(args, coordinator=coordinator)
        elif args.operation == "reconcile-external":
            path = reconcile_external_operation(args, coordinator=coordinator)
        else:  # pragma: no cover - argparse constrains the operation
            raise PublicationCLIError("unsupported publication operation")
    except (
        ArchiveError,
        BaselineValidationError,
        FirebasePublicationError,
        OSError,
        PublicationCLIError,
        PublicationAuthorizationError,
        PublicationExecutionError,
        PreparationProvenanceError,
        ReleaseValidationError,
        ValueError,
    ) as exc:
        print(f"publication {args.operation} failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"operation": args.operation, "result": str(path.name)}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "REPOSITORY",
    "TARGET",
    "WORKFLOW_PATH",
    "PreparationContext",
    "PublicationCLIError",
    "build_coordinator",
    "build_parser",
    "execute_operation",
    "load_preparation_context",
    "main",
    "prepare_operation",
    "seal_only_operation",
    "reconcile_external_operation",
    "reconcile_operation",
    "verify_only_operation",
]
