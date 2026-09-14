"""Strict, versioned logical records shared by recovery and publication.

The records in this module contain identities and content digests only.  They
never infer deployment or verification state from workflow success, and they
never carry provider credentials or private provider payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_POSITIVE_ID = re.compile(r"[1-9][0-9]*\Z")


class RecordValidationError(ValueError):
    """A logical publication record is incomplete, malformed, or unlinked."""


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def record_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RecordValidationError(f"{name} must be an object")
    return value


def _exact(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RecordValidationError(
            f"{name} fields do not match schema; missing={missing}, extra={extra}"
        )


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecordValidationError(f"{name} must be a non-empty string")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if not _SHA256.fullmatch(value):
        raise RecordValidationError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _version(value: Mapping[str, Any], record_type: str) -> None:
    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != SCHEMA_VERSION:
        raise RecordValidationError(f"unsupported {record_type} schema_version")
    if value.get("record_type") != record_type:
        raise RecordValidationError(f"expected record_type {record_type}")


@dataclass(frozen=True)
class ProviderTarget:
    project: str
    site: str
    channel: str

    def __post_init__(self) -> None:
        for name in ("project", "site"):
            value = _text(getattr(self, name), f"target.{name}")
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", value):
                raise RecordValidationError(f"target.{name} is not a canonical Firebase identifier")
        _text(self.channel, "target.channel")
        if self.channel != "live":
            raise RecordValidationError("the baseline target channel must be live")

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | "ProviderTarget") -> "ProviderTarget":
        if isinstance(value, cls):
            return value
        value = _object(value, "target")
        _exact(value, {"project", "site", "channel"}, "target")
        target = cls(*(_text(value[name], f"target.{name}") for name in ("project", "site", "channel")))
        return target

    def to_dict(self) -> dict[str, str]:
        return {"project": self.project, "site": self.site, "channel": self.channel}


@dataclass(frozen=True)
class ProviderIdentity:
    target: ProviderTarget
    release: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, ProviderTarget):
            raise RecordValidationError("provider identity target is invalid")
        release = re.escape(f"sites/{self.target.site}/channels/{self.target.channel}/releases/")
        version = re.escape(f"sites/{self.target.site}/versions/")
        resource_id = r"[A-Za-z0-9_-]+"
        if not re.fullmatch(release + resource_id, self.release):
            raise RecordValidationError("provider release identity is invalid for the target channel")
        if not re.fullmatch(version + resource_id, self.version):
            raise RecordValidationError("provider version identity is invalid for the target site")

    @classmethod
    def from_value(
        cls, value: Mapping[str, Any] | "ProviderIdentity", *, target: ProviderTarget | None = None
    ) -> "ProviderIdentity":
        if isinstance(value, cls):
            result = value
        else:
            value = _object(value, "provider identity")
            expected = {"target", "release", "version"} if "target" in value else {"release", "version"}
            _exact(value, expected, "provider identity")
            resolved_target = ProviderTarget.from_value(value["target"]) if "target" in value else target
            if resolved_target is None:
                raise RecordValidationError("provider identity target is required")
            result = cls(
                resolved_target,
                _text(value["release"], "identity.release"),
                _text(value["version"], "identity.version"),
            )
        if target is not None and result.target != target:
            raise RecordValidationError("provider identity target does not match")
        return result

    def to_dict(self, *, include_target: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {"release": self.release, "version": self.version}
        if include_target:
            result["target"] = self.target.to_dict()
        return result


@dataclass(frozen=True)
class SourceProvenance:
    status: str
    commit: str | None
    import_commit: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"known", "unknown"}:
            raise RecordValidationError("source.status must be known or unknown")
        if self.status == "unknown" and self.commit is not None:
            raise RecordValidationError("unknown historical source cannot claim a commit")
        if self.status == "known" and (
            not isinstance(self.commit, str) or not _COMMIT.fullmatch(self.commit)
        ):
            raise RecordValidationError("known historical source requires a commit SHA")
        if self.import_commit is not None and (
            not isinstance(self.import_commit, str) or not _COMMIT.fullmatch(self.import_commit)
        ):
            raise RecordValidationError("import_commit must be a commit SHA or null")

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | "SourceProvenance") -> "SourceProvenance":
        if isinstance(value, cls):
            return value
        value = _object(value, "source")
        _exact(value, {"status", "commit", "import_commit"}, "source")
        result = cls(str(value["status"]), value["commit"], value["import_commit"])
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "commit": self.commit, "import_commit": self.import_commit}


@dataclass(frozen=True)
class ManagedResourceEvidence:
    path: str
    raw_sha256: str
    provider_sha256: str
    size: int
    app_identity: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "app_identity", MappingProxyType(dict(self.app_identity)))
        if self.path not in {"/__/firebase/init.js", "/__/firebase/init.json"}:
            raise RecordValidationError("managed resource path is not an accepted initialization resource")
        _digest(self.raw_sha256, "managed resource raw_sha256")
        _digest(self.provider_sha256, "managed resource provider_sha256")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise RecordValidationError("managed resource size must be non-negative")
        identity = _object(self.app_identity, "managed resource app_identity")
        _exact(identity, {"project_id", "messaging_sender_id", "auth_domain", "storage_bucket"}, "managed resource app_identity")
        for name, value in identity.items():
            _text(value, f"app_identity.{name}")

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | "ManagedResourceEvidence") -> "ManagedResourceEvidence":
        if isinstance(value, cls):
            return value
        value = _object(value, "managed resource")
        _exact(value, {"path", "raw_sha256", "provider_sha256", "size", "app_identity"}, "managed resource")
        identity = _object(value["app_identity"], "managed resource app_identity")
        _exact(identity, {"project_id", "messaging_sender_id", "auth_domain", "storage_bucket"}, "managed resource app_identity")
        if isinstance(value["size"], bool) or not isinstance(value["size"], int) or value["size"] < 0:
            raise RecordValidationError("managed resource size must be non-negative")
        return cls(
            _text(value["path"], "managed resource path"),
            _digest(value["raw_sha256"], "managed resource raw_sha256"),
            _digest(value["provider_sha256"], "managed resource provider_sha256"),
            value["size"],
            MappingProxyType({name: _text(identity[name], f"app_identity.{name}") for name in identity}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "raw_sha256": self.raw_sha256,
            "provider_sha256": self.provider_sha256,
            "size": self.size,
            "app_identity": dict(self.app_identity),
        }


@dataclass(frozen=True)
class BaselineRecord:
    target: ProviderTarget
    observed: ProviderIdentity
    before: ProviderIdentity
    after: ProviderIdentity
    captured_at: str
    inventory_sha256: str
    configuration_sha256: str
    archive_sha256: str
    application_tree_sha256: str
    file_count: int
    total_bytes: int
    source: SourceProvenance
    managed_resources: tuple[ManagedResourceEvidence, ...]
    private_evidence_sha256: Mapping[str, str]
    redaction_method: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "managed_resources", tuple(self.managed_resources))
        object.__setattr__(
            self,
            "private_evidence_sha256",
            MappingProxyType(dict(self.private_evidence_sha256)),
        )
        if not isinstance(self.target, ProviderTarget):
            raise RecordValidationError("baseline target is invalid")
        for name in ("observed", "before", "after"):
            identity = getattr(self, name)
            if not isinstance(identity, ProviderIdentity) or identity.target != self.target:
                raise RecordValidationError(f"baseline {name} identity is invalid")
        if len({(value.release, value.version) for value in (self.observed, self.before, self.after)}) != 1:
            raise RecordValidationError("baseline observations are not stable")
        for name in ("inventory_sha256", "configuration_sha256", "archive_sha256", "application_tree_sha256"):
            _digest(getattr(self, name), name)
        for name in ("file_count", "total_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RecordValidationError(f"baseline {name} must be non-negative")
        if not isinstance(self.source, SourceProvenance):
            raise RecordValidationError("baseline source provenance is invalid")
        if any(not isinstance(item, ManagedResourceEvidence) for item in self.managed_resources) or tuple(item.path for item in self.managed_resources) != ("/__/firebase/init.js", "/__/firebase/init.json"):
            raise RecordValidationError("baseline must contain exactly the two managed initialization resources")
        if not self.private_evidence_sha256:
            raise RecordValidationError("private evidence source hashes are required")
        for name, value in self.private_evidence_sha256.items():
            _digest(value, f"private evidence {name}")
        _text(self.captured_at, "captured_at")
        _text(self.redaction_method, "redaction_method")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BaselineRecord":
        raw = _object(raw, "baseline record")
        fields = {
            "schema_version", "record_type", "target", "observed", "before", "after",
            "captured_at", "inventory_sha256", "configuration_sha256", "archive_sha256",
            "application_tree_sha256", "file_count", "total_bytes", "source",
            "managed_resources", "private_evidence_sha256", "redaction_method",
        }
        _exact(raw, fields, "baseline record")
        _version(raw, "baseline")
        target = ProviderTarget.from_value(raw["target"])
        identities = [ProviderIdentity.from_value(raw[name], target=target) for name in ("observed", "before", "after")]
        if len(set((item.release, item.version) for item in identities)) != 1:
            raise RecordValidationError("baseline observations are not stable")
        counts = []
        for name in ("file_count", "total_bytes"):
            value = raw[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RecordValidationError(f"baseline {name} must be non-negative")
            counts.append(value)
        managed_value = raw["managed_resources"]
        if not isinstance(managed_value, list):
            raise RecordValidationError("managed_resources must be an array")
        managed = tuple(ManagedResourceEvidence.from_value(value) for value in managed_value)
        if tuple(item.path for item in managed) != ("/__/firebase/init.js", "/__/firebase/init.json"):
            raise RecordValidationError("baseline must contain exactly the two managed initialization resources")
        private = _object(raw["private_evidence_sha256"], "private_evidence_sha256")
        if not private:
            raise RecordValidationError("private evidence source hashes are required")
        return cls(
            target, *identities, _text(raw["captured_at"], "captured_at"),
            _digest(raw["inventory_sha256"], "inventory_sha256"),
            _digest(raw["configuration_sha256"], "configuration_sha256"),
            _digest(raw["archive_sha256"], "archive_sha256"),
            _digest(raw["application_tree_sha256"], "application_tree_sha256"),
            counts[0], counts[1], SourceProvenance.from_value(raw["source"]), managed,
            MappingProxyType({str(name): _digest(value, f"private evidence {name}") for name, value in private.items()}),
            _text(raw["redaction_method"], "redaction_method"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": "baseline",
            "target": self.target.to_dict(),
            "observed": self.observed.to_dict(include_target=False),
            "before": self.before.to_dict(include_target=False),
            "after": self.after.to_dict(include_target=False),
            "captured_at": self.captured_at,
            "inventory_sha256": self.inventory_sha256,
            "configuration_sha256": self.configuration_sha256,
            "archive_sha256": self.archive_sha256,
            "application_tree_sha256": self.application_tree_sha256,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "source": self.source.to_dict(),
            "managed_resources": [value.to_dict() for value in self.managed_resources],
            "private_evidence_sha256": dict(self.private_evidence_sha256),
            "redaction_method": self.redaction_method,
        }

    @property
    def digest(self) -> str:
        return record_digest(self.to_dict())


@dataclass(frozen=True)
class ArchiveReference:
    """Stable identity of one asset in a published immutable GitHub release."""

    repository: str
    release_id: str
    tag: str
    target_commit: str
    asset_id: str
    asset_name: str
    sha256: str
    size: int
    immutable: bool

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise RecordValidationError("archive repository must be OWNER/REPOSITORY")
        for name in ("release_id", "tag", "asset_id", "asset_name"):
            _text(getattr(self, name), f"archive.{name}")
        for name in ("release_id", "asset_id"):
            if not isinstance(getattr(self, name), str) or not _POSITIVE_ID.fullmatch(
                getattr(self, name)
            ):
                raise RecordValidationError(
                    f"archive.{name} must be a positive numeric identity"
                )
        if not _COMMIT.fullmatch(self.target_commit):
            raise RecordValidationError("archive.target_commit must be an immutable commit SHA")
        if "/" in self.asset_name or self.asset_name in {".", ".."}:
            raise RecordValidationError("archive asset_name must be a file name")
        _digest(self.sha256, "archive.sha256")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise RecordValidationError("archive.size must be a non-negative integer")
        if not isinstance(self.immutable, bool) or not self.immutable:
            raise RecordValidationError("archive release must be immutable")

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | "ArchiveReference") -> "ArchiveReference":
        if isinstance(value, cls):
            return cls.from_dict(value.to_dict())
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ArchiveReference":
        raw = _object(raw, "archive reference")
        names = {"repository", "release_id", "tag", "target_commit", "asset_id", "asset_name", "sha256", "size", "immutable"}
        _exact(raw, {"schema_version", "record_type", *names}, "archive reference")
        _version(raw, "archive_reference")
        size = raw["size"]
        immutable = raw["immutable"]
        if isinstance(size, bool) or not isinstance(size, int):
            raise RecordValidationError("archive.size must be a non-negative integer")
        if not isinstance(immutable, bool):
            raise RecordValidationError("archive.immutable must be a boolean")
        return cls(
            *(_text(raw[name], f"archive.{name}") for name in ("repository", "release_id", "tag", "target_commit", "asset_id", "asset_name")),
            _digest(raw["sha256"], "archive.sha256"), size, immutable,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "record_type": "archive_reference", **{name: getattr(self, name) for name in self.__dataclass_fields__}}

    @property
    def digest(self) -> str:
        return record_digest(self.to_dict())


@dataclass(frozen=True)
class SanitizedBaselineArchiveRecord:
    source_baseline_record_sha256: str
    source_archive_sha256: str
    derivative_archive_sha256: str
    source_metadata_sha256: Mapping[str, str]
    derivative_metadata_sha256: Mapping[str, str]
    redaction_method: str
    removed_json_paths: tuple[str, ...]
    file_count: int
    total_bytes: int
    target: ProviderTarget
    observed: ProviderIdentity

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_metadata_sha256", MappingProxyType(dict(self.source_metadata_sha256)))
        object.__setattr__(self, "derivative_metadata_sha256", MappingProxyType(dict(self.derivative_metadata_sha256)))
        object.__setattr__(self, "removed_json_paths", tuple(self.removed_json_paths))
        for name in ("source_baseline_record_sha256", "source_archive_sha256", "derivative_archive_sha256"):
            _digest(getattr(self, name), name)
        for group_name in ("source_metadata_sha256", "derivative_metadata_sha256"):
            group = _object(getattr(self, group_name), group_name)
            if not group:
                raise RecordValidationError(f"{group_name} is required")
            for name, value in group.items():
                _text(name, f"{group_name} name")
                _digest(value, f"{group_name}.{name}")
        _text(self.redaction_method, "redaction_method")
        if not self.removed_json_paths or any(not isinstance(v, str) or not v.startswith("/") for v in self.removed_json_paths):
            raise RecordValidationError("removed_json_paths must identify removed fields")
        for name in ("file_count", "total_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RecordValidationError(f"{name} must be a non-negative integer")
        if not isinstance(self.target, ProviderTarget) or not isinstance(self.observed, ProviderIdentity) or self.observed.target != self.target:
            raise RecordValidationError("sanitized baseline provider identity is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "record_type": "sanitized_baseline_archive",
            "source_baseline_record_sha256": self.source_baseline_record_sha256,
            "source_archive_sha256": self.source_archive_sha256,
            "derivative_archive_sha256": self.derivative_archive_sha256,
            "source_metadata_sha256": dict(self.source_metadata_sha256),
            "derivative_metadata_sha256": dict(self.derivative_metadata_sha256),
            "redaction_method": self.redaction_method,
            "removed_json_paths": list(self.removed_json_paths), "file_count": self.file_count,
            "total_bytes": self.total_bytes, "target": self.target.to_dict(),
            "observed": self.observed.to_dict(include_target=False),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SanitizedBaselineArchiveRecord":
        raw = _object(raw, "sanitized baseline archive record")
        names = {"source_baseline_record_sha256", "source_archive_sha256", "derivative_archive_sha256", "source_metadata_sha256", "derivative_metadata_sha256", "redaction_method", "removed_json_paths", "file_count", "total_bytes", "target", "observed"}
        _exact(raw, {"schema_version", "record_type", *names}, "sanitized baseline archive record")
        _version(raw, "sanitized_baseline_archive")
        removed = raw["removed_json_paths"]
        if not isinstance(removed, list):
            raise RecordValidationError("removed_json_paths must be an array")
        target = ProviderTarget.from_value(raw["target"])
        return cls(
            *(_digest(raw[n], n) for n in ("source_baseline_record_sha256", "source_archive_sha256", "derivative_archive_sha256")),
            _object(raw["source_metadata_sha256"], "source_metadata_sha256"),
            _object(raw["derivative_metadata_sha256"], "derivative_metadata_sha256"),
            _text(raw["redaction_method"], "redaction_method"), tuple(removed), raw["file_count"], raw["total_bytes"],
            target, ProviderIdentity.from_value(raw["observed"], target=target),
        )

    @property
    def digest(self) -> str:
        return record_digest(self.to_dict())


@dataclass(frozen=True)
class ValidatedPackageRecord:
    candidate_commit: str
    bundle_sha256: str
    inventory_sha256: str
    configuration_sha256: str
    expected_baseline_sha256: str
    retained_inputs_sha256: str
    validation_sha256: str
    expected_predecessor: ProviderIdentity

    def __post_init__(self) -> None:
        if not _COMMIT.fullmatch(self.candidate_commit):
            raise RecordValidationError("candidate_commit must be an immutable commit SHA")
        for name in (
            "bundle_sha256", "inventory_sha256", "configuration_sha256",
            "expected_baseline_sha256", "retained_inputs_sha256", "validation_sha256",
        ):
            _digest(getattr(self, name), name)
        if not isinstance(self.expected_predecessor, ProviderIdentity):
            raise RecordValidationError("expected_predecessor must be a provider identity")

    @classmethod
    def create(cls, **values: str) -> "ValidatedPackageRecord":
        return cls.from_dict({"schema_version": SCHEMA_VERSION, "record_type": "validated_package", **values})

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ValidatedPackageRecord":
        raw = _object(raw, "validated package record")
        names = {"candidate_commit", "bundle_sha256", "inventory_sha256", "configuration_sha256", "expected_baseline_sha256", "retained_inputs_sha256", "validation_sha256", "expected_predecessor"}
        _exact(raw, {"schema_version", "record_type", *names}, "validated package record")
        _version(raw, "validated_package")
        commit = _text(raw["candidate_commit"], "candidate_commit")
        if not _COMMIT.fullmatch(commit):
            raise RecordValidationError("candidate_commit must be an immutable commit SHA")
        return cls(commit, *(_digest(raw[name], name) for name in (
            "bundle_sha256", "inventory_sha256", "configuration_sha256",
            "expected_baseline_sha256", "retained_inputs_sha256", "validation_sha256")),
            ProviderIdentity.from_value(raw["expected_predecessor"]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "record_type": "validated_package",
            "candidate_commit": self.candidate_commit, "bundle_sha256": self.bundle_sha256,
            "inventory_sha256": self.inventory_sha256, "configuration_sha256": self.configuration_sha256,
            "expected_baseline_sha256": self.expected_baseline_sha256,
            "retained_inputs_sha256": self.retained_inputs_sha256,
            "validation_sha256": self.validation_sha256,
            "expected_predecessor": self.expected_predecessor.to_dict(),
        }

    @property
    def digest(self) -> str:
        return record_digest(self.to_dict())


@dataclass(frozen=True)
class AttemptIntentRecord:
    attempt_id: str
    purpose: str
    package_sha256: str
    expected_predecessor: ProviderIdentity
    artifact_reference: ArchiveReference
    evidence_references: Mapping[str, ArchiveReference]
    protected_context: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "protected_context", MappingProxyType(dict(self.protected_context))
        )
        _text(self.attempt_id, "attempt_id")
        if self.purpose not in {"normal", "rollback", "correction"}:
            raise RecordValidationError("attempt purpose is invalid")
        _digest(self.package_sha256, "package_sha256")
        if not isinstance(self.expected_predecessor, ProviderIdentity):
            raise RecordValidationError("expected predecessor identity is invalid")
        if not isinstance(self.artifact_reference, ArchiveReference):
            raise RecordValidationError("artifact reference must be an immutable archive reference")
        evidence = _object(self.evidence_references, "evidence_references")
        required_evidence = {"baseline", "source_inputs", "original_prepared"}
        if set(evidence) != required_evidence:
            raise RecordValidationError("permanent baseline, source-input, and original-prepared references are required")
        object.__setattr__(self, "evidence_references", MappingProxyType({name: ArchiveReference.from_value(value) for name, value in evidence.items()}))
        evidence_identities = {
            (reference.release_id, reference.asset_id)
            for reference in self.evidence_references.values()
        }
        artifact_identity = (
            self.artifact_reference.release_id,
            self.artifact_reference.asset_id,
        )
        if len(evidence_identities) != len(required_evidence) or artifact_identity in evidence_identities:
            raise RecordValidationError("permanent evidence roles must use distinct archive assets")
        if not self.protected_context:
            raise RecordValidationError("protected execution context is required")
        required_context = {
            "repository", "workflow_ref", "workflow_sha", "run_id",
            "run_attempt", "environment", "event", "ref", "head_sha",
            "approval_state", "approver_login", "approver_id",
        }
        if set(self.protected_context) != required_context:
            raise RecordValidationError("protected execution context fields do not match the runtime evidence schema")
        for name, value in self.protected_context.items():
            _text(str(name), "protected_context field")
            _text(value, f"protected_context.{name}")
        repository = self.protected_context["repository"]
        if repository != self.artifact_reference.repository or any(
            reference.repository != repository
            for reference in self.evidence_references.values()
        ):
            raise RecordValidationError("all immutable evidence must belong to the protected repository")
        workflow_ref = self.protected_context["workflow_ref"]
        workflow_prefix = f"{repository}/.github/workflows/"
        if (
            not workflow_ref.startswith(workflow_prefix)
            or not workflow_ref.endswith("@refs/heads/main")
            or ".." in workflow_ref
        ):
            raise RecordValidationError("protected workflow_ref must identify a main-branch workflow")
        if (
            not _COMMIT.fullmatch(self.protected_context["workflow_sha"])
            or self.protected_context["head_sha"]
            != self.protected_context["workflow_sha"]
        ):
            raise RecordValidationError(
                "protected workflow/head SHA must identify one immutable commit"
            )
        for name in ("run_id", "run_attempt"):
            if not self.protected_context[name].isdigit() or int(self.protected_context[name]) < 1:
                raise RecordValidationError(f"protected {name} must be a positive integer")
        if self.protected_context["environment"] != "production":
            raise RecordValidationError("protected environment must be production")
        if (
            self.protected_context["event"] != "workflow_dispatch"
            or self.protected_context["ref"] != "refs/heads/main"
            or self.protected_context["run_attempt"] != "1"
            or self.protected_context["approval_state"] != "approved"
            or self.protected_context["approver_login"] != "arkar16"
            or self.protected_context["approver_id"] != "18407890"
        ):
            raise RecordValidationError(
                "protected execution is not the owner-approved first main run"
            )

    @classmethod
    def create(cls, *, package: ValidatedPackageRecord, **values: Any) -> "AttemptIntentRecord":
        reference = ArchiveReference.from_value(values.get("artifact_reference"))
        if reference.sha256 != package.bundle_sha256:
            raise RecordValidationError("artifact reference does not bind the validated package bytes")
        if reference.target_commit != package.candidate_commit:
            raise RecordValidationError("artifact reference does not bind the validated candidate commit")
        values["artifact_reference"] = reference.to_dict()
        return cls.from_dict({"schema_version": SCHEMA_VERSION, "record_type": "attempt_intent", "package_sha256": package.digest, **values}, package=package)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, package: ValidatedPackageRecord | None = None) -> "AttemptIntentRecord":
        raw = _object(raw, "attempt intent record")
        names = {"attempt_id", "purpose", "package_sha256", "expected_predecessor", "artifact_reference", "evidence_references", "protected_context"}
        _exact(raw, {"schema_version", "record_type", *names}, "attempt intent record")
        _version(raw, "attempt_intent")
        purpose = raw["purpose"]
        if purpose not in {"normal", "rollback", "correction"}:
            raise RecordValidationError("attempt purpose is invalid")
        package_sha = _digest(raw["package_sha256"], "package_sha256")
        if package is not None and package_sha != package.digest:
            raise RecordValidationError("attempt does not link to the supplied package")
        predecessor = ProviderIdentity.from_value(
            _object(raw["expected_predecessor"], "expected_predecessor")
        )
        if package is not None and predecessor != package.expected_predecessor:
            raise RecordValidationError("attempt predecessor does not match the validated package")
        context = _object(raw["protected_context"], "protected_context")
        if not context:
            raise RecordValidationError("protected execution context is required")
        evidence = raw["evidence_references"]
        if not isinstance(evidence, Mapping):
            raise RecordValidationError("evidence_references must be an object")
        artifact = ArchiveReference.from_value(raw["artifact_reference"])
        if package is not None and artifact.sha256 != package.bundle_sha256:
            raise RecordValidationError("artifact reference does not bind the validated package bytes")
        if package is not None and artifact.target_commit != package.candidate_commit:
            raise RecordValidationError("artifact reference does not bind the validated candidate commit")
        return cls(
            _text(raw["attempt_id"], "attempt_id"), purpose, package_sha,
            predecessor,
            artifact,
            MappingProxyType({str(name): ArchiveReference.from_value(v) for name, v in evidence.items()}),
            MappingProxyType({str(name): _text(value, f"protected_context.{name}") for name, value in context.items()}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "record_type": "attempt_intent", "attempt_id": self.attempt_id, "purpose": self.purpose, "package_sha256": self.package_sha256, "expected_predecessor": self.expected_predecessor.to_dict(), "artifact_reference": self.artifact_reference.to_dict(), "evidence_references": {name: value.to_dict() for name, value in self.evidence_references.items()}, "protected_context": dict(self.protected_context)}

    @property
    def digest(self) -> str:
        return record_digest(self.to_dict())


@dataclass(frozen=True)
class ProviderResultRecord:
    attempt_id: str
    intent_sha256: str
    observed_target: ProviderTarget
    observed_release: str | None
    observed_version: str | None
    outcome: str
    observed_at: str
    source_sha256: str
    redaction_method: str

    def __post_init__(self) -> None:
        _text(self.attempt_id, "attempt_id")
        _digest(self.intent_sha256, "intent_sha256")
        if not isinstance(self.observed_target, ProviderTarget):
            raise RecordValidationError("provider result target is invalid")
        if self.outcome not in {"accepted", "rejected", "unknown"}:
            raise RecordValidationError("provider outcome is invalid")
        if (self.observed_release is None) != (self.observed_version is None):
            raise RecordValidationError("observed release and version must both be known or unknown")
        if self.outcome == "accepted" and self.observed_release is None:
            raise RecordValidationError("accepted provider outcome requires an observed identity")
        if self.observed_release is not None:
            ProviderIdentity(self.observed_target, self.observed_release, self.observed_version)  # type: ignore[arg-type]
        _text(self.observed_at, "observed_at")
        _digest(self.source_sha256, "source_sha256")
        _text(self.redaction_method, "redaction_method")

    @classmethod
    def create(cls, *, intent: AttemptIntentRecord, outcome: str, **values: Any) -> "ProviderResultRecord":
        return cls.from_dict({"schema_version": SCHEMA_VERSION, "record_type": "provider_result", "attempt_id": intent.attempt_id, "intent_sha256": intent.digest, "outcome": outcome, "redaction_method": "allowlisted-fields-v1", **values}, intent=intent)

    @classmethod
    def create_unknown(cls, *, intent: AttemptIntentRecord, **values: Any) -> "ProviderResultRecord":
        return cls.create(intent=intent, outcome="unknown", **values)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, intent: AttemptIntentRecord | None = None) -> "ProviderResultRecord":
        raw = _object(raw, "provider result record")
        names = {"attempt_id", "intent_sha256", "observed_target", "observed_release", "observed_version", "outcome", "observed_at", "source_sha256", "redaction_method"}
        _exact(raw, {"schema_version", "record_type", *names}, "provider result record")
        _version(raw, "provider_result")
        outcome = raw["outcome"]
        if outcome not in {"accepted", "rejected", "unknown"}:
            raise RecordValidationError("provider outcome is invalid")
        attempt_id = _text(raw["attempt_id"], "attempt_id")
        intent_sha = _digest(raw["intent_sha256"], "intent_sha256")
        if intent is not None and (attempt_id != intent.attempt_id or intent_sha != intent.digest):
            raise RecordValidationError("provider result does not link to the supplied intent")
        observed_target = ProviderTarget.from_value(raw["observed_target"])
        if intent is not None and observed_target != intent.expected_predecessor.target:
            raise RecordValidationError("provider result target does not match the attempt target")
        release, version = raw["observed_release"], raw["observed_version"]
        if (release is None) != (version is None):
            raise RecordValidationError("observed release and version must both be known or unknown")
        if outcome == "accepted" and release is None:
            raise RecordValidationError("accepted provider outcome requires an observed identity")
        if release is not None:
            release, version = _text(release, "observed_release"), _text(version, "observed_version")
        return cls(attempt_id, intent_sha, observed_target, release, version, outcome, _text(raw["observed_at"], "observed_at"), _digest(raw["source_sha256"], "source_sha256"), _text(raw["redaction_method"], "redaction_method"))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "record_type": "provider_result", "attempt_id": self.attempt_id, "intent_sha256": self.intent_sha256, "observed_target": self.observed_target.to_dict(), "observed_release": self.observed_release, "observed_version": self.observed_version, "outcome": self.outcome, "observed_at": self.observed_at, "source_sha256": self.source_sha256, "redaction_method": self.redaction_method}

    @property
    def digest(self) -> str:
        return record_digest(self.to_dict())


@dataclass(frozen=True)
class VerificationRecord:
    attempt_id: str
    intent_sha256: str
    provider_result_sha256: str
    observed_release: str | None
    observed_version: str | None
    inventory_sha256: str | None
    configuration_sha256: str | None
    managed_resource_findings: Mapping[str, str]
    public_page_findings: Mapping[str, str]
    outcome: str
    observed_at: str
    findings: tuple[str, ...]
    source_sha256: str
    redaction_method: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "managed_resource_findings",
            MappingProxyType(dict(self.managed_resource_findings)),
        )
        object.__setattr__(
            self,
            "public_page_findings",
            MappingProxyType(dict(self.public_page_findings)),
        )
        object.__setattr__(self, "findings", tuple(self.findings))
        _text(self.attempt_id, "attempt_id")
        _digest(self.intent_sha256, "intent_sha256")
        _digest(self.provider_result_sha256, "provider_result_sha256")
        if (self.observed_release is None) != (self.observed_version is None):
            raise RecordValidationError("verification observed identity is partial")
        if self.outcome not in {"verified", "failed", "unknown"}:
            raise RecordValidationError("verification outcome is invalid")
        for name in ("inventory_sha256", "configuration_sha256"):
            value = getattr(self, name)
            if value is not None:
                _digest(value, name)
        if self.outcome == "verified":
            if self.observed_release is None or self.inventory_sha256 is None or self.configuration_sha256 is None:
                raise RecordValidationError("verified outcome requires observed identity and content/configuration digests")
            if set(self.managed_resource_findings) != {"/__/firebase/init.js", "/__/firebase/init.json"}:
                raise RecordValidationError("verified outcome requires both managed-resource findings")
            if not self.public_page_findings:
                raise RecordValidationError("verified outcome requires public-page findings")
        for group_name in ("managed_resource_findings", "public_page_findings"):
            group = _object(getattr(self, group_name), group_name)
            for path, finding in group.items():
                if not isinstance(path, str) or not path.startswith("/"):
                    raise RecordValidationError(f"{group_name} contains an invalid public path")
                _text(finding, f"{group_name}.{path}")
        if not self.findings or any(not isinstance(value, str) or not value for value in self.findings):
            raise RecordValidationError("verification findings must be non-empty")
        _text(self.observed_at, "observed_at")
        _digest(self.source_sha256, "source_sha256")
        _text(self.redaction_method, "redaction_method")

    @classmethod
    def create(
        cls,
        *,
        intent: AttemptIntentRecord,
        provider_result: ProviderResultRecord,
        outcome: str,
        **values: Any,
    ) -> "VerificationRecord":
        values["findings"] = list(values.get("findings", ()))
        values["managed_resource_findings"] = dict(values.get("managed_resource_findings", {}))
        values["public_page_findings"] = dict(values.get("public_page_findings", {}))
        values.setdefault("inventory_sha256", None)
        values.setdefault("configuration_sha256", None)
        values.setdefault("redaction_method", "allowlisted-findings-v1")
        return cls.from_dict({"schema_version": SCHEMA_VERSION, "record_type": "verification", "attempt_id": intent.attempt_id, "intent_sha256": intent.digest, "provider_result_sha256": provider_result.digest, "observed_release": provider_result.observed_release, "observed_version": provider_result.observed_version, "outcome": outcome, **values}, intent=intent, provider_result=provider_result)

    @classmethod
    def create_unknown(cls, *, intent: AttemptIntentRecord, provider_result: ProviderResultRecord, **values: Any) -> "VerificationRecord":
        return cls.create(intent=intent, provider_result=provider_result, outcome="unknown", **values)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, intent: AttemptIntentRecord | None = None, provider_result: ProviderResultRecord | None = None) -> "VerificationRecord":
        raw = _object(raw, "verification record")
        names = {"attempt_id", "intent_sha256", "provider_result_sha256", "observed_release", "observed_version", "inventory_sha256", "configuration_sha256", "managed_resource_findings", "public_page_findings", "outcome", "observed_at", "findings", "source_sha256", "redaction_method"}
        _exact(raw, {"schema_version", "record_type", *names}, "verification record")
        _version(raw, "verification")
        outcome = raw["outcome"]
        if outcome not in {"verified", "failed", "unknown"}:
            raise RecordValidationError("verification outcome is invalid")
        attempt_id = _text(raw["attempt_id"], "attempt_id")
        intent_sha = _digest(raw["intent_sha256"], "intent_sha256")
        provider_sha = _digest(raw["provider_result_sha256"], "provider_result_sha256")
        if intent is not None and (attempt_id != intent.attempt_id or intent_sha != intent.digest):
            raise RecordValidationError("verification does not link to the supplied intent")
        if provider_result is not None and provider_sha != provider_result.digest:
            raise RecordValidationError("verification does not link to the supplied provider result")
        if provider_result is not None and (
            attempt_id != provider_result.attempt_id
            or intent_sha != provider_result.intent_sha256
        ):
            raise RecordValidationError(
                "provider result does not belong to the supplied verification intent"
            )
        findings = raw["findings"]
        if not isinstance(findings, list) or not findings or any(not isinstance(value, str) or not value for value in findings):
            raise RecordValidationError("verification findings must be a non-empty string array")
        release, version = raw["observed_release"], raw["observed_version"]
        if (release is None) != (version is None):
            raise RecordValidationError("verification observed identity is partial")
        if provider_result is not None and (
            release != provider_result.observed_release or version != provider_result.observed_version
        ):
            raise RecordValidationError("verification observed identity disagrees with the provider result")
        inventory_sha = raw["inventory_sha256"]
        configuration_sha = raw["configuration_sha256"]
        if inventory_sha is not None:
            inventory_sha = _digest(inventory_sha, "inventory_sha256")
        if configuration_sha is not None:
            configuration_sha = _digest(configuration_sha, "configuration_sha256")
        managed = _object(raw["managed_resource_findings"], "managed_resource_findings")
        public = _object(raw["public_page_findings"], "public_page_findings")
        return cls(
            attempt_id, intent_sha, provider_sha, release, version,
            inventory_sha, configuration_sha,
            MappingProxyType({str(path): _text(value, f"managed_resource_findings.{path}") for path, value in managed.items()}),
            MappingProxyType({str(path): _text(value, f"public_page_findings.{path}") for path, value in public.items()}),
            outcome, _text(raw["observed_at"], "observed_at"), tuple(findings),
            _digest(raw["source_sha256"], "source_sha256"),
            _text(raw["redaction_method"], "redaction_method"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "record_type": "verification", "attempt_id": self.attempt_id, "intent_sha256": self.intent_sha256, "provider_result_sha256": self.provider_result_sha256, "observed_release": self.observed_release, "observed_version": self.observed_version, "inventory_sha256": self.inventory_sha256, "configuration_sha256": self.configuration_sha256, "managed_resource_findings": dict(self.managed_resource_findings), "public_page_findings": dict(self.public_page_findings), "outcome": self.outcome, "observed_at": self.observed_at, "findings": list(self.findings), "source_sha256": self.source_sha256, "redaction_method": self.redaction_method}

    @property
    def digest(self) -> str:
        return record_digest(self.to_dict())
