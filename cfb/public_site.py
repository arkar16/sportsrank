"""Validate a private recovery candidate and create a safe public site.

The ordinary release validator intentionally works on the private staging tree:
it needs the complete Season Snapshot in order to recompute rankings and game
dispositions.  This module is the boundary after that validation.  It copies
the accepted site, replaces source-bearing snapshot documents with small
provenance records, and emits a receipt that lets a later hosted check repeat
the public-side assertions without access to the retained inputs.

No function in this module fetches from CFBD, changes Git, or mutates the
caller supplied candidate.  The output directory is required to be new (or
empty) so an export can be discarded and recreated without an implicit delete.
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
import stat
import sys
import tempfile
from typing import Any, Mapping, Sequence

try:
    from .baseline import VerifiedBaseline, import_baseline
    from .publication_records import BaselineRecord, ProviderIdentity, ProviderTarget
    from .release import Release, validate_release
    from .recovery_inputs import RecoveryInputBundle, RecoveryInputError
    from .public_safety import assert_public_bytes, has_source_payload as _deep_has_source_payload
except ImportError:  # pragma: no cover - direct execution compatibility
    from baseline import VerifiedBaseline, import_baseline
    from publication_records import BaselineRecord, ProviderIdentity, ProviderTarget
    from release import Release, validate_release
    from recovery_inputs import RecoveryInputBundle, RecoveryInputError
    from public_safety import assert_public_bytes, has_source_payload as _deep_has_source_payload


SCHEMA_VERSION = 1
RECORD_TYPE = "local_validation_receipt"
PUBLIC_MANIFEST_TYPE = "public_site_manifest"
PUBLIC_RELEASE_TYPE = "public_site_release"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_YEAR = re.compile(r"[0-9]{4}\Z")
_SNAPSHOT_PATH = re.compile(
    r"cfb/years/(?P<year>[0-9]{4})/data/snapshot\.json\Z"
)
_SNAPSHOT_ARCHIVE_PATH = re.compile(
    r"cfb/years/(?P<year>[0-9]{4})/data/snapshots/(?P<checksum>[0-9a-f]{64})\.json\Z"
)
_SAFE_RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,126}[A-Za-z0-9]\Z")
_SOURCE_FINGERPRINT_EXCLUDED = frozenset(
    {
        # The canonical reviewed receipt location is deliberately outside the
        # website but inside the tracked configuration area.  Excluding this
        # one name prevents a receipt from recursively changing its own code
        # fingerprint; arbitrary source-like files remain fingerprinted.
        "config/sr7-local-validation-receipt.json",
    }
)


class PublicSiteError(ValueError):
    """The private candidate, export, or receipt cannot establish its contract."""


class PublicSiteValidationError(PublicSiteError):
    """The copied public tree contains a forbidden or inconsistent artifact."""


class LocalValidationError(PublicSiteError):
    """Independent private validation or local receipt verification failed."""


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise PublicSiteError(f"unreadable file: {path.name}") from exc
    return digest.hexdigest()


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PublicSiteError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _safe_relative(value: Any, label: str = "relative path") -> str:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise PublicSiteError(f"{label} is unsafe")
    if "\\" in value or "\x00" in value:
        raise PublicSiteError(f"{label} is unsafe")
    pure = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise PublicSiteError(f"{label} is unsafe")
    return pure.as_posix()


def _resolved_directory(value: str | Path, label: str) -> Path:
    raw = Path(value)
    if raw.is_symlink():
        raise PublicSiteError(f"{label} cannot be a symlink")
    path = raw.resolve()
    if not path.is_dir():
        raise PublicSiteError(f"{label} must be an existing directory")
    return path


def _resolved_file(value: str | Path, label: str) -> Path:
    raw = Path(value)
    if raw.is_symlink():
        raise PublicSiteError(f"{label} cannot be a symlink")
    path = raw.resolve()
    if not path.is_file():
        raise PublicSiteError(f"{label} must be an existing file")
    return path


def _walk_files(root: Path, label: str) -> list[tuple[str, Path]]:
    """List regular files while rejecting links, unsafe names, and odd files."""

    root = _resolved_directory(root, label)
    result: list[tuple[str, Path]] = []
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        dirnames.sort()
        filenames.sort()
        for name in tuple(dirnames):
            child = directory_path / name
            if child.is_symlink():
                raise PublicSiteError(f"{label} contains a symlink")
        for name in filenames:
            child = directory_path / name
            if child.is_symlink():
                raise PublicSiteError(f"{label} contains a symlink")
            mode = child.stat().st_mode
            if not stat.S_ISREG(mode):
                raise PublicSiteError(f"{label} contains a non-regular file")
            relative = _safe_relative(child.relative_to(root).as_posix(), f"{label} path")
            result.append((relative, child))
    return result


def _inventory(root: Path, label: str = "site") -> tuple[dict[str, Any], ...]:
    files = _walk_files(root, label)
    return tuple(
        {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for relative, path in sorted(files)
    )


def _inventory_hash(inventory: Sequence[Mapping[str, Any]]) -> str:
    return _sha256_bytes(_canonical_bytes(list(inventory)))


def _publication_inventory_hash(inventory: Sequence[Mapping[str, Any]]) -> str:
    """Hash the inventory in the exact package-validation wire format."""

    return _sha256_bytes(
        _canonical_bytes(
            {
                "files": [
                    {
                        "path": f"website/{item['path']}",
                        "sha256": item["sha256"],
                        "size": item["bytes"],
                    }
                    for item in inventory
                ]
            }
        )
    )


def _package_validation_hash(
    *,
    inventory_sha256: str,
    configuration_sha256: str,
    expected_baseline_sha256: str,
    retained_inputs_sha256: str,
) -> str:
    return _sha256_bytes(
        _canonical_bytes(
            {
                "schema_version": 1,
                "record_type": "package_validation",
                "outcome": "valid",
                "inventory_sha256": inventory_sha256,
                "configuration_sha256": configuration_sha256,
                "expected_baseline_sha256": expected_baseline_sha256,
                "retained_inputs_sha256": retained_inputs_sha256,
            }
        )
    )


def _tree_digest(root: Path, label: str = "tree") -> str:
    """Hash relative names and per-file digests for a stable evidence identity."""

    inventory = _inventory(root, label)
    return _sha256_bytes(
        b"".join(
            relative.encode("utf-8") + b"\0" + str(item["sha256"]).encode("ascii") + b"\0"
            for relative, item in ((item["path"], item) for item in inventory)
        )
    )


def source_fingerprint(root: str | Path) -> str:
    """Return a deterministic digest of trusted executable/configuration files.

    The fingerprint deliberately excludes ``website/`` and receipts.  It
    covers the ranking/runtime code, helper tooling, protected workflows,
    recovery configuration, and the Python lock/configuration files that
    determine how those sources execute.
    """

    repository = _resolved_directory(root, "source root")
    selected: list[tuple[str, Path]] = []
    for directory_name in ("cfb", "tools", ".github", "config"):
        directory = repository / directory_name
        if not directory.exists():
            continue
        if directory.is_symlink():
            raise PublicSiteError(f"source fingerprint directory is a symlink: {directory_name}")
        for relative, path in _walk_files(directory, f"source fingerprint {directory_name}"):
            if relative.endswith((".pyc", ".pyo")) or "/__pycache__/" in f"/{relative}/":
                continue
            full_relative = f"{directory_name}/{relative}"
            if full_relative in _SOURCE_FINGERPRINT_EXCLUDED:
                continue
            selected.append((full_relative, path))
    for filename in ("pyproject.toml", "uv.lock", "firebase.json"):
        path = repository / filename
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise PublicSiteError(f"source fingerprint file is unsafe: {filename}")
            selected.append((filename, path))
    if not selected:
        raise PublicSiteError("source fingerprint has no trusted files")
    records = [
        {"path": relative, "bytes": path.stat().st_size, "sha256": _sha256_file(path)}
        for relative, path in sorted(selected)
    ]
    return _sha256_bytes(_canonical_bytes(records))


def _json_file(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicSiteError(f"{label} is not readable JSON") from exc


def _snapshot_relative_paths(site: Path) -> list[str]:
    paths: list[str] = []
    for relative, _path in _walk_files(site, "private candidate"):
        if _SNAPSHOT_PATH.fullmatch(relative) or _SNAPSHOT_ARCHIVE_PATH.fullmatch(relative):
            paths.append(relative)
    return sorted(paths)


def _trusted_manifest_details(
    trusted_input_manifest: str | Path | Mapping[str, Any],
) -> tuple[str, dict[str, str], str | None, Mapping[str, Any]]:
    """Read the tracked SR7 trust manifest or a raw bundle manifest.

    The return values are the supplied trust-document digest, trusted source
    file pins, expected source-input manifest digest, and parsed document.
    """

    if isinstance(trusted_input_manifest, Mapping):
        raw = dict(trusted_input_manifest)
        raw_bytes = _canonical_bytes(raw)
        trust_sha = _sha256_bytes(raw_bytes)
    else:
        path = _resolved_file(trusted_input_manifest, "trusted input manifest")
        try:
            raw_bytes = path.read_bytes()
            raw_value = json.loads(raw_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicSiteError("trusted input manifest is unreadable") from exc
        if not isinstance(raw_value, Mapping):
            raise PublicSiteError("trusted input manifest must be an object")
        raw = dict(raw_value)
        trust_sha = _sha256_bytes(raw_bytes)
    pins: dict[str, str] = {}
    expected_manifest_sha: str | None = None
    if raw.get("record_type") == "sr7_recovery_input_trust":
        source = raw.get("source_inputs")
        if not isinstance(source, Mapping) or not isinstance(source.get("files"), Mapping):
            raise PublicSiteError("trusted SR7 input manifest has no source file pins")
        expected_manifest_sha = source.get("manifest_sha256")
        if expected_manifest_sha is not None:
            _digest(expected_manifest_sha, "trusted source-input manifest digest")
        for relative, entry in source["files"].items():
            if not isinstance(entry, Mapping):
                raise PublicSiteError("trusted source-input file identity is invalid")
            safe = _safe_relative(relative, "trusted source-input path")
            if safe != relative:
                raise PublicSiteError("trusted source-input path is not canonical")
            pins[safe] = _digest(entry.get("sha256"), f"trusted source-input digest {safe}")
    elif isinstance(raw.get("files"), list):
        # A direct inputs-manifest.json is useful for small local fixture runs.
        expected_manifest_sha = trust_sha
        for entry in raw["files"]:
            if not isinstance(entry, Mapping):
                raise PublicSiteError("source-input manifest entry is invalid")
            relative = _safe_relative(entry.get("path"), "source-input path")
            pins[relative] = _digest(entry.get("sha256"), f"source-input digest {relative}")
    else:
        raise PublicSiteError("trusted input manifest schema is unsupported")
    if not pins:
        raise PublicSiteError("trusted input manifest has no source file pins")
    return trust_sha, pins, expected_manifest_sha, raw


def _source_bundle_manifest_sha(source_root: Path) -> str:
    path = source_root / "inputs-manifest.json"
    if path.is_symlink() or not path.is_file():
        raise PublicSiteError("source root must contain inputs-manifest.json")
    return _sha256_file(path)


def _trusted_bundle_sha(trust_raw: Mapping[str, Any]) -> str | None:
    source = trust_raw.get("source_inputs")
    if not isinstance(source, Mapping):
        return None
    value = source.get("archive_sha256")
    if value is None:
        return None
    return _digest(value, "trusted source-input archive digest")


def _trusted_evidence_hashes(trust_raw: Mapping[str, Any]) -> dict[str, str | None]:
    """Return optional reviewed evidence digests without exposing any paths."""

    result: dict[str, str | None] = {
        "original_prepared_archive_sha256": None,
        "baseline_private_archive_sha256": None,
        "baseline_record_sha256": None,
        "baseline_public_archive_sha256": None,
        "baseline_sanitizer_record_sha256": None,
    }
    original = trust_raw.get("original_prepared")
    if isinstance(original, Mapping) and original.get("archive_sha256") is not None:
        result["original_prepared_archive_sha256"] = _digest(
            original["archive_sha256"], "trusted original-prepared archive digest"
        )
    baseline = trust_raw.get("baseline")
    if isinstance(baseline, Mapping):
        names = {
            "private_archive_sha256": "baseline_private_archive_sha256",
            "record_sha256": "baseline_record_sha256",
            "public_archive_sha256": "baseline_public_archive_sha256",
            "sanitizer_record_sha256": "baseline_sanitizer_record_sha256",
        }
        for source_name, result_name in names.items():
            if baseline.get(source_name) is not None:
                result[result_name] = _digest(baseline[source_name], f"trusted {result_name}")
    return result


def _ensure_trusted_source_bundle(
    source_inputs: RecoveryInputBundle,
    source_root: str | Path | None,
    trusted_input_manifest: str | Path | Mapping[str, Any],
) -> tuple[Path, str, dict[str, str], str, Mapping[str, Any]]:
    source_inputs.assert_current()
    bundle_root = _resolved_directory(source_inputs.root, "source input root")
    requested_root = bundle_root if source_root is None else _resolved_directory(source_root, "source input root")
    if requested_root != bundle_root:
        raise PublicSiteError("source_root must identify the supplied RecoveryInputBundle")
    trust_sha, pins, expected_manifest_sha, trust_raw = _trusted_manifest_details(
        trusted_input_manifest
    )
    actual_manifest_sha = _source_bundle_manifest_sha(bundle_root)
    if expected_manifest_sha is not None and actual_manifest_sha != expected_manifest_sha:
        raise PublicSiteError("source-input manifest does not match trusted identity")
    if set(pins) != {identity.relative_path for identity in source_inputs.identities}:
        raise PublicSiteError("trusted source-input pins do not match the supplied bundle")
    trusted_source = trust_raw.get("source_inputs")
    trusted_files = trusted_source.get("files") if isinstance(trusted_source, Mapping) else None
    if isinstance(trusted_files, Mapping):
        for identity in source_inputs.identities:
            entry = trusted_files.get(identity.relative_path)
            if not isinstance(entry, Mapping):
                raise PublicSiteError("trusted source-input identity is missing")
            if entry.get("bytes") != identity.source_file_bytes:
                raise PublicSiteError("trusted source-input byte count disagrees with the bundle")
            expected_snapshot = entry.get("snapshot_checksum")
            if expected_snapshot is not None and expected_snapshot != identity.source_snapshot_checksum:
                raise PublicSiteError("trusted source-input snapshot checksum disagrees with the bundle")
    for identity in source_inputs.identities:
        if pins.get(identity.relative_path) != identity.source_file_sha256:
            raise PublicSiteError("trusted source-input pins disagree with the supplied bundle")
    return bundle_root, trust_sha, pins, actual_manifest_sha, trust_raw


def _baseline_info(baseline: VerifiedBaseline | str | Path) -> tuple[Path, dict[str, Any]]:
    if isinstance(baseline, VerifiedBaseline):
        baseline.assert_current()
        site = _resolved_directory(baseline.application_site, "baseline site")
        record = baseline.record
        return site, {
            "kind": "verified",
            "record_sha256": record.digest,
            "archive_sha256": record.archive_sha256,
            "application_tree_sha256": record.application_tree_sha256,
            "record": record.to_dict(),
            "identity": record.observed.to_dict(include_target=False),
            "public_archive_sha256": (
                baseline.sanitizer_record.derivative_archive_sha256
                if baseline.sanitizer_record is not None else None
            ),
            "sanitizer_record_sha256": (
                baseline.sanitizer_record.digest
                if baseline.sanitizer_record is not None else None
            ),
        }
    site = _resolved_directory(baseline, "baseline site")
    return site, {
        "kind": "path",
        "record_sha256": None,
        "archive_sha256": None,
        "application_tree_sha256": _tree_digest(site, "baseline site"),
        "record": None,
        "identity": None,
        "public_archive_sha256": None,
        "sanitizer_record_sha256": None,
    }


def _candidate_site(candidate: str | Path | Release) -> tuple[Path, Mapping[str, Any]]:
    if isinstance(candidate, Release):
        site = _resolved_directory(candidate.site, "private candidate")
    else:
        path = Path(candidate)
        site = _resolved_directory(path / "site" if (path / "site").is_dir() else path, "private candidate")
    manifest = site / "manifest.json"
    if not manifest.is_file() or manifest.is_symlink():
        raise PublicSiteError("private candidate must contain manifest.json")
    raw = _json_file(manifest, "private candidate manifest")
    if not isinstance(raw, Mapping):
        raise PublicSiteError("private candidate manifest must be an object")
    return site, raw


def _source_snapshot_records(
    private_site: Path,
    source_inputs: RecoveryInputBundle,
) -> list[tuple[str, bytes, dict[str, Any]]]:
    paths = _snapshot_relative_paths(private_site)
    if not paths:
        raise PublicSiteError("private candidate contains no Season Snapshot artifacts")
    result: list[tuple[str, bytes, dict[str, Any]]] = []
    for relative in paths:
        path = private_site / relative
        raw = path.read_bytes()
        value = _json_file(path, f"private snapshot {relative}")
        if not isinstance(value, Mapping):
            raise PublicSiteError(f"private snapshot {relative} must be an object")
        try:
            year = int(value["year"])
            classification = str(value["classification"]).upper()
            sport = str(value["sport"]).lower()
            snapshot_checksum = value["checksum"]
        except (KeyError, TypeError, ValueError) as exc:
            raise PublicSiteError(f"private snapshot {relative} has no safe identity") from exc
        if sport != "cfb" or not _YEAR.fullmatch(str(year)) or not _SHA256.fullmatch(str(snapshot_checksum)):
            raise PublicSiteError(f"private snapshot {relative} has an invalid identity")
        path_year = (_SNAPSHOT_PATH.fullmatch(relative) or _SNAPSHOT_ARCHIVE_PATH.fullmatch(relative)).group("year")
        if int(path_year) != year:
            raise PublicSiteError(f"private snapshot {relative} disagrees with its path")
        archive_match = _SNAPSHOT_ARCHIVE_PATH.fullmatch(relative)
        if archive_match is not None and archive_match.group("checksum") != snapshot_checksum:
            raise PublicSiteError(f"private snapshot archive {relative} is not content-addressed")
        identity = source_inputs.resolve(year, classification)
        migration = value.get("metadata", {}).get("migration_provenance") if isinstance(value.get("metadata"), Mapping) else None
        source_checksum = (
            migration.get("source_snapshot_checksum")
            if isinstance(migration, Mapping)
            else snapshot_checksum
        )
        if source_checksum != identity.source_snapshot_checksum:
            raise PublicSiteError(f"private snapshot {relative} disagrees with trusted source input")
        provenance = {
            "schema_version": 1,
            "record_type": "public_snapshot_provenance",
            "identity": {
                "season": year,
                "sport": sport,
                "classification": classification,
                "source_path": identity.relative_path,
                "source_schema_version": identity.schema_version,
                "snapshot_schema_version": value.get("metadata", {}).get("schema_version", 2)
                if isinstance(value.get("metadata"), Mapping)
                else 2,
            },
            "source_snapshot_checksum": identity.source_snapshot_checksum,
            "source_file_sha256": identity.source_file_sha256,
            "source_file_bytes": identity.source_file_bytes,
            "snapshot_checksum": snapshot_checksum,
            "payload_sha256": _sha256_bytes(raw),
        }
        result.append((relative, raw, provenance))
    return result


def _public_provenance_bytes(provenance: Mapping[str, Any]) -> bytes:
    allowed = {
        "schema_version",
        "record_type",
        "identity",
        "source_snapshot_checksum",
        "source_file_sha256",
        "source_file_bytes",
        "snapshot_checksum",
        "payload_sha256",
    }
    if set(provenance) != allowed or provenance.get("record_type") != "public_snapshot_provenance":
        raise PublicSiteError("snapshot provenance schema is invalid")
    identity = provenance.get("identity")
    if not isinstance(identity, Mapping) or set(identity) != {
        "season", "sport", "classification", "source_path",
        "source_schema_version", "snapshot_schema_version",
    }:
        raise PublicSiteError("snapshot provenance identity is invalid")
    for name in ("source_snapshot_checksum", "source_file_sha256", "snapshot_checksum", "payload_sha256"):
        _digest(provenance.get(name), f"snapshot provenance {name}")
    if provenance.get("schema_version") != 1 or identity.get("sport") != "cfb":
        raise PublicSiteError("snapshot provenance version or sport is invalid")
    if not _YEAR.fullmatch(str(identity.get("season"))):
        raise PublicSiteError("snapshot provenance season is invalid")
    _safe_relative(identity.get("source_path"), "snapshot provenance source path")
    if isinstance(provenance.get("source_file_bytes"), bool) or not isinstance(provenance.get("source_file_bytes"), int) or provenance.get("source_file_bytes") < 0:
        raise PublicSiteError("snapshot provenance byte count is invalid")
    if _deep_has_source_payload(provenance):
        raise PublicSiteError("snapshot provenance contains source payload")
    return _canonical_bytes(dict(provenance))


def _public_manifest_bytes(value: Mapping[str, Any]) -> bytes:
    expected = {
        "schema_version", "record_type", "format", "source_release_id",
        "source_candidate_tree_sha256", "manifest_inventory",
        "manifest_inventory_sha256", "snapshot_provenance",
        "firebase_json_sha256", "source_fingerprint",
        "trusted_input_manifest_sha256",
    }
    if set(value) != expected or value.get("schema_version") != 1 or value.get("record_type") != PUBLIC_MANIFEST_TYPE or value.get("format") != "public-export-v1":
        raise PublicSiteError("public manifest schema is invalid")
    release_id = value.get("source_release_id")
    if not isinstance(release_id, str) or _SAFE_RELEASE_ID.fullmatch(release_id) is None:
        raise PublicSiteError("public manifest release identity is invalid")
    for name in ("source_candidate_tree_sha256", "manifest_inventory_sha256", "firebase_json_sha256", "source_fingerprint", "trusted_input_manifest_sha256"):
        _digest(value.get(name), f"public manifest {name}")
    inventory = value.get("manifest_inventory")
    if not isinstance(inventory, list):
        raise PublicSiteError("public manifest inventory is invalid")
    _validate_inventory(inventory, "public manifest inventory")
    if _inventory_hash(inventory) != value["manifest_inventory_sha256"]:
        raise PublicSiteError("public manifest inventory digest mismatch")
    snapshots = value.get("snapshot_provenance")
    if not isinstance(snapshots, list):
        raise PublicSiteError("public manifest snapshot provenance is invalid")
    paths: list[str] = []
    for item in snapshots:
        if not isinstance(item, Mapping) or set(item) != {"path", "provenance_sha256"}:
            raise PublicSiteError("public manifest snapshot binding is invalid")
        path = _safe_relative(item.get("path"), "public manifest snapshot path")
        if not (_SNAPSHOT_PATH.fullmatch(path) or _SNAPSHOT_ARCHIVE_PATH.fullmatch(path)):
            raise PublicSiteError("public manifest snapshot path is not canonical")
        paths.append(path)
        _digest(item.get("provenance_sha256"), "public manifest snapshot digest")
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise PublicSiteError("public manifest snapshot bindings are not canonical")
    return _canonical_bytes(dict(value))


def _validate_inventory(value: Any, label: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise PublicSiteError(f"{label} must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "bytes", "sha256"}:
            raise PublicSiteError(f"{label} entry is invalid")
        path = _safe_relative(item.get("path"), f"{label} path")
        if path in seen:
            raise PublicSiteError(f"{label} contains duplicate paths")
        seen.add(path)
        if isinstance(item.get("bytes"), bool) or not isinstance(item.get("bytes"), int) or item.get("bytes") < 0:
            raise PublicSiteError(f"{label} byte count is invalid")
        _digest(item.get("sha256"), f"{label} digest")
        result.append({"path": path, "bytes": item["bytes"], "sha256": item["sha256"]})
    if [item["path"] for item in result] != sorted(item["path"] for item in result):
        raise PublicSiteError(f"{label} is not sorted")
    return tuple(result)


def _validate_public_json(site: Path, relative: str, path: Path) -> None:
    raw = path.read_bytes()
    looks_like_json = raw.lstrip().startswith((b"{", b"["))
    if not relative.endswith(".json") and not looks_like_json:
        return
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        if relative.endswith(".json"):
            raise PublicSiteValidationError(f"public JSON {relative} is unreadable") from exc
        # A non-JSON asset whose first byte happens to be ``{``/``[`` is
        # still allowed when it is not valid JSON; the archive and symlink
        # gates have already examined its raw bytes.
        return
    snapshot_match = _SNAPSHOT_PATH.fullmatch(relative) or _SNAPSHOT_ARCHIVE_PATH.fullmatch(relative)
    if snapshot_match:
        raw = _public_provenance_bytes(value) if isinstance(value, Mapping) else None
        if raw is None:
            raise PublicSiteValidationError(f"public snapshot {relative} is not an object")
        archive_match = _SNAPSHOT_ARCHIVE_PATH.fullmatch(relative)
        if archive_match is not None and value.get("snapshot_checksum") != archive_match.group("checksum"):
            raise PublicSiteValidationError(f"public snapshot archive {relative} is not content-addressed")
        if int(value["identity"]["season"]) != int(snapshot_match.group("year")):
            raise PublicSiteValidationError(f"public snapshot {relative} disagrees with its path")
        return
    if _deep_has_source_payload(value):
        raise PublicSiteValidationError(f"public JSON {relative} contains a source snapshot payload")
    if relative == "manifest.json":
        if not isinstance(value, Mapping):
            raise PublicSiteValidationError("public manifest must be an object")
        _public_manifest_bytes(value)
    elif relative == "release.json":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version", "record_type", "manifest_sha256", "source_release_id",
            "public_site_inventory_sha256",
        }:
            raise PublicSiteValidationError("public release metadata schema is invalid")
        if value.get("schema_version") != 1 or value.get("record_type") != PUBLIC_RELEASE_TYPE:
            raise PublicSiteValidationError("public release metadata type is invalid")
        _digest(value.get("manifest_sha256"), "public release manifest digest")
        _digest(value.get("public_site_inventory_sha256"), "public release inventory digest")
        release_id = value.get("source_release_id")
        if not isinstance(release_id, str) or _SAFE_RELEASE_ID.fullmatch(release_id) is None:
            raise PublicSiteValidationError("public release identity is invalid")


@dataclass(frozen=True)
class PublicOutputReport:
    valid: bool
    failures: tuple[str, ...] = ()
    inventory: tuple[dict[str, Any], ...] = ()

    @property
    def ok(self) -> bool:
        return self.valid and not self.failures

    def raise_for_failure(self) -> "PublicOutputReport":
        if not self.ok:
            raise PublicSiteValidationError("; ".join(self.failures[:8]) or "public output is invalid")
        return self


def validate_public_output(site: str | Path, *, expected_receipt: "LocalValidationReceipt | None" = None) -> PublicOutputReport:
    """Validate a public export without consuming private source payloads."""

    failures: list[str] = []
    try:
        root = _resolved_directory(site, "public site")
        files = _walk_files(root, "public site")
        if not files:
            raise PublicSiteValidationError("public site is empty")
        for relative, path in files:
            assert_public_bytes(relative, path.read_bytes())
            _validate_public_json(root, relative, path)
        if not (root / "manifest.json").is_file() or not (root / "release.json").is_file():
            raise PublicSiteValidationError("public site requires manifest.json and release.json")
        manifest = _json_file(root / "manifest.json", "public manifest")
        release = _json_file(root / "release.json", "public release metadata")
        manifest_bytes = (root / "manifest.json").read_bytes()
        manifest_hash = _sha256_bytes(manifest_bytes)
        if release.get("manifest_sha256") != manifest_hash:
            raise PublicSiteValidationError("public release does not bind manifest bytes")
        manifest_inventory = tuple(
            item for item in _validate_inventory(manifest.get("manifest_inventory"), "public manifest inventory")
        )
        actual_inventory = _inventory(root, "public site")
        actual_without_metadata = tuple(item for item in actual_inventory if item["path"] not in {"manifest.json", "release.json"})
        if actual_without_metadata != manifest_inventory:
            raise PublicSiteValidationError("public manifest inventory does not match exported files")
        actual_without_release = tuple(item for item in actual_inventory if item["path"] != "release.json")
        if release.get("public_site_inventory_sha256") != _inventory_hash(actual_without_release):
            raise PublicSiteValidationError("public release inventory digest does not match exported files")
        snapshot_bindings = manifest.get("snapshot_provenance")
        expected_snapshot_paths = [
            relative for relative, _path in _walk_files(root, "public site")
            if _SNAPSHOT_PATH.fullmatch(relative) or _SNAPSHOT_ARCHIVE_PATH.fullmatch(relative)
        ]
        actual_snapshot_bindings = sorted(item["path"] for item in snapshot_bindings)
        if actual_snapshot_bindings != expected_snapshot_paths:
            raise PublicSiteValidationError("public manifest snapshot paths do not match exported files")
        for binding in snapshot_bindings:
            path = root / binding["path"]
            if _sha256_file(path) != binding["provenance_sha256"]:
                raise PublicSiteValidationError("public manifest snapshot binding does not match file bytes")
        if expected_receipt is not None and actual_inventory != expected_receipt.public_site_inventory:
            raise PublicSiteValidationError("public site inventory does not match receipt")
        return PublicOutputReport(True, (), actual_inventory)
    except (OSError, PublicSiteError, KeyError, TypeError, ValueError) as exc:
        failures.append(str(exc))
        try:
            inventory = _inventory(Path(site), "public site")
        except Exception:
            inventory = ()
        return PublicOutputReport(False, tuple(failures), inventory)


validate_public_site = validate_public_output


def _receipt_value(
    *,
    inventory: tuple[dict[str, Any], ...],
    firebase_sha: str,
    fingerprint: str,
    trust_sha: str,
    private_evidence: Mapping[str, Any],
    baseline: Mapping[str, Any],
    independent_validation: Mapping[str, Any],
    transform: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "public_site_inventory": list(inventory),
        "public_site_inventory_sha256": _inventory_hash(inventory),
        "firebase_json_sha256": firebase_sha,
        "source_fingerprint": fingerprint,
        "trusted_input_manifest_sha256": trust_sha,
        "private_evidence": dict(private_evidence),
        "baseline": dict(baseline),
        "independent_validation": dict(independent_validation),
        "transform": dict(transform),
    }


@dataclass(frozen=True)
class LocalValidationReceipt:
    """Strict, path-free receipt for one locally validated public export."""

    public_site_inventory: tuple[dict[str, Any], ...]
    public_site_inventory_sha256: str
    firebase_json_sha256: str
    source_fingerprint: str
    trusted_input_manifest_sha256: str
    private_evidence: Mapping[str, Any]
    baseline: Mapping[str, Any]
    independent_validation: Mapping[str, Any]
    transform: Mapping[str, Any]
    schema_version: int = SCHEMA_VERSION
    record_type: str = RECORD_TYPE

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_type": self.record_type,
            "public_site_inventory": [dict(item) for item in self.public_site_inventory],
            "public_site_inventory_sha256": self.public_site_inventory_sha256,
            "firebase_json_sha256": self.firebase_json_sha256,
            "source_fingerprint": self.source_fingerprint,
            "trusted_input_manifest_sha256": self.trusted_input_manifest_sha256,
            "private_evidence": dict(self.private_evidence),
            "baseline": dict(self.baseline),
            "independent_validation": dict(self.independent_validation),
            "transform": dict(self.transform),
        }

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @property
    def digest(self) -> str:
        return _sha256_bytes(self.to_bytes())

    # Publication preparation consumes these names as a small compatibility
    # facade.  They are derived from the canonical fields above; none is an
    # additional authority and none is serialized a second time.
    @property
    def inventory_sha256(self) -> str:
        return _publication_inventory_hash(self.public_site_inventory)

    @property
    def configuration_sha256(self) -> str:
        return self.firebase_json_sha256

    @property
    def expected_baseline_sha256(self) -> str | None:
        value = self.baseline.get("record_sha256")
        return value if isinstance(value, str) else None

    @property
    def expected_predecessor(self) -> ProviderIdentity | None:
        value = self.baseline.get("identity")
        record = self.baseline.get("record")
        if not isinstance(value, Mapping) or not isinstance(record, Mapping):
            return None
        try:
            target = ProviderTarget.from_value(record["target"])
            return ProviderIdentity.from_value(value, target=target)
        except (TypeError, ValueError, KeyError):
            return None

    @property
    def retained_inputs_sha256(self) -> str | None:
        value = self.private_evidence.get("source_input_bundle_sha256")
        return value if isinstance(value, str) else None

    @property
    def validation_sha256(self) -> str:
        baseline_sha = self.expected_baseline_sha256
        retained_sha = self.retained_inputs_sha256
        if baseline_sha is None or retained_sha is None:
            return self.digest
        return _package_validation_hash(
            inventory_sha256=self.inventory_sha256,
            configuration_sha256=self.configuration_sha256,
            expected_baseline_sha256=baseline_sha,
            retained_inputs_sha256=retained_sha,
        )

    @property
    def baseline_record(self) -> BaselineRecord | None:
        value = self.baseline.get("record")
        if not isinstance(value, Mapping):
            return None
        try:
            return BaselineRecord.from_dict(value)
        except (TypeError, ValueError, KeyError):
            return None

    @property
    def baseline_public_archive_sha256(self) -> str | None:
        value = self.baseline.get("public_archive_sha256")
        return value if isinstance(value, str) else None

    @property
    def baseline_sanitizer_record_sha256(self) -> str | None:
        value = self.baseline.get("sanitizer_record_sha256")
        return value if isinstance(value, str) else None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LocalValidationReceipt":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version", "record_type", "public_site_inventory",
            "public_site_inventory_sha256", "firebase_json_sha256", "source_fingerprint",
            "trusted_input_manifest_sha256", "private_evidence", "baseline",
            "independent_validation", "transform",
        }:
            raise PublicSiteError("local validation receipt fields do not match its schema")
        if value.get("schema_version") != SCHEMA_VERSION or value.get("record_type") != RECORD_TYPE:
            raise PublicSiteError("local validation receipt schema is unsupported")
        inventory = _validate_inventory(value.get("public_site_inventory"), "receipt inventory")
        for name in ("public_site_inventory_sha256", "firebase_json_sha256", "source_fingerprint", "trusted_input_manifest_sha256"):
            _digest(value.get(name), f"receipt {name}")
        if _inventory_hash(inventory) != value["public_site_inventory_sha256"]:
            raise PublicSiteError("receipt inventory digest mismatch")
        for name in ("private_evidence", "baseline", "independent_validation", "transform"):
            if not isinstance(value.get(name), Mapping):
                raise PublicSiteError(f"receipt {name} must be an object")
        _validate_receipt_nested(value)
        return cls(
            inventory,
            value["public_site_inventory_sha256"],
            value["firebase_json_sha256"],
            value["source_fingerprint"],
            value["trusted_input_manifest_sha256"],
            dict(value["private_evidence"]),
            dict(value["baseline"]),
            dict(value["independent_validation"]),
            dict(value["transform"]),
        )

    @classmethod
    def from_bytes(cls, value: bytes) -> "LocalValidationReceipt":
        if not isinstance(value, bytes) or len(value) > 16 * 1024 * 1024:
            raise PublicSiteError("local validation receipt bytes are invalid")
        try:
            parsed = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicSiteError("local validation receipt is not JSON") from exc
        receipt = cls.from_dict(parsed)
        if value != receipt.to_bytes():
            raise PublicSiteError("local validation receipt is not canonical JSON")
        return receipt


def _receipt_from_value(value: Mapping[str, Any]) -> LocalValidationReceipt:
    return LocalValidationReceipt.from_dict(value)


def _validate_receipt_nested(value: Mapping[str, Any]) -> None:
    evidence = value.get("private_evidence")
    if set(evidence) != {
        "candidate_release_id", "candidate_tree_sha256", "candidate_manifest_sha256",
        "source_input_manifest_sha256", "source_input_bundle_sha256", "source_inputs",
        "original_prepared_archive_sha256", "baseline_private_archive_sha256",
        "baseline_record_sha256", "baseline_public_archive_sha256",
        "baseline_sanitizer_record_sha256",
    }:
        raise PublicSiteError("receipt private evidence fields do not match its schema")
    release_id = evidence["candidate_release_id"]
    if not isinstance(release_id, str) or _SAFE_RELEASE_ID.fullmatch(release_id) is None:
        raise PublicSiteError("receipt candidate release identity is invalid")
    for name in ("candidate_tree_sha256", "candidate_manifest_sha256", "source_input_manifest_sha256"):
        _digest(evidence[name], f"receipt private evidence {name}")
    bundle_sha = evidence["source_input_bundle_sha256"]
    if bundle_sha is not None:
        _digest(bundle_sha, "receipt private evidence source_input_bundle_sha256")
    for name in (
        "original_prepared_archive_sha256", "baseline_private_archive_sha256",
        "baseline_record_sha256", "baseline_public_archive_sha256",
        "baseline_sanitizer_record_sha256",
    ):
        if evidence[name] is not None:
            _digest(evidence[name], f"receipt private evidence {name}")
    source_entries = evidence["source_inputs"]
    if not isinstance(source_entries, list) or not source_entries:
        raise PublicSiteError("receipt source input identities are required")
    source_paths: list[str] = []
    for item in source_entries:
        if not isinstance(item, Mapping) or set(item) != {
            "season", "sport", "classification", "path", "source_file_sha256",
            "source_file_bytes", "source_snapshot_checksum", "schema_version",
        }:
            raise PublicSiteError("receipt source input identity fields are invalid")
        if isinstance(item["season"], bool) or not isinstance(item["season"], int) or not _YEAR.fullmatch(str(item["season"])):
            raise PublicSiteError("receipt source input season is invalid")
        if item["sport"] != "cfb" or not isinstance(item["classification"], str) or not item["classification"]:
            raise PublicSiteError("receipt source input identity is invalid")
        source_paths.append(_safe_relative(item["path"], "receipt source input path"))
        _digest(item["source_file_sha256"], "receipt source input file digest")
        _digest(item["source_snapshot_checksum"], "receipt source input snapshot checksum")
        if isinstance(item["source_file_bytes"], bool) or not isinstance(item["source_file_bytes"], int) or item["source_file_bytes"] < 0:
            raise PublicSiteError("receipt source input byte count is invalid")
        if item["schema_version"] != 3:
            raise PublicSiteError("receipt source input schema version is invalid")
    if source_paths != sorted(source_paths) or len(source_paths) != len(set(source_paths)):
        raise PublicSiteError("receipt source input identities are not canonical")

    baseline = value.get("baseline")
    if set(baseline) != {
        "kind", "record_sha256", "archive_sha256", "application_tree_sha256",
        "record", "identity", "public_archive_sha256", "sanitizer_record_sha256",
    }:
        raise PublicSiteError("receipt baseline fields do not match its schema")
    if baseline["kind"] not in {"verified", "path"}:
        raise PublicSiteError("receipt baseline kind is invalid")
    _digest(baseline["application_tree_sha256"], "receipt baseline application tree digest")
    for name in ("record_sha256", "archive_sha256", "public_archive_sha256", "sanitizer_record_sha256"):
        if baseline[name] is not None:
            _digest(baseline[name], f"receipt baseline {name}")
    if baseline["kind"] == "verified":
        if baseline["record_sha256"] is None or baseline["archive_sha256"] is None:
            raise PublicSiteError("verified receipt baseline requires record and archive digests")
        if not isinstance(baseline["record"], Mapping) or not isinstance(baseline["identity"], Mapping):
            raise PublicSiteError("verified receipt baseline record and identity are required")
        try:
            record = BaselineRecord.from_dict(baseline["record"])
        except (TypeError, ValueError, KeyError) as exc:
            raise PublicSiteError("receipt baseline record is invalid") from exc
        if record.digest != baseline["record_sha256"] or record.archive_sha256 != baseline["archive_sha256"]:
            raise PublicSiteError("receipt baseline record digest does not match its contents")
        if record.observed.to_dict(include_target=False) != dict(baseline["identity"]):
            raise PublicSiteError("receipt baseline identity does not match its record")
    elif baseline["record"] is not None or baseline["identity"] is not None:
        raise PublicSiteError("path-backed receipt baseline cannot claim a record")

    independent = value.get("independent_validation")
    if set(independent) != {"validator", "success", "failure_count", "checked_artifacts"}:
        raise PublicSiteError("receipt validation fields do not match its schema")
    if independent["validator"] != "cfb.release.validate_release" or independent["success"] is not True or independent["failure_count"] != 0:
        raise PublicSiteError("receipt does not prove successful independent validation")
    checked = independent["checked_artifacts"]
    if not isinstance(checked, list) or any(not isinstance(path, str) for path in checked) or checked != sorted(checked):
        raise PublicSiteError("receipt checked artifact list is invalid")
    for path in checked:
        _safe_relative(path, "receipt checked artifact path")

    transform = value.get("transform")
    if set(transform) != {
        "format", "public_manifest_path", "public_release_path", "snapshot_paths",
        "replaced_snapshot_count", "private_manifest_retained_for_validation",
    }:
        raise PublicSiteError("receipt transform fields do not match its schema")
    if transform["format"] != "public-export-v1" or transform["public_manifest_path"] != "manifest.json" or transform["public_release_path"] != "release.json" or transform["private_manifest_retained_for_validation"] is not True:
        raise PublicSiteError("receipt transform binding is invalid")
    snapshot_paths = transform["snapshot_paths"]
    if not isinstance(snapshot_paths, list) or any(not isinstance(path, str) for path in snapshot_paths) or snapshot_paths != sorted(snapshot_paths) or transform["replaced_snapshot_count"] != len(snapshot_paths):
        raise PublicSiteError("receipt transformed snapshot paths are invalid")
    for path in snapshot_paths:
        if not (_SNAPSHOT_PATH.fullmatch(path) or _SNAPSHOT_ARCHIVE_PATH.fullmatch(path)):
            raise PublicSiteError("receipt transformed snapshot path is not canonical")


def _write_new(path: Path, content: bytes, label: str) -> None:
    if path.exists() or path.is_symlink():
        if path.is_file() and path.read_bytes() == content:
            return
        raise PublicSiteError(f"refusing to overwrite existing {label}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def validate_and_export(
    private_candidate: str | Path | Release,
    baseline: VerifiedBaseline | str | Path,
    source_inputs: RecoveryInputBundle,
    firebase_json: str | Path,
    output_site: str | Path,
    receipt_path: str | Path,
    *,
    source_root: str | Path | None = None,
    trusted_input_manifest: str | Path | Mapping[str, Any] | None = None,
    code_root: str | Path | None = None,
) -> LocalValidationReceipt:
    """Validate a private candidate, then create a provenance-only site."""

    if not isinstance(source_inputs, RecoveryInputBundle):
        raise PublicSiteError("source_inputs must be a verified RecoveryInputBundle")
    if trusted_input_manifest is None:
        trusted_input_manifest = source_inputs.manifest_path
    private_site, private_manifest = _candidate_site(private_candidate)
    baseline_site, baseline_info = _baseline_info(baseline)
    source_dir, trust_sha, pins, source_manifest_sha, _trust_raw = _ensure_trusted_source_bundle(
        source_inputs, source_root, trusted_input_manifest
    )
    expected_bundle_sha = _trusted_bundle_sha(_trust_raw)
    if expected_bundle_sha is not None and source_inputs.bundle_sha256 != expected_bundle_sha:
        raise PublicSiteError("source-input archive digest does not match trusted identity")
    trusted_evidence = _trusted_evidence_hashes(_trust_raw)
    if (
        trusted_evidence["baseline_record_sha256"] is not None
        and baseline_info.get("record_sha256") != trusted_evidence["baseline_record_sha256"]
    ):
        raise PublicSiteError("baseline record does not match trusted identity")
    if (
        trusted_evidence["baseline_private_archive_sha256"] is not None
        and baseline_info.get("archive_sha256") != trusted_evidence["baseline_private_archive_sha256"]
    ):
        raise PublicSiteError("baseline archive does not match trusted identity")
    firebase_path = _resolved_file(firebase_json, "firebase.json")
    firebase_sha = _sha256_file(firebase_path)
    fingerprint = source_fingerprint(code_root or Path(__file__).resolve().parents[1])
    candidate_tree_before = _tree_digest(private_site, "private candidate")
    output_path = Path(output_site)
    receipt_output = Path(receipt_path)
    if output_path.exists() or output_path.is_symlink():
        if output_path.is_symlink() or not output_path.is_dir() or any(output_path.iterdir()):
            raise PublicSiteError("public output directory must be new or empty")
    if receipt_output.exists() or receipt_output.is_symlink():
        raise PublicSiteError("receipt path must be new")
    resolved_output = output_path.resolve()
    resolved_receipt = receipt_output.resolve()
    if resolved_receipt == resolved_output or resolved_output in resolved_receipt.parents:
        raise PublicSiteError("receipt must remain outside the public site")
    if resolved_receipt == private_site or private_site in resolved_receipt.parents:
        raise PublicSiteError("receipt must remain outside the private candidate")
    if resolved_receipt == source_dir or source_dir in resolved_receipt.parents:
        raise PublicSiteError("receipt must remain outside retained source inputs")
    if source_dir == resolved_output or source_dir in resolved_output.parents or resolved_output in source_dir.parents:
        raise PublicSiteError("public output must remain outside private source inputs")
    if source_dir == private_site or source_dir in private_site.parents or private_site in source_dir.parents:
        raise PublicSiteError("private candidate and source inputs must remain separate")
    if firebase_path == resolved_output or firebase_path in resolved_output.parents:
        raise PublicSiteError("firebase.json must remain outside the public site")

    report = validate_release(
        private_candidate,
        published_site=baseline,
        source_inputs=source_inputs,
    )
    if not report.ok:
        detail = "; ".join(str(failure) for failure in report.failures[:8])
        raise LocalValidationError(detail or "private release validation failed")

    # Local validation may be a long calculation.  Refuse to attest a result
    # if the executable/configuration fingerprint, trust pins, retained input
    # manifest, or candidate bytes changed while it was running.
    if source_fingerprint(code_root or Path(__file__).resolve().parents[1]) != fingerprint:
        raise LocalValidationError("validator/runtime source changed during local validation")
    source_inputs.assert_current()
    trust_sha_after, _pins_after, _manifest_after, _trust_after = _trusted_manifest_details(
        trusted_input_manifest
    )
    if trust_sha_after != trust_sha:
        raise LocalValidationError("trusted input manifest changed during local validation")
    if _source_bundle_manifest_sha(source_dir) != source_manifest_sha:
        raise LocalValidationError("source-input manifest changed during local validation")
    if _tree_digest(private_site, "private candidate") != candidate_tree_before:
        raise LocalValidationError("private candidate changed during local validation")

    snapshots = _source_snapshot_records(private_site, source_inputs)
    private_tree_sha = _tree_digest(private_site, "private candidate")
    private_manifest_sha = _sha256_file(private_site / "manifest.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_directory = Path(tempfile.mkdtemp(prefix="sportsrank-public-export-", dir=output_path.parent))
    committed = False
    try:
        for relative, source in _walk_files(private_site, "private candidate"):
            destination = temporary_directory / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        snapshot_provenance_bindings: list[dict[str, str]] = []
        for relative, _raw, provenance in snapshots:
            destination = temporary_directory / relative
            destination.write_bytes(_public_provenance_bytes(provenance))
            snapshot_provenance_bindings.append(
                {
                    "path": relative,
                    "provenance_sha256": _sha256_bytes(destination.read_bytes()),
                }
            )

        manifest_inventory = tuple(
            item
            for item in _inventory(temporary_directory, "public export")
            if item["path"] not in {"manifest.json", "release.json"}
        )
        release_id = private_manifest.get("release_id")
        if not isinstance(release_id, str) or _SAFE_RELEASE_ID.fullmatch(release_id) is None:
            raise PublicSiteError("private candidate release identity is invalid")
        public_manifest = {
            "schema_version": 1,
            "record_type": PUBLIC_MANIFEST_TYPE,
            "format": "public-export-v1",
            "source_release_id": release_id,
            "source_candidate_tree_sha256": private_tree_sha,
            "manifest_inventory": list(manifest_inventory),
            "manifest_inventory_sha256": _inventory_hash(manifest_inventory),
            "snapshot_provenance": sorted(snapshot_provenance_bindings, key=lambda item: item["path"]),
            "firebase_json_sha256": firebase_sha,
            "source_fingerprint": fingerprint,
            "trusted_input_manifest_sha256": trust_sha,
        }
        manifest_bytes = _public_manifest_bytes(public_manifest)
        (temporary_directory / "manifest.json").write_bytes(manifest_bytes)
        public_release = {
            "schema_version": 1,
            "record_type": PUBLIC_RELEASE_TYPE,
            "manifest_sha256": _sha256_bytes(manifest_bytes),
            "source_release_id": release_id,
            # ``release.json`` carries this digest, so exclude it to avoid a
            # self-referential hash.  The manifest is final and remains bound.
            "public_site_inventory_sha256": _inventory_hash(
                tuple(
                    item
                    for item in _inventory(temporary_directory, "public export")
                    if item["path"] != "release.json"
                )
            ),
        }
        (temporary_directory / "release.json").write_bytes(_canonical_bytes(public_release))
        output_report = validate_public_output(temporary_directory)
        output_report.raise_for_failure()
        inventory = output_report.inventory
        private_evidence = {
            "candidate_release_id": release_id,
            "candidate_tree_sha256": private_tree_sha,
            "candidate_manifest_sha256": private_manifest_sha,
            "source_input_manifest_sha256": source_manifest_sha,
            "source_input_bundle_sha256": source_inputs.bundle_sha256,
            "source_inputs": [identity.to_dict() for identity in source_inputs.identities],
            **trusted_evidence,
        }
        independent = {
            "validator": "cfb.release.validate_release",
            "success": True,
            "failure_count": 0,
            "checked_artifacts": sorted(report.checked_artifacts),
        }
        receipt_baseline = dict(baseline_info)
        if trusted_evidence["baseline_public_archive_sha256"] is not None:
            receipt_baseline["public_archive_sha256"] = trusted_evidence["baseline_public_archive_sha256"]
        if trusted_evidence["baseline_sanitizer_record_sha256"] is not None:
            receipt_baseline["sanitizer_record_sha256"] = trusted_evidence["baseline_sanitizer_record_sha256"]
        transform = {
            "format": "public-export-v1",
            "public_manifest_path": "manifest.json",
            "public_release_path": "release.json",
            "snapshot_paths": sorted(item["path"] for item in snapshot_provenance_bindings),
            "replaced_snapshot_count": len(snapshot_provenance_bindings),
            "private_manifest_retained_for_validation": True,
        }
        receipt_value = _receipt_value(
            inventory=inventory,
            firebase_sha=firebase_sha,
            fingerprint=fingerprint,
            trust_sha=trust_sha,
            private_evidence=private_evidence,
            baseline=receipt_baseline,
            independent_validation=independent,
            transform=transform,
        )
        receipt = _receipt_from_value(receipt_value)
        _write_new(receipt_output, receipt.to_bytes(), "local validation receipt")
        if output_path.exists() and any(output_path.iterdir()):
            raise PublicSiteError("public output directory became non-empty during export")
        # An empty pre-created directory is an allowed staging target.  Remove
        # that directory only after the receipt and copied tree have passed all
        # checks, so the final rename remains atomic and never deletes data.
        if output_path.exists():
            output_path.rmdir()
        os.replace(temporary_directory, output_path)
        committed = True
        return receipt
    finally:
        if not committed:
            shutil.rmtree(temporary_directory, ignore_errors=True)


def verify_local_receipt(
    receipt_path: str | Path,
    site: str | Path,
    firebase_json: str | Path,
    source_root: str | Path,
    trusted_input_manifest: str | Path | Mapping[str, Any],
    *,
    code_root: str | Path | None = None,
) -> LocalValidationReceipt:
    """Verify one receipt against current code, source pins, and public bytes."""

    receipt_file = _resolved_file(receipt_path, "local validation receipt")
    receipt = LocalValidationReceipt.from_bytes(receipt_file.read_bytes())
    site_path = _resolved_directory(site, "public site")
    firebase_path = _resolved_file(firebase_json, "firebase.json")
    if receipt_file == site_path or receipt_file in site_path.parents:
        raise PublicSiteError("receipt must remain outside the public site")
    if _sha256_file(firebase_path) != receipt.firebase_json_sha256:
        raise PublicSiteError("firebase.json digest does not match receipt")
    # Hosted verification receives the repository checkout as ``source_root``.
    # It must never require or inspect the retained raw-input directory.  The
    # committed trust manifest carries the exact hashes and identities that
    # were accepted during local validation; those are compared to the safe
    # receipt below.
    repository = _resolved_directory(source_root, "source root")
    if code_root is not None and _resolved_directory(code_root, "source root") != repository:
        raise PublicSiteError("code_root and source_root must identify the same checkout")
    fingerprint = source_fingerprint(repository)
    if fingerprint != receipt.source_fingerprint:
        raise PublicSiteError("trusted source fingerprint does not match receipt")
    trust_sha, trusted_pins, expected_manifest_sha, trust_raw = _trusted_manifest_details(
        trusted_input_manifest
    )
    if trust_sha != receipt.trusted_input_manifest_sha256:
        raise PublicSiteError("trusted input manifest digest does not match receipt")
    evidence = receipt.private_evidence
    if expected_manifest_sha is None:
        raise PublicSiteError("trusted input manifest has no source-input manifest digest")
    if evidence.get("source_input_manifest_sha256") != expected_manifest_sha:
        raise PublicSiteError("source-input manifest digest does not match receipt")
    trusted_bundle_sha = _trusted_bundle_sha(trust_raw)
    if trusted_bundle_sha is not None and evidence.get("source_input_bundle_sha256") != trusted_bundle_sha:
        raise PublicSiteError("source-input archive digest does not match receipt")
    if trusted_bundle_sha is None and evidence.get("source_input_bundle_sha256") is not None:
        raise PublicSiteError("receipt requires a trusted source-input archive identity")
    trusted_evidence = _trusted_evidence_hashes(trust_raw)
    for name, expected in trusted_evidence.items():
        if expected is not None and evidence.get(name) != expected:
            raise PublicSiteError(f"receipt {name} does not match trusted evidence")
    if (
        trusted_evidence["baseline_record_sha256"] is not None
        and receipt.baseline.get("record_sha256") != trusted_evidence["baseline_record_sha256"]
    ):
        raise PublicSiteError("receipt baseline record does not match trusted evidence")
    if (
        trusted_evidence["baseline_private_archive_sha256"] is not None
        and receipt.baseline.get("archive_sha256") != trusted_evidence["baseline_private_archive_sha256"]
    ):
        raise PublicSiteError("receipt baseline archive does not match trusted evidence")
    if (
        trusted_evidence["baseline_public_archive_sha256"] is not None
        and receipt.baseline.get("public_archive_sha256") != trusted_evidence["baseline_public_archive_sha256"]
    ):
        raise PublicSiteError("receipt public baseline archive does not match trusted evidence")
    if (
        trusted_evidence["baseline_sanitizer_record_sha256"] is not None
        and receipt.baseline.get("sanitizer_record_sha256") != trusted_evidence["baseline_sanitizer_record_sha256"]
    ):
        raise PublicSiteError("receipt baseline sanitizer record does not match trusted evidence")
    trusted_source = trust_raw.get("source_inputs")
    if isinstance(trusted_source, Mapping) and isinstance(trusted_source.get("files"), Mapping):
        trusted_files = trusted_source["files"]
    elif isinstance(trust_raw.get("files"), list):
        trusted_files = {}
        for entry in trust_raw["files"]:
            if not isinstance(entry, Mapping):
                raise PublicSiteError("trusted source-input file identity is invalid")
            trusted_files[entry["path"]] = entry
    else:
        raise PublicSiteError("trusted input manifest has no source-input file identities")
    receipt_source = evidence.get("source_inputs")
    expected_paths = sorted(trusted_files)
    if not isinstance(receipt_source, list) or [item.get("path") for item in receipt_source] != expected_paths:
        raise PublicSiteError("receipt source-input paths do not match trusted identities")
    for item in receipt_source:
        expected = trusted_files.get(item["path"])
        if not isinstance(expected, Mapping) or expected.get("sha256") != item["source_file_sha256"] or expected.get("bytes") != item["source_file_bytes"]:
            raise PublicSiteError("receipt source-input identity does not match trusted manifest")
        expected_snapshot = expected.get("snapshot_checksum")
        if expected_snapshot is not None and expected_snapshot != item["source_snapshot_checksum"]:
            raise PublicSiteError("receipt source-input snapshot checksum does not match trusted manifest")
    output_report = validate_public_output(site_path, expected_receipt=receipt)
    output_report.raise_for_failure()
    manifest = _json_file(site_path / "manifest.json", "public manifest")
    if (manifest.get("source_candidate_tree_sha256") != evidence["candidate_tree_sha256"]
            or manifest.get("source_release_id") != evidence["candidate_release_id"]):
        raise PublicSiteError("public manifest private candidate binding does not match receipt")
    release = _json_file(site_path / "release.json", "public release metadata")
    if release.get("source_release_id") != evidence["candidate_release_id"]:
        raise PublicSiteError("public release private candidate binding does not match receipt")
    if manifest.get("source_fingerprint") != fingerprint or manifest.get("trusted_input_manifest_sha256") != trust_sha:
        raise PublicSiteError("public manifest trust bindings do not match receipt")
    if manifest.get("firebase_json_sha256") != receipt.firebase_json_sha256:
        raise PublicSiteError("public manifest Firebase binding does not match receipt")
    if receipt.transform.get("snapshot_paths") != [item["path"] for item in manifest.get("snapshot_provenance", [])]:
        raise PublicSiteError("receipt transform does not match public manifest snapshot bindings")
    if _inventory_hash(output_report.inventory) != receipt.public_site_inventory_sha256:
        raise PublicSiteError("public inventory digest does not match receipt")
    _ = repository, trusted_pins
    return receipt


def _default_target() -> dict[str, str]:
    return {"project": "sportsrank-837af", "site": "sportsrank-837af", "channel": "live"}


def _load_target(value: str | None) -> Mapping[str, Any]:
    if value is None:
        return _default_target()
    path = Path(value)
    if path.is_file():
        raw = _json_file(path, "baseline target")
    else:
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PublicSiteError("baseline target must be JSON or a JSON file") from exc
    if not isinstance(raw, Mapping):
        raise PublicSiteError("baseline target must be an object")
    return raw


def _load_cli_source_inputs(args: argparse.Namespace) -> RecoveryInputBundle:
    trust_sha, pins, expected_manifest_sha, raw = _trusted_manifest_details(args.trusted_input_manifest)
    del trust_sha, expected_manifest_sha, raw
    source_archive = getattr(args, "source_archive", None)
    source_sha = getattr(args, "source_archive_sha256", None)
    if (source_archive is None) != (source_sha is None):
        raise PublicSiteError("source archive and source archive digest must be supplied together")
    return RecoveryInputBundle.from_directory(
        args.source_root,
        trusted_file_sha256=pins,
        archive=source_archive,
        expected_archive_sha256=source_sha,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate private SportsRank output and export a safe public site.")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="validate a private candidate and write a public export")
    export.add_argument("--private-candidate", "--candidate", "--candidate-root", dest="private_candidate", type=Path, required=True)
    export.add_argument("--baseline-archive", type=Path, required=True)
    export.add_argument("--baseline-sha256", "--baseline-archive-sha256", dest="baseline_sha256", required=True)
    export.add_argument("--baseline-target")
    export.add_argument("--source-input-root", dest="source_root", type=Path, required=True)
    export.add_argument("--source-archive", "--source-input-archive", dest="source_archive", type=Path)
    export.add_argument("--source-archive-sha256", "--source-input-sha256", dest="source_archive_sha256")
    export.add_argument("--trusted-input-manifest", type=Path, required=True)
    export.add_argument("--firebase-json", type=Path, required=True)
    export.add_argument("--output-site", "--site", dest="output_site", type=Path, required=True)
    export.add_argument("--receipt", "--receipt-path", dest="receipt_path", type=Path, required=True)
    export.add_argument("--code-root", type=Path)

    verify = commands.add_parser("verify", help="verify a local validation receipt and public site")
    verify.add_argument("--receipt", "--receipt-path", dest="receipt_path", type=Path, required=True)
    verify.add_argument("--site", "--output-site", dest="site", type=Path, required=True)
    verify.add_argument("--firebase-json", type=Path, required=True)
    verify.add_argument("--source-root", dest="source_root", type=Path, required=True)
    verify.add_argument("--trusted-input-manifest", type=Path, required=True)
    verify.add_argument("--code-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export":
            source_inputs = _load_cli_source_inputs(args)
            baseline = import_baseline(
                args.baseline_archive,
                target=_load_target(args.baseline_target),
                expected_archive_sha256=args.baseline_sha256,
            )
            try:
                receipt = validate_and_export(
                    args.private_candidate,
                    baseline,
                    source_inputs,
                    args.firebase_json,
                    args.output_site,
                    args.receipt_path,
                    source_root=args.source_root,
                    trusted_input_manifest=args.trusted_input_manifest,
                    code_root=args.code_root,
                )
            finally:
                baseline.close()
            print(json.dumps({"command": "export", "record_type": RECORD_TYPE, "receipt_sha256": receipt.digest, "inventory_files": len(receipt.public_site_inventory)}, sort_keys=True))
            return 0
        receipt = verify_local_receipt(
            args.receipt_path,
            args.site,
            args.firebase_json,
            args.source_root,
            args.trusted_input_manifest,
            code_root=args.code_root,
        )
        print(json.dumps({"command": "verify", "record_type": RECORD_TYPE, "receipt_sha256": receipt.digest, "inventory_files": len(receipt.public_site_inventory)}, sort_keys=True))
        return 0
    except (PublicSiteError, RecoveryInputError, OSError, TypeError, ValueError) as exc:
        print(f"public-site {args.command} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "LocalValidationError",
    "LocalValidationReceipt",
    "PublicOutputReport",
    "PublicSiteError",
    "PublicSiteValidationError",
    "source_fingerprint",
    "validate_and_export",
    "validate_public_output",
    "validate_public_site",
    "verify_local_receipt",
]
