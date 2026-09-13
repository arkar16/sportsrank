"""Import and verify a complete provider-backed Published Site baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from typing import Any, Mapping

try:
    from .publication_records import (
        BaselineRecord,
        ManagedResourceEvidence,
        ProviderIdentity,
        ProviderTarget,
        RecordValidationError,
        SourceProvenance,
    )
except ImportError:  # Direct execution from the cfb directory.
    from publication_records import (
        BaselineRecord,
        ManagedResourceEvidence,
        ProviderIdentity,
        ProviderTarget,
        RecordValidationError,
        SourceProvenance,
    )


MANAGED_RESOURCE_PATHS = ("/__/firebase/init.js", "/__/firebase/init.json")
_MANAGED_RELATIVES = frozenset(path.removeprefix("/") for path in MANAGED_RESOURCE_PATHS)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ARCHIVE_FILES = 250_000
_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


class BaselineValidationError(ValueError):
    """The retained evidence cannot establish a complete stable baseline."""


_VERIFIED_BASELINE_TOKEN = object()


@dataclass(frozen=True, init=False)
class VerifiedBaseline:
    """A verified baseline record and its provider-independent application tree."""

    record: BaselineRecord
    application_site: Path
    evidence_archive: Path
    _temporary: tempfile.TemporaryDirectory[str] | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        record: BaselineRecord,
        application_site: Path,
        evidence_archive: Path,
        temporary: tempfile.TemporaryDirectory[str] | None = None,
        *,
        _token: object | None = None,
    ) -> None:
        if _token is not _VERIFIED_BASELINE_TOKEN:
            raise BaselineValidationError("VerifiedBaseline values can only be created by verified import or capture")
        object.__setattr__(self, "record", record)
        object.__setattr__(self, "application_site", application_site)
        object.__setattr__(self, "evidence_archive", evidence_archive)
        object.__setattr__(self, "_temporary", temporary)

    @classmethod
    def _create(
        cls,
        record: BaselineRecord,
        application_site: Path,
        evidence_archive: Path,
        temporary: tempfile.TemporaryDirectory[str] | None = None,
    ) -> "VerifiedBaseline":
        return cls(record, application_site, evidence_archive, temporary, _token=_VERIFIED_BASELINE_TOKEN)

    def assert_current(self) -> None:
        if not self.application_site.is_dir() or _tree_digest(self.application_site) != self.record.application_tree_sha256:
            raise BaselineValidationError("verified baseline application tree was changed after import")
        if not self.evidence_archive.is_file() or _sha256_file(self.evidence_archive) != self.record.archive_sha256:
            raise BaselineValidationError("verified baseline archive no longer matches its trusted identity")

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()

    def __enter__(self) -> "VerifiedBaseline":
        return self

    def __exit__(self, *unused: object) -> None:
        self.close()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineValidationError(f"unreadable baseline evidence: {path.name}") from exc


def _canonical_provider_config(value: Any) -> bytes:
    if not isinstance(value, Mapping):
        raise BaselineValidationError("provider serving configuration must be an object")
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise BaselineValidationError("baseline inventory contains an unsafe relative path")
    if "\\" in value or "\x00" in value:
        raise BaselineValidationError("baseline inventory contains an unsafe relative path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BaselineValidationError("baseline inventory contains an unsafe relative path")
    return path.as_posix()


def _extract_archive(archive: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            if len(members) > _MAX_ARCHIVE_FILES:
                raise BaselineValidationError("baseline archive contains too many entries")
            total = 0
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
                    raise BaselineValidationError("baseline archive contains an unsafe entry")
                total += member.size
                if total > _MAX_ARCHIVE_BYTES:
                    raise BaselineValidationError("baseline archive exceeds the extraction limit")
            source.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise BaselineValidationError("baseline archive is unreadable") from exc


def _channel_identity(value: Any, target: ProviderTarget) -> ProviderIdentity:
    if not isinstance(value, Mapping):
        raise BaselineValidationError("provider channel observation must be an object")
    expected_name = f"sites/{target.site}/channels/{target.channel}"
    if value.get("name") != expected_name:
        raise BaselineValidationError("provider channel observation targets another channel")
    release = value.get("release")
    version = release.get("version") if isinstance(release, Mapping) else None
    try:
        return ProviderIdentity.from_value(
            {
                "release": release.get("name") if isinstance(release, Mapping) else None,
                "version": version.get("name") if isinstance(version, Mapping) else None,
            },
            target=target,
        )
    except RecordValidationError as exc:
        raise BaselineValidationError("provider channel has no valid live identity") from exc


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(root.rglob("*")):
        if child.is_file():
            digest.update(child.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(child.read_bytes())
    return digest.hexdigest()


def _app_config(raw: bytes, *, javascript: bool) -> Mapping[str, Any]:
    try:
        text = raw.decode("utf-8")
        if javascript:
            marker = "firebase.initializeApp("
            start = text.index(marker) + len(marker)
            value, _ = json.JSONDecoder().raw_decode(text[start:].lstrip())
        else:
            value = json.loads(text)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise BaselineValidationError("managed Firebase initialization resource is invalid") from exc
    if not isinstance(value, Mapping):
        raise BaselineValidationError("managed Firebase initialization resource is not an object")
    return value


def _app_identity(config: Mapping[str, Any], target: ProviderTarget) -> dict[str, str]:
    source_names = {
        "project_id": "projectId",
        "messaging_sender_id": "messagingSenderId",
        "auth_domain": "authDomain",
        "storage_bucket": "storageBucket",
    }
    identity: dict[str, str] = {}
    for destination, source in source_names.items():
        value = config.get(source)
        if not isinstance(value, str) or not value:
            raise BaselineValidationError(f"managed Firebase configuration is missing {source}")
        identity[destination] = value
    if identity["project_id"] != target.project:
        raise BaselineValidationError("managed Firebase project identity does not match the target")
    return identity


def _verify_evidence_root(
    root: Path,
    *,
    target: ProviderTarget,
    archive_sha256: str,
    evidence_archive: Path,
    materialize_to: Path | None,
    temporary: tempfile.TemporaryDirectory[str] | None = None,
) -> VerifiedBaseline:
    required = ("capture.json", "channel-before.json", "channel-after.json", "version.json", "inventory.json")
    if not root.is_dir() or any(not (root / name).is_file() for name in required):
        raise BaselineValidationError("baseline evidence is incomplete")
    if not _SHA256.fullmatch(archive_sha256):
        raise BaselineValidationError("trusted archive identity must be a SHA-256 digest")

    capture = _load_json(root / "capture.json")
    before_raw = _load_json(root / "channel-before.json")
    after_raw = _load_json(root / "channel-after.json")
    version = _load_json(root / "version.json")
    inventory = _load_json(root / "inventory.json")
    if not isinstance(capture, Mapping) or not isinstance(version, Mapping) or not isinstance(inventory, list):
        raise BaselineValidationError("baseline evidence has an invalid shape")
    if capture.get("schema_version") != 1 or capture.get("status") != "verified":
        raise BaselineValidationError("baseline capture is not a verified schema-1 capture")
    if capture.get("site") != target.site:
        raise BaselineValidationError("baseline capture targets another site")

    before = _channel_identity(before_raw, target)
    after = _channel_identity(after_raw, target)
    if before != after:
        raise BaselineValidationError("live provider identity changed during capture")
    live = capture.get("live")
    if not isinstance(live, Mapping) or live != before.to_dict(include_target=False):
        raise BaselineValidationError("capture identity does not match provider observations")
    if version.get("name") != before.version or version.get("status") != "FINALIZED":
        raise BaselineValidationError("captured provider version is not the observed finalized version")

    metadata_hashes = capture.get("metadata_sha256")
    if not isinstance(metadata_hashes, Mapping) or set(metadata_hashes) != {
        "channel-before.json", "channel-after.json", "version.json", "inventory.json"
    }:
        raise BaselineValidationError("capture metadata digest inventory is incomplete")
    for name, expected in metadata_hashes.items():
        if not isinstance(expected, str) or _sha256_file(root / name) != expected:
            raise BaselineValidationError(f"captured metadata digest mismatch: {name}")

    config_sha = _sha256_bytes(_canonical_provider_config(version.get("config")))
    if capture.get("config_sha256") != config_sha:
        raise BaselineValidationError("provider serving configuration was substituted")
    try:
        finalized_count = int(version.get("fileCount"))
    except (TypeError, ValueError) as exc:
        raise BaselineValidationError("finalized provider version has no valid file count") from exc
    files = capture.get("files")
    if not isinstance(files, list) or capture.get("file_count") != len(files) or finalized_count != len(files):
        raise BaselineValidationError("provider inventory is incomplete")
    if not isinstance(capture.get("total_bytes"), int):
        raise BaselineValidationError("capture total byte count is invalid")

    inventory_by_path: dict[str, Mapping[str, Any]] = {}
    for item in inventory:
        if not isinstance(item, Mapping):
            raise BaselineValidationError("provider inventory entry is invalid")
        relative = _safe_relative(item.get("relative"))
        if relative in inventory_by_path:
            raise BaselineValidationError("provider inventory contains a duplicate path")
        if item.get("path") != f"/{relative}" or item.get("status") != "ACTIVE" or not _SHA256.fullmatch(str(item.get("provider_sha256", ""))):
            raise BaselineValidationError("provider inventory entry is invalid")
        inventory_by_path[relative] = item
    if len(inventory_by_path) != len(files):
        raise BaselineValidationError("provider inventory count does not match captured files")

    captured_by_path: dict[str, Mapping[str, Any]] = {}
    total_bytes = 0
    managed_raw: dict[str, bytes] = {}
    for item in files:
        if not isinstance(item, Mapping):
            raise BaselineValidationError("captured file entry is invalid")
        relative = _safe_relative(item.get("relative"))
        if relative in captured_by_path or relative not in inventory_by_path:
            raise BaselineValidationError("captured file inventory is mixed or duplicated")
        inventory_item = inventory_by_path[relative]
        for name in ("path", "relative", "provider_sha256", "status"):
            if item.get(name) != inventory_item.get(name):
                raise BaselineValidationError("captured file disagrees with provider inventory")
        raw_path = root / "site" / relative
        payload_value = item.get("provider_payload")
        expected_payload = f"provider-payloads/{item.get('provider_sha256')}.gz"
        if payload_value != expected_payload:
            raise BaselineValidationError("captured file has an invalid provider payload binding")
        payload_path = root / expected_payload
        if not raw_path.is_file() or not payload_path.is_file():
            raise BaselineValidationError("captured raw or provider payload is missing")
        raw = raw_path.read_bytes()
        payload = payload_path.read_bytes()
        if _sha256_bytes(raw) != item.get("sha256") or _sha256_bytes(payload) != item.get("provider_sha256"):
            raise BaselineValidationError("captured raw or provider payload digest mismatch")
        try:
            decoded = gzip.decompress(payload)
        except (OSError, EOFError) as exc:
            raise BaselineValidationError("captured provider payload is not valid gzip") from exc
        if decoded != raw:
            raise BaselineValidationError("provider upload hash does not reconcile to raw content")
        if item.get("bytes") != len(raw):
            raise BaselineValidationError("captured raw byte count mismatch")
        total_bytes += len(raw)
        captured_by_path[relative] = item
        if relative.startswith("__/"):
            if relative not in _MANAGED_RELATIVES:
                raise BaselineValidationError("baseline contains an unexplained provider-managed resource")
            managed_raw[relative] = raw
    if set(captured_by_path) != set(inventory_by_path) or total_bytes != capture.get("total_bytes"):
        raise BaselineValidationError("captured inventory or total byte count is incomplete")
    actual_site_paths = {
        path.relative_to(root / "site").as_posix()
        for path in (root / "site").rglob("*") if path.is_file()
    }
    if actual_site_paths != set(captured_by_path):
        raise BaselineValidationError("archived site tree disagrees with the full provider inventory")
    if set(managed_raw) != _MANAGED_RELATIVES:
        raise BaselineValidationError("baseline is missing a managed Firebase initialization resource")

    json_config = _app_config(managed_raw["__/firebase/init.json"], javascript=False)
    js_config = _app_config(managed_raw["__/firebase/init.js"], javascript=True)
    json_identity = _app_identity(json_config, target)
    if _app_identity(js_config, target) != json_identity:
        raise BaselineValidationError("managed Firebase resources disagree on app identity")

    if materialize_to is None:
        materialize_to = root / ".application-site"
    if materialize_to.exists():
        raise BaselineValidationError("baseline application materialization target already exists")
    materialize_to.mkdir(parents=True)
    for relative in sorted(set(captured_by_path) - _MANAGED_RELATIVES):
        source = root / "site" / relative
        destination = materialize_to / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    source_status = capture.get("source_commit_status")
    source = SourceProvenance.from_value({
        "status": source_status,
        "commit": capture.get("source_commit"),
        "import_commit": None,
    })
    managed = tuple(
        ManagedResourceEvidence.from_value({
            "path": f"/{relative}",
            "raw_sha256": captured_by_path[relative]["sha256"],
            "provider_sha256": captured_by_path[relative]["provider_sha256"],
            "size": captured_by_path[relative]["bytes"],
            "app_identity": json_identity,
        })
        for relative in (
            MANAGED_RESOURCE_PATHS[0].removeprefix("/"),
            MANAGED_RESOURCE_PATHS[1].removeprefix("/"),
        )
    )
    record = BaselineRecord.from_dict({
        "schema_version": 1,
        "record_type": "baseline",
        "target": target.to_dict(),
        "observed": before.to_dict(include_target=False),
        "before": before.to_dict(include_target=False),
        "after": after.to_dict(include_target=False),
        "captured_at": capture.get("completed_at"),
        "inventory_sha256": metadata_hashes["inventory.json"],
        "configuration_sha256": config_sha,
        "archive_sha256": archive_sha256,
        "application_tree_sha256": _tree_digest(materialize_to),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "source": source.to_dict(),
        "managed_resources": [item.to_dict() for item in managed],
        "private_evidence_sha256": dict(metadata_hashes),
        "redaction_method": "allowlisted-provider-identities-and-digests-v1",
    })
    return VerifiedBaseline._create(record, materialize_to, evidence_archive, temporary)


def import_baseline(
    archive: str | Path,
    *,
    target: Mapping[str, Any] | ProviderTarget,
    expected_archive_sha256: str,
    materialize_to: str | Path | None = None,
) -> VerifiedBaseline:
    """Verify pinned archive bytes, then import a complete baseline.

    ``expected_archive_sha256`` must come from retained task evidence or another
    trusted source.  The capture manifest cannot assert its own archive identity.
    """

    archive_path = Path(archive)
    if not archive_path.is_file() or not _SHA256.fullmatch(expected_archive_sha256):
        raise BaselineValidationError("a pinned baseline archive and SHA-256 digest are required")
    actual_archive_sha256 = _sha256_file(archive_path)
    if actual_archive_sha256 != expected_archive_sha256:
        raise BaselineValidationError("baseline archive digest does not match trusted evidence")
    resolved_target = ProviderTarget.from_value(target)
    temporary = tempfile.TemporaryDirectory(prefix="sportsrank-baseline-evidence-")
    evidence_root = Path(temporary.name)
    try:
        _extract_archive(archive_path, evidence_root)
        verified = _verify_evidence_root(
            evidence_root,
            target=resolved_target,
            archive_sha256=actual_archive_sha256,
            evidence_archive=archive_path,
            materialize_to=Path(materialize_to) if materialize_to is not None else None,
            temporary=temporary,
        )
        if materialize_to is not None:
            result = VerifiedBaseline._create(
                verified.record,
                verified.application_site,
                archive_path,
            )
            temporary.cleanup()
            return result
        return verified
    except BaseException:
        temporary.cleanup()
        raise
