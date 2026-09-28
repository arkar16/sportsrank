"""Trusted, external source-input identities for offline recovery.

The recovery-input bundle is deliberately separate from a public application
tree.  Its manifest is descriptive only: callers must provide the retained
raw-file digests (and, when available, the retained bundle archive digest) as
the trust anchor before any source can be consumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from types import MappingProxyType
from typing import Any, Mapping


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT_NAME = re.compile(r"cfb-([a-z0-9]+)-(\d{4})\.json\Z")
_MANIFEST_NAME = "inputs-manifest.json"
_LEGACY_SCHEMA_VERSION = 3
_NATIVE_SCHEMA_VERSION = 4
_SUPPORTED_SCHEMA_VERSIONS = frozenset({_LEGACY_SCHEMA_VERSION, _NATIVE_SCHEMA_VERSION})


class RecoveryInputError(ValueError):
    """Retained source inputs cannot establish a trusted immutable bundle."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise RecoveryInputError(f"source input is unreadable: {path}") from exc
    return digest.hexdigest()


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise RecoveryInputError("source input manifest contains an unsafe path")
    if "\\" in value or "\x00" in value:
        raise RecoveryInputError("source input manifest contains an unsafe path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RecoveryInputError("source input manifest contains an unsafe path")
    return path.as_posix()


def _verified_snapshot_identity(
    state: Mapping[str, Any], *, path: Path
) -> tuple[int, str]:
    """Validate a retained source snapshot and return its schema/checksum."""

    try:
        from .season_snapshot import (
            _checksum,
            _complete_through,
            _validate_provider_week_mapping,
            _validate_repair_metadata,
        )
        from .season_source import SourceTeam, normalize_game

        schema_version = state.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or schema_version not in _SUPPORTED_SCHEMA_VERSIONS
        ):
            raise RecoveryInputError(
                f"source snapshot schema is unsupported: {path}"
            )
        teams = tuple(SourceTeam(**item) for item in (state.get("teams") or []))
        games = tuple(normalize_game(item) for item in (state.get("games") or []))
        season = int(state["year"])
        _validate_provider_week_mapping(season, games)
        if int(state.get("complete_through_week", -1)) != _complete_through(games):
            raise RecoveryInputError(
                f"source snapshot completion metadata is invalid: {path}"
            )
        if schema_version == _NATIVE_SCHEMA_VERSION:
            if state.get("migration_provenance") is not None:
                raise RecoveryInputError(
                    f"source Schema 4 snapshot must be native provider evidence: {path}"
                )
            _validate_repair_metadata(state, games, teams)
        checksum = state.get("checksum")
        if not isinstance(checksum, str) or _SHA256.fullmatch(checksum) is None:
            raise RecoveryInputError(f"source snapshot checksum is invalid: {path}")
        if checksum != _checksum(state, teams, games):
            raise RecoveryInputError(
                f"source snapshot canonical checksum mismatch: {path}"
            )
        return int(schema_version), checksum
    except RecoveryInputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RecoveryInputError(f"source snapshot is invalid: {path}") from exc


@dataclass(frozen=True)
class SourceInputIdentity:
    """Immutable identity of one retained raw file and its normalized snapshot."""

    season: int
    sport: str
    classification: str
    relative_path: str
    source_file_sha256: str
    source_file_bytes: int
    source_snapshot_checksum: str
    schema_version: int = _LEGACY_SCHEMA_VERSION
    bundle_sha256: str | None = None
    manifest_sha256: str | None = None
    source_path: Path = field(default=Path("."), repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Return safe provenance without exposing an absolute local path."""

        return {
            "season": self.season,
            "sport": self.sport,
            "classification": self.classification,
            "path": self.relative_path,
            "source_file_sha256": self.source_file_sha256,
            "source_file_bytes": self.source_file_bytes,
            "source_snapshot_checksum": self.source_snapshot_checksum,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class RecoveryInputBundle:
    """A re-checkable view of trusted, immutable source snapshots."""

    root: Path
    manifest_path: Path
    snapshots_root: Path
    manifest_sha256: str
    bundle_sha256: str | None
    identities: tuple[SourceInputIdentity, ...]
    archive_path: Path | None = field(default=None, repr=False, compare=False)
    _trusted_file_sha256: Mapping[str, str] = field(
        default_factory=dict, repr=False, compare=False
    )

    @classmethod
    def from_directory(
        cls,
        root: str | Path,
        *,
        trusted_file_sha256: Mapping[str, str],
        archive: str | Path | None = None,
        expected_archive_sha256: str | None = None,
    ) -> "RecoveryInputBundle":
        """Load and verify a bundle using caller-supplied raw-file pins.

        The manifest is not an authority for its own hashes.  Every listed
        source file must have a matching externally supplied digest.  When an
        archive is supplied, its trusted digest and exact member bytes are
        checked against the directory as well.
        """

        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise RecoveryInputError("source input bundle root must be an existing directory")
        manifest_path = root_path / _MANIFEST_NAME
        try:
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RecoveryInputError("source input manifest is unreadable") from exc
        if not isinstance(manifest, Mapping) or not isinstance(manifest.get("files"), list):
            raise RecoveryInputError("source input manifest has an invalid shape")

        normalized_pins: dict[str, str] = {}
        for raw_path, raw_digest in trusted_file_sha256.items():
            relative = _safe_relative(str(raw_path))
            if not isinstance(raw_digest, str) or _SHA256.fullmatch(raw_digest) is None:
                raise RecoveryInputError(f"trusted source input digest is invalid: {relative}")
            if relative in normalized_pins:
                raise RecoveryInputError(f"trusted source input pin is duplicated: {relative}")
            normalized_pins[relative] = raw_digest

        entries: dict[str, Mapping[str, Any]] = {}
        for raw_entry in manifest["files"]:
            if not isinstance(raw_entry, Mapping):
                raise RecoveryInputError("source input manifest entry is invalid")
            relative = _safe_relative(raw_entry.get("path"))
            if relative in entries:
                raise RecoveryInputError(f"source input manifest path is duplicated: {relative}")
            digest = raw_entry.get("sha256")
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise RecoveryInputError(f"source input manifest digest is invalid: {relative}")
            byte_count = raw_entry.get("bytes")
            if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
                raise RecoveryInputError(f"source input manifest byte count is invalid: {relative}")
            entries[relative] = raw_entry

        if not entries:
            raise RecoveryInputError("source input manifest has no files")
        if set(normalized_pins) != set(entries):
            raise RecoveryInputError("trusted source input pins must cover exactly the manifest files")

        expected_files = {_MANIFEST_NAME, *entries}
        actual_files: set[str] = set()
        for path in root_path.rglob("*"):
            if path.is_symlink():
                raise RecoveryInputError("source input bundle cannot contain symlinks")
            if path.is_file():
                actual_files.add(path.relative_to(root_path).as_posix())
        if actual_files != expected_files:
            raise RecoveryInputError("source input bundle contains files outside its manifest")

        manifest_sha256 = _sha256_bytes(manifest_bytes)
        archive_path, bundle_sha256 = cls._verify_archive(
            root_path,
            expected_files,
            archive=archive,
            expected_archive_sha256=expected_archive_sha256,
        )

        identities: list[SourceInputIdentity] = []
        for relative, entry in sorted(entries.items()):
            relative_path = PurePosixPath(relative)
            match = _SNAPSHOT_NAME.fullmatch(relative_path.name)
            path_parts = relative_path.parts
            is_original_path = len(path_parts) == 2 and path_parts[0] == "snapshots"
            is_versioned_path = (
                len(path_parts) == 3
                and path_parts[0] == "snapshots"
                and _SHA256.fullmatch(path_parts[1]) is not None
            )
            if match is None or not (is_original_path or is_versioned_path):
                raise RecoveryInputError(f"source input path is not a canonical snapshot path: {relative}")
            filename_classification, filename_year = match.groups()
            path = root_path / relative
            if not path.is_file():
                raise RecoveryInputError(f"source input file is missing: {relative}")
            actual_digest = _sha256_file(path)
            if actual_digest != normalized_pins[relative] or actual_digest != entry["sha256"]:
                raise RecoveryInputError(f"source input digest mismatch: {relative}")
            actual_bytes = path.stat().st_size
            if actual_bytes != entry["bytes"]:
                raise RecoveryInputError(f"source input byte count mismatch: {relative}")
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RecoveryInputError(f"source snapshot is unreadable: {relative}") from exc
            if not isinstance(state, Mapping):
                raise RecoveryInputError(f"source snapshot is not an object: {relative}")
            try:
                season = int(state["year"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RecoveryInputError(f"source snapshot has no valid year: {relative}") from exc
            classification = str(state.get("classification", "")).upper()
            if (
                state.get("sport") != "cfb"
                or str(state.get("classification", "")).lower() != filename_classification
                or season != int(filename_year)
                or classification != filename_classification.upper()
            ):
                raise RecoveryInputError(f"source snapshot identity does not match its path: {relative}")
            schema_version, snapshot_checksum = _verified_snapshot_identity(
                state, path=path
            )
            if is_versioned_path and path_parts[1] != snapshot_checksum:
                raise RecoveryInputError(
                    f"source snapshot checksum does not match its versioned path: {relative}"
                )
            identities.append(
                SourceInputIdentity(
                    season=season,
                    sport="cfb",
                    classification=classification,
                    relative_path=relative,
                    source_file_sha256=actual_digest,
                    source_file_bytes=actual_bytes,
                    source_snapshot_checksum=snapshot_checksum,
                    schema_version=schema_version,
                    bundle_sha256=bundle_sha256,
                    manifest_sha256=manifest_sha256,
                    source_path=path,
                )
            )

        identity_keys = [
            (
                identity.season,
                identity.classification.upper(),
                identity.source_snapshot_checksum,
            )
            for identity in identities
        ]
        if len(identity_keys) != len(set(identity_keys)):
            raise RecoveryInputError("source input bundle contains a duplicate source version")
        return cls(
            root=root_path,
            manifest_path=manifest_path,
            snapshots_root=root_path / "snapshots",
            manifest_sha256=manifest_sha256,
            bundle_sha256=bundle_sha256,
            identities=tuple(identities),
            archive_path=archive_path,
            _trusted_file_sha256=MappingProxyType(normalized_pins),
        )

    @staticmethod
    def _verify_archive(
        root: Path,
        expected_files: set[str],
        *,
        archive: str | Path | None,
        expected_archive_sha256: str | None,
    ) -> tuple[Path | None, str | None]:
        if archive is None and expected_archive_sha256 is None:
            return None, None
        if archive is None or expected_archive_sha256 is None:
            raise RecoveryInputError("source input archive and its trusted SHA-256 must be supplied together")
        if _SHA256.fullmatch(expected_archive_sha256) is None:
            raise RecoveryInputError("trusted source input archive digest is invalid")
        archive_path = Path(archive).resolve()
        if not archive_path.is_file() or _sha256_file(archive_path) != expected_archive_sha256:
            raise RecoveryInputError("source input archive digest does not match trusted evidence")
        try:
            with tarfile.open(archive_path, "r:gz") as source:
                members = source.getmembers()
                files: dict[str, tarfile.TarInfo] = {}
                for member in members:
                    pure = PurePosixPath(member.name)
                    if (
                        not member.name
                        or member.name.startswith("/")
                        or any(part in {"", ".", ".."} for part in pure.parts)
                        or member.issym()
                        or member.islnk()
                        or not (member.isfile() or member.isdir())
                    ):
                        raise RecoveryInputError("source input archive contains an unsafe entry")
                    if member.isfile():
                        if member.name in files:
                            raise RecoveryInputError("source input archive contains a duplicate file")
                        files[member.name] = member
                if set(files) != expected_files:
                    raise RecoveryInputError("source input archive members do not match its directory")
                for relative, member in files.items():
                    handle = source.extractfile(member)
                    if handle is None or handle.read() != (root / relative).read_bytes():
                        raise RecoveryInputError(f"source input archive content mismatch: {relative}")
        except (OSError, tarfile.TarError) as exc:
            raise RecoveryInputError("source input archive is unreadable") from exc
        return archive_path, expected_archive_sha256

    @property
    def source_root(self) -> Path:
        """Return the directory consumed by ``SnapshotCache``."""

        return self.snapshots_root

    def assert_current(self) -> None:
        """Re-verify the retained bytes and manifest before a consume."""

        current = self.from_directory(
            self.root,
            trusted_file_sha256=self._trusted_file_sha256,
            archive=self.archive_path,
            expected_archive_sha256=self.bundle_sha256,
        )
        if current.manifest_sha256 != self.manifest_sha256 or current.provenance() != self.provenance():
            raise RecoveryInputError("source input bundle identity changed after verification")

    def resolve(
        self,
        year: int,
        classification: str = "FBS",
        *,
        source_snapshot_checksum: str | None = None,
    ) -> SourceInputIdentity:
        """Resolve and recheck one source file by its domain identity."""

        self.assert_current()
        wanted = (int(year), str(classification).upper())
        if source_snapshot_checksum is not None and (
            not isinstance(source_snapshot_checksum, str)
            or _SHA256.fullmatch(source_snapshot_checksum) is None
        ):
            raise RecoveryInputError("source snapshot checksum selector is invalid")
        matches = [
            identity
            for identity in self.identities
            if (identity.season, identity.classification.upper()) == wanted
            and (
                source_snapshot_checksum is None
                or identity.source_snapshot_checksum == source_snapshot_checksum
            )
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RecoveryInputError(
                f"trusted source input is ambiguous for {wanted[1]} {wanted[0]}; "
                "select source_snapshot_checksum"
            )
        raise RecoveryInputError(
            f"trusted source input is missing for {wanted[1]} {wanted[0]}"
            + (
                f" at checksum {source_snapshot_checksum}"
                if source_snapshot_checksum is not None
                else ""
            )
        )

    def provenance(self) -> dict[str, Any]:
        """Return stable, path-safe provenance for a Release/package record."""

        return {
            "schema_version": 1,
            "manifest_sha256": self.manifest_sha256,
            "bundle_sha256": self.bundle_sha256,
            "files": [identity.to_dict() for identity in self.identities],
        }

    def assert_external_to(self, public_site: str | Path) -> None:
        """Reject a bundle that overlaps a public application tree."""

        public = Path(public_site).resolve()
        retained_paths = (self.root, self.archive_path)
        for retained in retained_paths:
            if retained is None:
                continue
            retained_path = Path(retained).resolve()
            if (
                public == retained_path
                or public in retained_path.parents
                or retained_path in public.parents
            ):
                raise RecoveryInputError(
                    "source input bundle must remain outside the public application tree"
                )


__all__ = ["RecoveryInputBundle", "RecoveryInputError", "SourceInputIdentity"]
