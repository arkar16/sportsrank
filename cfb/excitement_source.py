"""Credential-safe source qualification for ADR-0021 excitement inputs.

The qualification pilot is deliberately a small, immutable supplement to the
Season Snapshot boundary.  This module does not normalize provider plays,
quarters, or market semantics.  It records the exact response bytes returned
for each reviewed request, together with path-free provenance that is safe to
carry into later private validation.

There are two execution paths:

* ``python -m cfb.excitement_source dry-run`` validates and prints the frozen
  request plan without creating a store, reading credentials, or opening a
  network connection.
* ``python -m cfb.excitement_source acquire`` requires an explicit owner
  allowance and trusted private-input references.  Every HTTP attempt is
  claimed durably before it enters ``RequestMeter``.  A failure therefore
  consumes its one attempt and cannot be retried by a restart or a new
  allowance identity.

The live path is intentionally not used by tests.  Tests inject a callable
transport with the same ``PilotRequest -> bytes`` interface.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import tempfile
from types import MappingProxyType
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

try:
    from .cfbd_client import configured_api_key
    from .private_inputs import InputReference, LocalInputStore
    from .request_meter import MeteredRequestFailed, RequestBudgetExhausted, RequestMeter
except ImportError:  # pragma: no cover - preserve direct cfb/ execution style
    from cfbd_client import configured_api_key
    from private_inputs import InputReference, LocalInputStore
    from request_meter import MeteredRequestFailed, RequestBudgetExhausted, RequestMeter


PILOT_ID = "adr21-qualification-pilot-v1"
PILOT_MANIFEST_SHA256 = (
    "b612f6c155eb90ac7b7473efcbb6f8a984f7c65d754ed3b5809cd49e410c344d"
)
SOURCE_ARCHIVE_SHA256 = (
    "86a27f80709549c48bfaca763f4e925b3168dbae232745d534986e6fee43da37"
)
MAX_ATTEMPTS = 9
REQUESTS_PER_KEY = 1
REQUEST_PURPOSE = "historical"
DEFAULT_CONFIG = Path("config/excitement-pilot-v1.json")
API_BASE_URL = "https://api.collegefootballdata.com"
# Keep the transport destination independent from the public compatibility
# constant.  A caller can otherwise monkey-patch the latter before
# constructing a transport and turn the credential-bearing path into an
# arbitrary-host client.
_CFBD_BASE_URL = "https://api.collegefootballdata.com"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SYSTEM_PATH_ALIASES = {Path("/tmp"), Path("/var")}


class PilotError(RuntimeError):
    """Base class for safe, credential-free pilot failures."""


class PilotManifestError(PilotError):
    """The reviewed pilot manifest is absent, changed, or malformed."""


class PilotAllowanceError(PilotError):
    """The owner allowance is absent or does not bind to this pilot."""


class PilotSourceBindingError(PilotError):
    """The retained source archive/snapshots cannot establish the pin."""


class PilotClaimError(PilotError):
    """The durable attempt ledger cannot establish a safe claim."""


class PilotTransportError(PilotError):
    """A one-shot provider transport failed without exposing credentials."""


class PilotRetentionError(PilotError):
    """A successful response could not be retained immutably."""


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise PilotError(f"{label} is not a safe identifier")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PilotError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _safe_json_read(path: Path, *, error_type: type[PilotError], label: str) -> tuple[bytes, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error_type(f"{label} is unreadable") from exc
    return raw, value


def _fsync_directory(path: Path) -> None:
    """Persist an exclusive file's directory entry where supported."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise PilotClaimError("pilot state directory cannot be persisted") from None
    try:
        os.fsync(fd)
    except OSError:
        raise PilotClaimError("pilot state directory cannot be persisted") from None
    finally:
        try:
            os.close(fd)
        except OSError:
            raise PilotClaimError("pilot state directory cannot be persisted") from None


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:  # pragma: no cover - defensive OS failure guard
            raise OSError("short write while persisting pilot state")
        view = view[written:]


@dataclass(frozen=True, slots=True)
class PilotRequest:
    """One exact endpoint/filter key from the reviewed pilot."""

    request_id: str
    endpoint: str
    params: tuple[tuple[str, str | int], ...]
    expected_game_id: str
    parent_snapshot_checksum: str
    parent_snapshot_path: str
    parent_snapshot_sha256: str

    def __post_init__(self) -> None:
        _safe_id(self.request_id, "request_id")
        if self.endpoint not in {"/plays", "/games", "/lines"}:
            raise PilotManifestError("pilot endpoint is not allowlisted")
        _safe_id(self.expected_game_id, "expected_game_id")
        _digest(self.parent_snapshot_checksum, "parent snapshot checksum")
        _digest(self.parent_snapshot_sha256, "parent snapshot SHA-256")
        if (
            not isinstance(self.parent_snapshot_path, str)
            or not self.parent_snapshot_path.startswith("snapshots/")
            or ".." in Path(self.parent_snapshot_path).parts
            or "\\" in self.parent_snapshot_path
        ):
            raise PilotManifestError("parent snapshot path is unsafe")
        names: set[str] = set()
        for name, value in self.params:
            if not isinstance(name, str) or not name or name in names:
                raise PilotManifestError("pilot request parameters are invalid")
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise PilotManifestError("pilot request parameter values are invalid")
            names.add(name)
        expected = dict(self.params)
        if self.endpoint == "/plays":
            required = {"classification", "seasonType", "team", "week", "year"}
        elif self.endpoint == "/games":
            required = {"classification", "id", "seasonType", "year"}
        else:
            required = {"gameId", "seasonType", "year"}
        if set(expected) != required:
            raise PilotManifestError("pilot request has undeclared or missing parameters")
        if expected.get("classification") not in {None, "fbs"}:
            raise PilotManifestError("pilot classification is not fbs")
        if expected.get("seasonType") != "regular":
            raise PilotManifestError("pilot seasonType must be regular")
        if self.endpoint == "/plays":
            if expected.get("team") not in {"Georgia", "Ohio State"}:
                raise PilotManifestError("pilot plays team is not allowlisted")
        else:
            field = "id" if self.endpoint == "/games" else "gameId"
            if expected.get(field) != int(self.expected_game_id):
                raise PilotManifestError("pilot game identity is not bound to the request")

    @property
    def year(self) -> int:
        value = dict(self.params).get("year")
        if isinstance(value, bool) or not isinstance(value, int):
            raise PilotManifestError("pilot request year is invalid")
        return value

    @property
    def parameter_map(self) -> dict[str, str | int]:
        return dict(self.params)

    @property
    def request_key(self) -> str:
        return _sha256_bytes(
            _json_bytes({"endpoint": self.endpoint, "params": self.parameter_map})
        )

    def safe_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "params": self.parameter_map,
            "expected_game_id": self.expected_game_id,
            "parent_snapshot_checksum": self.parent_snapshot_checksum,
            "parent_snapshot_path": self.parent_snapshot_path,
            "parent_snapshot_sha256": self.parent_snapshot_sha256,
            "request_key": self.request_key,
        }


@dataclass(frozen=True, slots=True)
class PilotManifest:
    """Validated bytes and typed view of the frozen pilot configuration."""

    pilot_id: str
    purpose: str
    max_attempts: int
    attempts_per_request: int
    redirects: bool
    retries: int
    source_archive_sha256: str
    requests: tuple[PilotRequest, ...]
    manifest_sha256: str

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONFIG) -> "PilotManifest":
        manifest_path = Path(path)
        raw, value = _safe_json_read(
            manifest_path, error_type=PilotManifestError, label="pilot manifest"
        )
        digest = _sha256_bytes(raw)
        if digest != PILOT_MANIFEST_SHA256:
            raise PilotManifestError("pilot manifest digest is not the reviewed frozen identity")
        if not isinstance(value, Mapping):
            raise PilotManifestError("pilot manifest must be a JSON object")
        expected_top = {
            "attempts_per_request",
            "max_attempts",
            "pilot_id",
            "purpose",
            "redirects",
            "requests",
            "retries",
            "schema_version",
            "source_archive_sha256",
        }
        if set(value) != expected_top:
            raise PilotManifestError("pilot manifest fields do not match the reviewed schema")
        if value.get("schema_version") != 1:
            raise PilotManifestError("unsupported pilot manifest schema")
        if value.get("pilot_id") != PILOT_ID or value.get("purpose") != REQUEST_PURPOSE:
            raise PilotManifestError("pilot identity or purpose is not approved")
        if value.get("max_attempts") != MAX_ATTEMPTS:
            raise PilotManifestError("pilot attempt cap is not nine")
        if value.get("attempts_per_request") != REQUESTS_PER_KEY:
            raise PilotManifestError("pilot request cap is not one")
        if value.get("redirects") is not False or value.get("retries") != 0:
            raise PilotManifestError("pilot transport policy permits retries or redirects")
        if value.get("source_archive_sha256") != SOURCE_ARCHIVE_SHA256:
            raise PilotManifestError("pilot source archive pin is not approved")
        entries = value.get("requests")
        if not isinstance(entries, list) or len(entries) != MAX_ATTEMPTS:
            raise PilotManifestError("pilot must contain exactly nine requests")
        requests = tuple(cls._request(entry) for entry in entries)
        cls._validate_allowlist(requests)
        return cls(
            pilot_id=PILOT_ID,
            purpose=REQUEST_PURPOSE,
            max_attempts=MAX_ATTEMPTS,
            attempts_per_request=REQUESTS_PER_KEY,
            redirects=False,
            retries=0,
            source_archive_sha256=SOURCE_ARCHIVE_SHA256,
            requests=requests,
            manifest_sha256=digest,
        )

    @staticmethod
    def _request(value: object) -> PilotRequest:
        if not isinstance(value, Mapping):
            raise PilotManifestError("pilot request must be an object")
        required = {
            "endpoint",
            "expected_game_id",
            "params",
            "parent_snapshot_checksum",
            "parent_snapshot_path",
            "parent_snapshot_sha256",
            "request_id",
        }
        if set(value) != required or not isinstance(value.get("params"), Mapping):
            raise PilotManifestError("pilot request fields do not match the reviewed schema")
        params: list[tuple[str, str | int]] = []
        for name, parameter in value["params"].items():
            if not isinstance(name, str) or isinstance(parameter, bool) or not isinstance(parameter, (str, int)):
                raise PilotManifestError("pilot request parameters are invalid")
            params.append((name, parameter))
        return PilotRequest(
            request_id=value["request_id"],  # type: ignore[arg-type]
            endpoint=value["endpoint"],  # type: ignore[arg-type]
            params=tuple(sorted(params)),
            expected_game_id=value["expected_game_id"],  # type: ignore[arg-type]
            parent_snapshot_checksum=value["parent_snapshot_checksum"],  # type: ignore[arg-type]
            parent_snapshot_path=value["parent_snapshot_path"],  # type: ignore[arg-type]
            parent_snapshot_sha256=value["parent_snapshot_sha256"],  # type: ignore[arg-type]
        )

    @staticmethod
    def _validate_allowlist(requests: Sequence[PilotRequest]) -> None:
        expected = (
            ("2024-plays-401628439", "/plays", 2024, "Georgia", "401628439"),
            ("2024-games-401628439", "/games", 2024, None, "401628439"),
            ("2024-lines-401628439", "/lines", 2024, None, "401628439"),
            ("2025-plays-401752677", "/plays", 2025, "Ohio State", "401752677"),
            ("2025-games-401752677", "/games", 2025, None, "401752677"),
            ("2025-lines-401752677", "/lines", 2025, None, "401752677"),
            ("2026-plays-401858432", "/plays", 2026, "Ohio State", "401858432"),
            ("2026-games-401858432", "/games", 2026, None, "401858432"),
            ("2026-lines-401858432", "/lines", 2026, None, "401858432"),
        )
        if len(requests) != len(expected):
            raise PilotManifestError("pilot request count is not nine")
        for request, (request_id, endpoint, year, team, game_id) in zip(requests, expected):
            if (
                request.request_id != request_id
                or request.endpoint != endpoint
                or request.year != year
                or request.expected_game_id != game_id
            ):
                raise PilotManifestError("pilot request is outside the reviewed allowlist")
            if endpoint == "/plays":
                params = request.parameter_map
                if params.get("week") != (14 if year == 2024 else 1) or params.get("team") != team:
                    raise PilotManifestError("pilot plays filter is outside the reviewed allowlist")
            elif endpoint == "/lines" and set(request.parameter_map) != {"gameId", "seasonType", "year"}:
                raise PilotManifestError("pilot lines filter has an undeclared field")
        if len({request.request_key for request in requests}) != MAX_ATTEMPTS:
            raise PilotManifestError("pilot request keys are not unique")

    def validate_instance(self) -> None:
        """Validate a public manifest object before it crosses acquisition.

        ``PilotManifest`` is intentionally public so offline callers can
        inspect a plan, but a frozen dataclass is not an authenticity
        boundary.  ``dataclasses.replace`` (or direct construction) can
        produce an object with the reviewed digest and a different request
        set.  Re-run the complete typed and allowlist checks at the point
        where a caller-supplied object could authorize network work.
        """

        if type(self) is not PilotManifest:
            raise PilotManifestError("pilot manifest has an invalid object type")
        if not isinstance(self.pilot_id, str) or self.pilot_id != PILOT_ID:
            raise PilotManifestError("pilot manifest identity is not approved")
        if not isinstance(self.purpose, str) or self.purpose != REQUEST_PURPOSE:
            raise PilotManifestError("pilot manifest purpose is not approved")
        if (
            type(self.max_attempts) is not int
            or self.max_attempts != MAX_ATTEMPTS
            or type(self.attempts_per_request) is not int
            or self.attempts_per_request != REQUESTS_PER_KEY
        ):
            raise PilotManifestError("pilot manifest attempt policy is not approved")
        if self.redirects is not False or type(self.retries) is not int or self.retries != 0:
            raise PilotManifestError("pilot manifest transport policy is not approved")
        if (
            not isinstance(self.source_archive_sha256, str)
            or self.source_archive_sha256 != SOURCE_ARCHIVE_SHA256
            or not isinstance(self.manifest_sha256, str)
            or self.manifest_sha256 != PILOT_MANIFEST_SHA256
        ):
            raise PilotManifestError("pilot manifest digest binding is not approved")
        if type(self.requests) is not tuple or len(self.requests) != MAX_ATTEMPTS:
            raise PilotManifestError("pilot manifest request set is not approved")
        for request in self.requests:
            if type(request) is not PilotRequest:
                raise PilotManifestError("pilot manifest contains an invalid request object")
            try:
                # Re-run the request's complete field and endpoint checks in
                # case a caller mutated a frozen instance through reflection.
                request.__post_init__()
            except PilotError:
                raise
            except Exception as exc:  # pragma: no cover - defensive object guard
                raise PilotManifestError("pilot manifest request is malformed") from exc
        self._validate_allowlist(self.requests)

    def safe_dict(self) -> dict[str, object]:
        return {
            "pilot_id": self.pilot_id,
            "purpose": self.purpose,
            "manifest_sha256": self.manifest_sha256,
            "source_archive_sha256": self.source_archive_sha256,
            "max_attempts": self.max_attempts,
            "attempts_per_request": self.attempts_per_request,
            "redirects": self.redirects,
            "retries": self.retries,
            "requests": [request.safe_dict() for request in self.requests],
        }


@dataclass(frozen=True, slots=True)
class PilotAllowance:
    """A reviewed owner allowance identity, never a credential value."""

    allowance_id: str
    pilot_manifest_sha256: str
    max_attempts: int
    purpose: str
    document_sha256: str

    @classmethod
    def load(cls, path: str | Path) -> "PilotAllowance":
        raw, value = _safe_json_read(
            Path(path), error_type=PilotAllowanceError, label="allowance document"
        )
        if not isinstance(value, Mapping):
            raise PilotAllowanceError("allowance document must be a JSON object")
        required = {"allowance_id", "pilot_manifest_sha256", "max_attempts", "purpose"}
        if set(value) != required:
            raise PilotAllowanceError("allowance document fields do not match the reviewed schema")
        allowance_id = value.get("allowance_id")
        try:
            _safe_id(allowance_id, "allowance_id")
            _digest(value.get("pilot_manifest_sha256"), "allowance pilot manifest SHA-256")
        except PilotError as exc:
            raise PilotAllowanceError(str(exc)) from None
        if (
            value.get("pilot_manifest_sha256") != PILOT_MANIFEST_SHA256
            or value.get("max_attempts") != MAX_ATTEMPTS
            or value.get("purpose") != REQUEST_PURPOSE
        ):
            raise PilotAllowanceError("allowance is not approved for this frozen pilot")
        return cls(
            allowance_id=allowance_id,  # type: ignore[arg-type]
            pilot_manifest_sha256=value["pilot_manifest_sha256"],  # type: ignore[arg-type]
            max_attempts=MAX_ATTEMPTS,
            purpose=REQUEST_PURPOSE,
            document_sha256=_sha256_bytes(raw),
        )

    def validate(self, manifest: PilotManifest) -> None:
        if type(self) is not PilotAllowance:
            raise PilotAllowanceError("pilot allowance has an invalid object type")
        try:
            _safe_id(self.allowance_id, "allowance_id")
            _digest(self.pilot_manifest_sha256, "allowance pilot manifest SHA-256")
            _digest(self.document_sha256, "allowance document SHA-256")
        except PilotError as exc:
            raise PilotAllowanceError(str(exc)) from None
        if (
            type(self.max_attempts) is not int
            or self.max_attempts != MAX_ATTEMPTS
            or self.purpose != REQUEST_PURPOSE
        ):
            raise PilotAllowanceError("allowance cap or purpose is not approved")
        if self.pilot_manifest_sha256 != manifest.manifest_sha256:
            raise PilotAllowanceError("allowance does not bind to the reviewed pilot manifest")
        if self.max_attempts != manifest.max_attempts or self.purpose != manifest.purpose:
            raise PilotAllowanceError("allowance cap or purpose does not match the pilot")


@dataclass(frozen=True, slots=True)
class SourceBinding:
    """Trusted path-free references for the retained source inputs."""

    source_archive: InputReference
    snapshots: Mapping[str, InputReference]

    def __post_init__(self) -> None:
        if not isinstance(self.source_archive, InputReference):
            raise PilotSourceBindingError("source archive reference has an invalid type")
        object.__setattr__(self, "snapshots", MappingProxyType(dict(self.snapshots)))
        for path, reference in self.snapshots.items():
            if not isinstance(path, str) or not path.startswith("snapshots/"):
                raise PilotSourceBindingError("source snapshot reference has an unsafe path")
            if not isinstance(reference, InputReference):
                raise PilotSourceBindingError("source snapshot reference has an invalid type")

    @classmethod
    def load(cls, path: str | Path) -> "SourceBinding":
        _, value = _safe_json_read(
            Path(path), error_type=PilotSourceBindingError, label="source binding"
        )
        if not isinstance(value, Mapping) or set(value) != {"source_archive", "snapshots"}:
            raise PilotSourceBindingError("source binding has an invalid shape")
        try:
            archive = InputReference.from_public_receipt(value["source_archive"])  # type: ignore[arg-type]
            snapshots_value = value["snapshots"]
            if not isinstance(snapshots_value, Mapping):
                raise PilotSourceBindingError("source snapshot references are invalid")
            snapshots = {
                path: InputReference.from_public_receipt(reference)  # type: ignore[arg-type]
                for path, reference in snapshots_value.items()
            }
        except PilotSourceBindingError:
            raise
        except Exception as exc:
            raise PilotSourceBindingError("source binding contains an invalid receipt") from exc
        return cls(archive, snapshots)

    def verify(self, store: LocalInputStore, manifest: PilotManifest) -> None:
        if self.source_archive.sha256 != manifest.source_archive_sha256:
            raise PilotSourceBindingError("source archive reference does not match the frozen manifest")
        try:
            store.verify(self.source_archive)
        except Exception as exc:
            raise PilotSourceBindingError("source archive reference cannot be verified") from exc
        expected_paths = {request.parent_snapshot_path for request in manifest.requests}
        if set(self.snapshots) != expected_paths:
            raise PilotSourceBindingError("source snapshot references do not cover the pilot pins")
        for path in sorted(expected_paths):
            reference = self.snapshots[path]
            expected = {
                request.parent_snapshot_sha256
                for request in manifest.requests
                if request.parent_snapshot_path == path
            }
            if reference.sha256 not in expected:
                raise PilotSourceBindingError(f"source snapshot pin mismatch for {path}")
            try:
                store.verify(reference)
            except Exception as exc:
                raise PilotSourceBindingError(f"source snapshot cannot be verified for {path}") from exc

    def safe_dict(self) -> dict[str, object]:
        return {
            "source_archive": self.source_archive.public_receipt(),
            "snapshots": {
                path: self.snapshots[path].public_receipt()
                for path in sorted(self.snapshots)
            },
        }


@dataclass(frozen=True, slots=True)
class SupplementReceipt:
    """Safe receipt for one retained response; raw bytes stay private."""

    request_id: str
    endpoint: str
    params: tuple[tuple[str, str | int], ...]
    response: InputReference
    captured_at: str
    pilot_id: str
    manifest_sha256: str
    allowance_id: str
    source_archive_sha256: str
    parent_snapshot_path: str
    parent_snapshot_sha256: str
    parent_snapshot_checksum: str

    def public_receipt(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "params": dict(self.params),
            "response": self.response.public_receipt(),
            "captured_at": self.captured_at,
            "pilot_id": self.pilot_id,
            "manifest_sha256": self.manifest_sha256,
            "allowance_id": self.allowance_id,
            "source_archive_sha256": self.source_archive_sha256,
            "parent_snapshot": {
                "path": self.parent_snapshot_path,
                "sha256": self.parent_snapshot_sha256,
                "snapshot_checksum": self.parent_snapshot_checksum,
            },
        }

    @classmethod
    def from_public_receipt(cls, value: Mapping[str, object]) -> "SupplementReceipt":
        required = {
            "schema_version",
            "request_id",
            "endpoint",
            "params",
            "response",
            "captured_at",
            "pilot_id",
            "manifest_sha256",
            "allowance_id",
            "source_archive_sha256",
            "parent_snapshot",
        }
        if set(value) != required:
            raise PilotClaimError("stored supplement receipt has an invalid schema")
        if value.get("schema_version") != 1:
            raise PilotClaimError("stored supplement receipt has an unsupported schema")
        parent = value.get("parent_snapshot")
        if not isinstance(parent, Mapping):
            raise PilotClaimError("stored supplement receipt has invalid source provenance")
        if set(parent) != {"path", "sha256", "snapshot_checksum"}:
            raise PilotClaimError("stored supplement receipt has invalid source provenance")
        try:
            response = InputReference.from_public_receipt(value["response"])  # type: ignore[arg-type]
            params = value["params"]
            if not isinstance(params, Mapping):
                raise PilotClaimError("stored supplement receipt has invalid request parameters")
            _safe_id(value["request_id"], "stored request_id")
            _safe_id(value["pilot_id"], "stored pilot_id")
            _safe_id(value["allowance_id"], "stored allowance_id")
            _digest(value["manifest_sha256"], "stored manifest SHA-256")
            _digest(value["source_archive_sha256"], "stored source archive SHA-256")
            if not isinstance(value["captured_at"], str) or not value["captured_at"]:
                raise PilotClaimError("stored supplement receipt has an invalid capture time")
            if (
                not isinstance(parent["path"], str)
                or not parent["path"].startswith("snapshots/")
                or ".." in Path(parent["path"]).parts
                or "\\" in parent["path"]
            ):
                raise PilotClaimError("stored supplement receipt has an unsafe source path")
            _digest(parent["sha256"], "stored parent snapshot SHA-256")
            _digest(parent["snapshot_checksum"], "stored parent snapshot checksum")
            params_tuple = tuple(sorted((key, parameter) for key, parameter in params.items()))
            return cls(
                request_id=value["request_id"],  # type: ignore[arg-type]
                endpoint=value["endpoint"],  # type: ignore[arg-type]
                params=params_tuple,  # type: ignore[arg-type]
                response=response,
                captured_at=value["captured_at"],  # type: ignore[arg-type]
                pilot_id=value["pilot_id"],  # type: ignore[arg-type]
                manifest_sha256=value["manifest_sha256"],  # type: ignore[arg-type]
                allowance_id=value["allowance_id"],  # type: ignore[arg-type]
                source_archive_sha256=value["source_archive_sha256"],  # type: ignore[arg-type]
                parent_snapshot_path=parent["path"],  # type: ignore[arg-type]
                parent_snapshot_sha256=parent["sha256"],  # type: ignore[arg-type]
                parent_snapshot_checksum=parent["snapshot_checksum"],  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PilotClaimError("stored supplement receipt is malformed") from exc


class _ClaimLedger:
    """SQLite claim ledger with independently durable pilot state witnesses."""

    _GENESIS_SCHEMA = 1
    _WITNESS_SCHEMA = 1

    def __init__(self, store: LocalInputStore, manifest: PilotManifest) -> None:
        self.store = store
        self.manifest = manifest
        try:
            _safe_id(manifest.pilot_id, "pilot_id")
        except PilotError as exc:
            raise PilotClaimError("private claim store pilot identity is invalid") from None
        if not isinstance(store.root, Path) or not store.root.is_absolute():
            raise PilotClaimError("private claim store root is invalid")
        self.genesis_path = store.root / f".{manifest.pilot_id}.genesis.json"
        self.ready_path = store.root / f".{manifest.pilot_id}.ledger-ready.json"
        self.path = store.root / f".{manifest.pilot_id}.claims.sqlite3"
        self.ledger_id: str
        self._genesis_raw: bytes
        fresh = self._prepare_state()
        self._ensure_file(allow_create=fresh)
        self._initialize(fresh=fresh)
        if fresh:
            self._create_ready_marker()

    @staticmethod
    def _assert_owner_only(info: os.stat_result, *, label: str) -> None:
        uid = getattr(os, "getuid", lambda: None)()
        if uid is not None and info.st_uid != uid:
            raise PilotClaimError(f"{label} has the wrong owner")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PilotClaimError(f"{label} is not owner-only")

    @staticmethod
    def _assert_no_symlink_components(
        path: Path, *, label: str, allow_missing_final: bool = False
    ) -> None:
        """Match LocalInputStore's ancestor-symlink policy without importing internals."""

        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                info = os.lstat(current)
            except FileNotFoundError as exc:
                if allow_missing_final and current == path:
                    return
                raise PilotClaimError(f"{label} is missing") from exc
            except OSError as exc:
                raise PilotClaimError(f"{label} cannot be inspected") from exc
            if stat.S_ISLNK(info.st_mode) and current not in _SYSTEM_PATH_ALIASES:
                raise PilotClaimError(f"{label} contains a symlink")

    def _assert_store_root(self) -> None:
        root = self.store.root
        if not isinstance(root, Path) or not root.is_absolute() or root.parent == root:
            raise PilotClaimError("private claim store root is invalid")
        self._assert_no_symlink_components(
            root, label="private claim store path", allow_missing_final=False
        )
        try:
            root_info = os.lstat(root)
        except OSError as exc:
            raise PilotClaimError("private claim store cannot be inspected") from exc
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise PilotClaimError("private claim store is not a directory")
        self._assert_owner_only(root_info, label="private claim store")

    def _assert_private_file(
        self, path: Path, *, label: str, require: bool = True
    ) -> bool:
        self._assert_store_root()
        if path.parent != self.store.root:
            raise PilotClaimError(f"{label} is outside the private store")
        self._assert_no_symlink_components(
            path,
            label=f"{label} path",
            allow_missing_final=not require,
        )
        try:
            info = os.lstat(path)
        except FileNotFoundError as exc:
            if require:
                raise PilotClaimError(f"{label} is missing") from exc
            return False
        except OSError as exc:
            raise PilotClaimError(f"{label} cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise PilotClaimError(f"{label} is not a regular file")
        self._assert_owner_only(info, label=label)
        return True

    def _assert_secure_path(
        self, *, require_ledger: bool = True, require_ready: bool = True
    ) -> None:
        """Recheck all private marker/ledger ownership and symlink boundaries."""

        self._assert_store_root()
        self._assert_private_file(
            self.genesis_path, label="pilot genesis marker", require=True
        )
        if require_ready:
            self._assert_private_file(
                self.ready_path, label="pilot ledger-ready marker", require=True
            )
        self._assert_private_file(
            self.path, label="pilot claim ledger", require=require_ledger
        )

    @staticmethod
    def _marker_payload(
        *,
        pilot_id: str,
        manifest_sha256: str,
        source_archive_sha256: str,
        max_attempts: int,
        ledger_id: str,
    ) -> dict[str, object]:
        return {
            "schema_version": _ClaimLedger._GENESIS_SCHEMA,
            "pilot_id": pilot_id,
            "manifest_sha256": manifest_sha256,
            "source_archive_sha256": source_archive_sha256,
            "max_attempts": max_attempts,
            "ledger_id": ledger_id,
        }

    def _create_exclusive_file(self, path: Path, payload: bytes, *, label: str) -> None:
        """Create one owner-only immutable state file and persist its directory entry."""

        self._assert_private_file(path, label=label, require=False)
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        except FileExistsError as exc:
            raise PilotClaimError(f"{label} already exists") from exc
        except OSError as exc:
            raise PilotClaimError(f"{label} cannot be created") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        _fsync_directory(self.store.root)
        self._assert_private_file(path, label=label, require=True)

    def _read_genesis(self) -> None:
        raw, value = _safe_json_read(
            self.genesis_path,
            error_type=PilotClaimError,
            label="pilot genesis marker",
        )
        expected = {
            "schema_version",
            "pilot_id",
            "manifest_sha256",
            "source_archive_sha256",
            "max_attempts",
            "ledger_id",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise PilotClaimError("pilot genesis marker has an invalid schema")
        ledger_id = value.get("ledger_id")
        try:
            _safe_id(value.get("pilot_id"), "pilot genesis pilot_id")
            _digest(value.get("manifest_sha256"), "pilot genesis manifest SHA-256")
            _digest(value.get("source_archive_sha256"), "pilot genesis source archive SHA-256")
            _safe_id(ledger_id, "pilot genesis ledger_id")
        except PilotError:
            raise PilotClaimError("pilot genesis marker has invalid identity fields") from None
        if (
            value.get("schema_version") != self._GENESIS_SCHEMA
            or value.get("pilot_id") != self.manifest.pilot_id
            or value.get("manifest_sha256") != self.manifest.manifest_sha256
            or value.get("source_archive_sha256") != self.manifest.source_archive_sha256
            or value.get("max_attempts") != self.manifest.max_attempts
        ):
            raise PilotClaimError("pilot genesis marker is bound to a different reviewed pilot")
        self.ledger_id = ledger_id  # type: ignore[assignment]
        self._genesis_raw = raw

    def _read_ready(self) -> None:
        raw, value = _safe_json_read(
            self.ready_path,
            error_type=PilotClaimError,
            label="pilot ledger-ready marker",
        )
        expected = {
            "schema_version",
            "pilot_id",
            "manifest_sha256",
            "ledger_id",
            "genesis_sha256",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise PilotClaimError("pilot ledger-ready marker has an invalid schema")
        try:
            _digest(value.get("genesis_sha256"), "pilot ready genesis SHA-256")
            _safe_id(value.get("ledger_id"), "pilot ready ledger_id")
        except PilotError:
            raise PilotClaimError("pilot ledger-ready marker has invalid identity fields") from None
        if (
            value.get("schema_version") != self._GENESIS_SCHEMA
            or value.get("pilot_id") != self.manifest.pilot_id
            or value.get("manifest_sha256") != self.manifest.manifest_sha256
            or value.get("ledger_id") != self.ledger_id
            or value.get("genesis_sha256") != _sha256_bytes(self._genesis_raw)
        ):
            raise PilotClaimError("pilot ledger-ready marker is not bound to the genesis marker")

    def _prepare_state(self) -> bool:
        """Load established state or create the genesis marker for a fresh store."""

        self._assert_store_root()
        genesis_exists = self._assert_private_file(
            self.genesis_path, label="pilot genesis marker", require=False
        )
        ledger_exists = self._assert_private_file(
            self.path, label="pilot claim ledger", require=False
        )
        ready_exists = self._assert_private_file(
            self.ready_path, label="pilot ledger-ready marker", require=False
        )
        if not genesis_exists:
            if ledger_exists or ready_exists or any(
                self._assert_private_file(
                    self._witness_path(request),
                    label="pilot claim witness",
                    require=False,
                )
                for request in self.manifest.requests
            ):
                raise PilotClaimError("pilot claim state is missing its genesis marker")
            self.ledger_id = secrets.token_hex(32)
            try:
                _safe_id(self.ledger_id, "pilot ledger_id")
            except PilotError:
                raise PilotClaimError("pilot ledger identity could not be created") from None
            payload = _json_bytes(
                self._marker_payload(
                    pilot_id=self.manifest.pilot_id,
                    manifest_sha256=self.manifest.manifest_sha256,
                    source_archive_sha256=self.manifest.source_archive_sha256,
                    max_attempts=self.manifest.max_attempts,
                    ledger_id=self.ledger_id,
                )
            )
            self._create_exclusive_file(
                self.genesis_path, payload, label="pilot genesis marker"
            )
            self._genesis_raw = payload
            return True

        self._read_genesis()
        if not ready_exists:
            raise PilotClaimError("pilot claim ledger is not durably initialized")
        self._read_ready()
        if not ledger_exists:
            raise PilotClaimError("pilot claim ledger is missing after initialization")
        return False

    def _ensure_file(self, *, allow_create: bool) -> None:
        # Validate the complete store boundary before creating or touching the
        # ledger.  In particular, do not chmod a path under a replaced root or
        # through an ancestor symlink.
        self._assert_secure_path(require_ledger=False, require_ready=False)
        existing = self._assert_private_file(
            self.path, label="pilot claim ledger", require=False
        )
        if existing:
            if allow_create:
                try:
                    info = os.lstat(self.path)
                except OSError as exc:
                    raise PilotClaimError("pilot claim ledger cannot be inspected") from exc
                if info.st_size != 0:
                    raise PilotClaimError("new pilot claim ledger is not empty")
            else:
                return
        elif not allow_create:
            raise PilotClaimError("pilot claim ledger is missing after initialization")
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT
                | os.O_EXCL
                | os.O_RDWR
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError as exc:
            raise PilotClaimError("pilot claim ledger appeared during initialization") from exc
        except OSError as exc:
            raise PilotClaimError("pilot claim ledger cannot be created") from exc
        else:
            os.close(descriptor)
        _fsync_directory(self.store.root)
        self._assert_private_file(self.path, label="pilot claim ledger", require=True)

    def _create_ready_marker(self) -> None:
        payload = _json_bytes(
            {
                "schema_version": self._GENESIS_SCHEMA,
                "pilot_id": self.manifest.pilot_id,
                "manifest_sha256": self.manifest.manifest_sha256,
                "ledger_id": self.ledger_id,
                "genesis_sha256": _sha256_bytes(self._genesis_raw),
            }
        )
        self._create_exclusive_file(
            self.ready_path, payload, label="pilot ledger-ready marker"
        )
        self._read_ready()

    def _connect(self, *, require_ready: bool = True) -> sqlite3.Connection:
        self._assert_secure_path(require_ready=require_ready)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            # A replacement between the pre-open check and sqlite's open is
            # still rejected before any claim transaction can proceed.
            self._assert_secure_path(require_ready=require_ready)
            return connection
        except PilotClaimError:
            if connection is not None:
                connection.close()
            raise
        except (OSError, sqlite3.Error, TypeError) as exc:
            raise PilotClaimError("pilot claim ledger cannot be opened") from exc

    def _initialize(self, *, fresh: bool) -> None:
        try:
            with self._connect(require_ready=not fresh) as connection:
                if fresh:
                    connection.execute(
                        """
                        CREATE TABLE pilot_binding (
                            id INTEGER PRIMARY KEY CHECK (id = 1),
                            pilot_id TEXT NOT NULL,
                            manifest_sha256 TEXT NOT NULL,
                            source_archive_sha256 TEXT NOT NULL,
                            max_attempts INTEGER NOT NULL,
                            ledger_id TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE allowances (
                            allowance_id TEXT PRIMARY KEY,
                            document_sha256 TEXT NOT NULL,
                            manifest_sha256 TEXT NOT NULL,
                            max_attempts INTEGER NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE claims (
                            request_id TEXT PRIMARY KEY,
                            request_key TEXT NOT NULL UNIQUE,
                            endpoint TEXT NOT NULL,
                            params_json TEXT NOT NULL,
                            allowance_id TEXT NOT NULL,
                            allowance_document_sha256 TEXT NOT NULL,
                            state TEXT NOT NULL,
                            claimed_at TEXT NOT NULL,
                            completed_at TEXT,
                            receipt_json TEXT
                        )
                        """
                    )
                else:
                    self._require_schema(connection)
                row = connection.execute(
                    "SELECT pilot_id, manifest_sha256, source_archive_sha256, max_attempts, ledger_id "
                    "FROM pilot_binding WHERE id = 1"
                ).fetchone()
                expected = (
                    self.manifest.pilot_id,
                    self.manifest.manifest_sha256,
                    self.manifest.source_archive_sha256,
                    self.manifest.max_attempts,
                    self.ledger_id,
                )
                if row is None and fresh:
                    connection.execute(
                        "INSERT INTO pilot_binding "
                        "(id, pilot_id, manifest_sha256, source_archive_sha256, max_attempts, ledger_id) "
                        "VALUES (1, ?, ?, ?, ?, ?)",
                        expected,
                    )
                elif row is None or tuple(row) != expected:
                    raise PilotClaimError("pilot claim ledger is bound to a different reviewed pilot")
        except PilotClaimError:
            raise
        except sqlite3.Error as exc:
            raise PilotClaimError("pilot claim ledger schema is invalid") from exc

    @staticmethod
    def _require_schema(connection: sqlite3.Connection) -> None:
        required = {
            "pilot_binding": {"id", "pilot_id", "manifest_sha256", "source_archive_sha256", "max_attempts", "ledger_id"},
            "allowances": {"allowance_id", "document_sha256", "manifest_sha256", "max_attempts"},
            "claims": {
                "request_id", "request_key", "endpoint", "params_json", "allowance_id",
                "allowance_document_sha256", "state", "claimed_at", "completed_at", "receipt_json",
            },
        }
        for table, columns in required.items():
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if row is None:
                raise PilotClaimError("pilot claim ledger schema is incomplete")
            actual = {
                column[1]
                for column in connection.execute(f"PRAGMA table_info({table})")
            }
            if not columns <= actual:
                raise PilotClaimError("pilot claim ledger schema is incomplete")

    def _witness_path(self, request: PilotRequest) -> Path:
        try:
            _safe_id(request.request_id, "pilot request_id")
        except PilotError:
            raise PilotClaimError("pilot request identity is invalid") from None
        return self.store.root / (
            f".{self.manifest.pilot_id}.claim.{request.request_id}.witness"
        )

    def _witness_payload(
        self, request: PilotRequest, allowance: PilotAllowance
    ) -> dict[str, object]:
        return {
            "schema_version": self._WITNESS_SCHEMA,
            "pilot_id": self.manifest.pilot_id,
            "manifest_sha256": self.manifest.manifest_sha256,
            "source_archive_sha256": self.manifest.source_archive_sha256,
            "max_attempts": self.manifest.max_attempts,
            "request_id": request.request_id,
            "request_key": request.request_key,
            "endpoint": request.endpoint,
            "params": request.parameter_map,
            "allowance_id": allowance.allowance_id,
            "allowance_document_sha256": allowance.document_sha256,
        }

    def _read_witness(
        self, request: PilotRequest, *, require: bool = False
    ) -> Mapping[str, object] | None:
        path = self._witness_path(request)
        if not self._assert_private_file(path, label="pilot claim witness", require=require):
            return None
        _, value = _safe_json_read(
            path,
            error_type=PilotClaimError,
            label="pilot claim witness",
        )
        expected = {
            "schema_version",
            "pilot_id",
            "manifest_sha256",
            "source_archive_sha256",
            "max_attempts",
            "request_id",
            "request_key",
            "endpoint",
            "params",
            "allowance_id",
            "allowance_document_sha256",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise PilotClaimError("pilot claim witness has an invalid schema")
        params = value.get("params")
        if not isinstance(params, Mapping):
            raise PilotClaimError("pilot claim witness has invalid request parameters")
        try:
            _safe_id(value.get("pilot_id"), "pilot witness pilot_id")
            _safe_id(value.get("request_id"), "pilot witness request_id")
            _digest(value.get("manifest_sha256"), "pilot witness manifest SHA-256")
            _digest(value.get("source_archive_sha256"), "pilot witness source archive SHA-256")
            _digest(value.get("request_key"), "pilot witness request key")
            _safe_id(value.get("allowance_id"), "pilot witness allowance_id")
            _digest(
                value.get("allowance_document_sha256"),
                "pilot witness allowance document SHA-256",
            )
        except PilotError:
            raise PilotClaimError("pilot claim witness has invalid identity fields") from None
        expected_params = request.parameter_map
        if (
            value.get("schema_version") != self._WITNESS_SCHEMA
            or value.get("pilot_id") != self.manifest.pilot_id
            or value.get("manifest_sha256") != self.manifest.manifest_sha256
            or value.get("source_archive_sha256") != self.manifest.source_archive_sha256
            or value.get("max_attempts") != self.manifest.max_attempts
            or value.get("request_id") != request.request_id
            or value.get("request_key") != request.request_key
            or value.get("endpoint") != request.endpoint
            or dict(params) != expected_params
        ):
            raise PilotClaimError("pilot claim witness is bound to a different request")
        return value

    def _create_witness(self, request: PilotRequest, allowance: PilotAllowance) -> None:
        payload = _json_bytes(self._witness_payload(request, allowance))
        self._create_exclusive_file(
            self._witness_path(request),
            payload,
            label="pilot claim witness",
        )

    @staticmethod
    def _witness_matches_claim(
        witness: Mapping[str, object], *, allowance_id: object, allowance_document_sha256: object
    ) -> None:
        if (
            witness.get("allowance_id") != allowance_id
            or witness.get("allowance_document_sha256") != allowance_document_sha256
        ):
            raise PilotClaimError("pilot claim witness does not match its ledger claim")

    def register_allowance(self, allowance: PilotAllowance) -> None:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT document_sha256, manifest_sha256, max_attempts FROM allowances "
                    "WHERE allowance_id = ?",
                    (allowance.allowance_id,),
                ).fetchone()
                expected = (
                    allowance.document_sha256,
                    allowance.pilot_manifest_sha256,
                    allowance.max_attempts,
                )
                if row is None:
                    connection.execute(
                        "INSERT INTO allowances "
                        "(allowance_id, document_sha256, manifest_sha256, max_attempts) "
                        "VALUES (?, ?, ?, ?)",
                        (allowance.allowance_id, *expected),
                    )
                elif tuple(row) != expected:
                    raise PilotClaimError("allowance identity was reused with different contents")
                connection.commit()
        except PilotClaimError:
            raise
        except (IndexError, TypeError, ValueError, sqlite3.Error) as exc:
            raise PilotClaimError("pilot allowance identity cannot be recorded") from exc

    def claim(self, request: PilotRequest, allowance: PilotAllowance) -> str:
        """Claim before transport; return ``new`` or the existing state."""

        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT request_id, request_key, endpoint, params_json, allowance_id, "
                    "allowance_document_sha256, state FROM claims WHERE request_id = ?",
                    (request.request_id,),
                ).fetchone()
                params_json = _json_bytes(request.parameter_map).decode("utf-8")
                witness = self._read_witness(request)
                if row is not None:
                    if (row[0], row[1], row[2], row[3]) != (
                        request.request_id,
                        request.request_key,
                        request.endpoint,
                        params_json,
                    ):
                        raise PilotClaimError("request identity changed inside the claim ledger")
                    if witness is None:
                        raise PilotClaimError("pilot claim ledger row is missing its witness")
                    self._witness_matches_claim(
                        witness,
                        allowance_id=row[4],
                        allowance_document_sha256=row[5],
                    )
                    connection.commit()
                    return str(row[6])
                if witness is not None:
                    raise PilotClaimError("pilot claim witness has no matching ledger row")
                count = connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
                if count >= self.manifest.max_attempts:
                    raise PilotClaimError("pilot attempt cap is exhausted")
                # The witness is the independent consumed-attempt boundary.
                # If the process exits after this fsync and before SQLite's
                # commit, the next run sees an orphan witness and stops
                # instead of issuing the request again.
                self._create_witness(request, allowance)
                connection.execute(
                    "INSERT INTO claims "
                    "(request_id, request_key, endpoint, params_json, allowance_id, "
                    "allowance_document_sha256, state, claimed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'claimed', ?)",
                    (
                        request.request_id,
                        request.request_key,
                        request.endpoint,
                        params_json,
                        allowance.allowance_id,
                        allowance.document_sha256,
                        now,
                    ),
                )
                connection.commit()
                return "new"
        except PilotClaimError:
            raise
        except (IndexError, TypeError, ValueError, sqlite3.Error) as exc:
            raise PilotClaimError("pilot request claim cannot be recorded") from exc

    def finish(self, request: PilotRequest, state: str, receipt: SupplementReceipt | None = None) -> None:
        if state not in {"succeeded", "failed", "blocked"}:
            raise PilotClaimError("pilot claim outcome is invalid")
        payload = (
            json.dumps(receipt.public_receipt(), sort_keys=True, separators=(",", ":"))
            if receipt is not None
            else None
        )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT request_key, state, allowance_id, allowance_document_sha256 "
                    "FROM claims WHERE request_id = ?",
                    (request.request_id,),
                ).fetchone()
                if row is None or row[0] != request.request_key:
                    raise PilotClaimError("pilot claim is missing or changed")
                witness = self._read_witness(request, require=True)
                if witness is None:  # pragma: no cover - require=True guard
                    raise PilotClaimError("pilot claim witness is missing")
                self._witness_matches_claim(
                    witness,
                    allowance_id=row[2],
                    allowance_document_sha256=row[3],
                )
                if row[1] != "claimed":
                    if row[1] == state:
                        connection.commit()
                        return
                    raise PilotClaimError("pilot claim outcome was already recorded")
                connection.execute(
                    "UPDATE claims SET state = ?, completed_at = ?, receipt_json = ? "
                    "WHERE request_id = ?",
                    (state, datetime.now(timezone.utc).isoformat(), payload, request.request_id),
                )
                connection.commit()
        except PilotClaimError:
            raise
        except (IndexError, TypeError, ValueError, sqlite3.Error) as exc:
            raise PilotClaimError("pilot claim outcome cannot be recorded") from exc

    def receipts(self) -> tuple[SupplementReceipt, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT request_id, allowance_id, allowance_document_sha256, state, receipt_json "
                    "FROM claims WHERE state = 'succeeded' "
                    "ORDER BY rowid"
                ).fetchall()
        except (IndexError, TypeError, ValueError, sqlite3.Error) as exc:
            raise PilotClaimError("pilot receipts cannot be read") from exc
        values: list[SupplementReceipt] = []
        expected_requests = {request.request_id: request for request in self.manifest.requests}
        for row in rows:
            if row[4] is None:
                raise PilotClaimError("pilot ledger contains a successful claim without a receipt")
            request = expected_requests.get(row[0])
            if request is None:
                raise PilotClaimError("pilot ledger contains an unknown successful claim")
            witness = self._read_witness(request, require=True)
            if witness is None:  # pragma: no cover - require=True guard
                raise PilotClaimError("pilot successful claim is missing its witness")
            self._witness_matches_claim(
                witness,
                allowance_id=row[1],
                allowance_document_sha256=row[2],
            )
            try:
                parsed = json.loads(row[4])
                if not isinstance(parsed, Mapping):
                    raise ValueError
                receipt = SupplementReceipt.from_public_receipt(parsed)
                if receipt.request_id != row[0] or receipt.allowance_id != row[1]:
                    raise PilotClaimError("pilot ledger receipt identity does not match its claim")
                values.append(receipt)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise PilotClaimError("pilot ledger contains an invalid receipt") from exc
        return tuple(values)

    def receipt_for(self, request: PilotRequest) -> SupplementReceipt:
        """Read one successful receipt for a replay and preserve its row binding."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT request_id, allowance_id, allowance_document_sha256, state, receipt_json "
                    "FROM claims WHERE request_id = ?",
                    (request.request_id,),
                ).fetchone()
        except (IndexError, TypeError, ValueError, sqlite3.Error) as exc:
            raise PilotClaimError("pilot receipt cannot be read") from exc
        if row is None or row[3] != "succeeded" or row[4] is None:
            raise PilotClaimError("pilot successful claim has no retained receipt")
        witness = self._read_witness(request, require=True)
        if witness is None:  # pragma: no cover - require=True guard
            raise PilotClaimError("pilot successful claim is missing its witness")
        self._witness_matches_claim(
            witness,
            allowance_id=row[1],
            allowance_document_sha256=row[2],
        )
        try:
            parsed = json.loads(row[4])
            if not isinstance(parsed, Mapping):
                raise ValueError
            receipt = SupplementReceipt.from_public_receipt(parsed)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PilotClaimError("pilot ledger contains an invalid receipt") from exc
        if receipt.request_id != row[0] or receipt.allowance_id != row[1]:
            raise PilotClaimError("pilot ledger receipt identity does not match its claim")
        return receipt

    def remaining(self) -> int:
        try:
            with self._connect() as connection:
                count = connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        except (IndexError, TypeError, ValueError, sqlite3.Error) as exc:
            raise PilotClaimError("pilot ledger count cannot be read") from exc
        return max(0, self.manifest.max_attempts - int(count))


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects instead of allowing urllib to perform a second call."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise PilotTransportError("provider redirect rejected by pilot transport policy")


@dataclass(frozen=True, slots=True)
class HttpSupplementTransport:
    """One-shot CFBD GET transport with retries and redirects disabled."""

    base_url: str = API_BASE_URL
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        # The supplement is approved only for the CFBD host.  Keeping the
        # field injectable would allow a caller to send the BB-injected key to
        # an arbitrary host, so fail closed before the credential is read.
        if type(self.base_url) is not str or self.base_url != _CFBD_BASE_URL:
            raise PilotTransportError("pilot transport host is not the approved CFBD endpoint")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise PilotTransportError("pilot transport timeout is invalid")

    def __call__(self, request: PilotRequest) -> bytes:
        api_key = configured_api_key()
        if not api_key:
            raise PilotTransportError("CFBD_API is not configured")
        query = urlencode(sorted(request.parameter_map.items()))
        url = f"{_CFBD_BASE_URL}{request.endpoint}?{query}"
        outbound = Request(
            url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        opener = build_opener(_NoRedirectHandler())
        try:
            with opener.open(outbound, timeout=self.timeout_seconds) as response:
                status = int(response.getcode())
                if 300 <= status < 400:
                    raise PilotTransportError("provider redirect rejected by pilot transport policy")
                if status < 200 or status >= 300:
                    raise PilotTransportError("provider returned a non-success status")
                return response.read()
        except PilotTransportError:
            raise
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise PilotTransportError("provider redirect rejected by pilot transport policy") from None
            raise PilotTransportError("provider returned an HTTP failure") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise PilotTransportError("provider transport failed") from None


Transport = Callable[[PilotRequest], bytes]


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    pilot_id: str
    manifest_sha256: str
    allowance_id: str
    receipts: tuple[SupplementReceipt, ...]
    remaining_attempts: int

    def safe_dict(self) -> dict[str, object]:
        return {
            "pilot_id": self.pilot_id,
            "manifest_sha256": self.manifest_sha256,
            "allowance_id": self.allowance_id,
            "receipts": [receipt.public_receipt() for receipt in self.receipts],
            "remaining_attempts": self.remaining_attempts,
        }


class PilotAdapter:
    """Acquire exactly the frozen pilot through RequestMeter and LocalInputStore."""

    def __init__(
        self,
        meter: RequestMeter | None,
        store: LocalInputStore | None = None,
        *,
        manifest: PilotManifest | None = None,
        transport: Transport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if meter is not None and not hasattr(meter, "execute"):
            raise TypeError("meter must provide RequestMeter.execute")
        self.meter = meter
        self.store = store
        self.manifest = manifest or PilotManifest.load()
        self.transport = transport or HttpSupplementTransport()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _assert_frozen_manifest(self) -> None:
        """Re-check a caller-supplied object against the reviewed bytes."""

        if type(self.manifest) is not PilotManifest:
            raise PilotManifestError("pilot object has an invalid manifest type")
        try:
            canonical = PilotManifest.load()
            self.manifest.validate_instance()
        except PilotError:
            raise
        except Exception as exc:  # pragma: no cover - defensive public-object guard
            raise PilotManifestError("pilot object is malformed") from exc
        if self.manifest.safe_dict() != canonical.safe_dict():
            raise PilotManifestError("pilot object does not match the reviewed allowlist")

    def dry_run(self) -> dict[str, object]:
        """Return a read-only plan; this method never touches store or transport."""

        return {
            "mode": "dry-run",
            "pilot": self.manifest.safe_dict(),
            "remaining_attempts": self.manifest.max_attempts,
            "network": "disabled",
            "credentials": "not read",
            "private_store": "not opened or created",
        }

    def acquire(
        self,
        allowance: PilotAllowance,
        source_binding: SourceBinding,
    ) -> AcquisitionResult:
        """Acquire the pilot, failing closed on any consumed request failure."""

        self._assert_frozen_manifest()
        if type(allowance) is not PilotAllowance:
            raise PilotAllowanceError("pilot allowance has an invalid object type")
        if not isinstance(source_binding, SourceBinding):
            raise PilotSourceBindingError("pilot source binding has an invalid object type")
        allowance.validate(self.manifest)
        if self.meter is None:
            raise PilotClaimError("live acquisition requires RequestMeter")
        if self.store is None:
            raise PilotSourceBindingError("live acquisition requires an owner-only LocalInputStore")
        source_binding.verify(self.store, self.manifest)
        ledger = _ClaimLedger(self.store, self.manifest)
        ledger.register_allowance(allowance)
        self._verify_existing_receipts(ledger)
        for request in self.manifest.requests:
            state = ledger.claim(request, allowance)
            if state == "succeeded":
                # Recheck at the exact replay decision as well as during the
                # initial ledger scan.  This keeps a reused response behind a
                # LocalInputStore digest verification even if the object
                # changes between those two boundaries.
                self._verify_existing_receipt(ledger, request)
                continue
            if state != "new":
                raise PilotClaimError(
                    f"pilot request {request.request_id} was already consumed ({state})"
                )
            try:
                response = self.meter.execute(
                    purpose=self.manifest.purpose,
                    endpoint=request.endpoint,
                    season=request.year,
                    cache_decision="miss",
                    transport=lambda request=request: self._invoke_transport(request),
                )
                if not isinstance(response, bytes):
                    raise PilotTransportError("pilot transport did not return response bytes")
                receipt = self._retain_response(request, response, allowance)
            except RequestBudgetExhausted as exc:
                ledger.finish(request, "blocked")
                raise PilotClaimError("RequestMeter blocked the claimed pilot request") from None
            except MeteredRequestFailed:
                ledger.finish(request, "failed")
                raise PilotTransportError("pilot transport failed") from None
            except Exception as exc:
                try:
                    ledger.finish(request, "failed")
                except PilotClaimError:
                    raise
                if isinstance(exc, PilotError):
                    raise exc
                raise PilotRetentionError("pilot response could not be retained") from None
            ledger.finish(request, "succeeded", receipt)
        return AcquisitionResult(
            pilot_id=self.manifest.pilot_id,
            manifest_sha256=self.manifest.manifest_sha256,
            allowance_id=allowance.allowance_id,
            receipts=ledger.receipts(),
            remaining_attempts=ledger.remaining(),
        )

    def _verify_existing_receipts(self, ledger: _ClaimLedger) -> None:
        """Recheck retained bytes before treating a prior claim as complete."""

        if self.store is None:  # pragma: no cover - guarded by acquire
            raise PilotSourceBindingError("private store is unavailable")
        expected = {request.request_id: request for request in self.manifest.requests}
        for receipt in ledger.receipts():
            request = expected.get(receipt.request_id)
            if request is None:
                raise PilotClaimError("pilot ledger contains an unknown request receipt")
            self._verify_receipt(request, receipt)

    def _verify_existing_receipt(self, ledger: _ClaimLedger, request: PilotRequest) -> None:
        """Verify one receipt immediately before reusing a successful claim."""

        self._verify_receipt(request, ledger.receipt_for(request))

    def _verify_receipt(self, request: PilotRequest, receipt: SupplementReceipt) -> None:
        if self.store is None:  # pragma: no cover - guarded by acquire
            raise PilotSourceBindingError("private store is unavailable")
        if (
            receipt.pilot_id != self.manifest.pilot_id
            or receipt.manifest_sha256 != self.manifest.manifest_sha256
            or receipt.source_archive_sha256 != self.manifest.source_archive_sha256
            or receipt.endpoint != request.endpoint
            or receipt.params != request.params
            or receipt.parent_snapshot_path != request.parent_snapshot_path
            or receipt.parent_snapshot_sha256 != request.parent_snapshot_sha256
            or receipt.parent_snapshot_checksum != request.parent_snapshot_checksum
        ):
            raise PilotClaimError("pilot receipt provenance does not match the frozen request")
        try:
            self.store.verify(receipt.response)
        except Exception:
            raise PilotRetentionError("a retained pilot response failed integrity verification") from None

    def _invoke_transport(self, request: PilotRequest) -> bytes:
        try:
            response = self.transport(request)
        except PilotError:
            raise
        except Exception as exc:
            raise PilotTransportError("pilot transport failed") from None
        if not isinstance(response, bytes):
            raise PilotTransportError("pilot transport did not return response bytes")
        return response

    def _retain_response(
        self, request: PilotRequest, response: bytes, allowance: PilotAllowance
    ) -> SupplementReceipt:
        if self.store is None:  # pragma: no cover - guarded by acquire
            raise PilotRetentionError("private store is unavailable")
        digest = _sha256_bytes(response)
        role = f"{self.manifest.pilot_id}.{request.request_id}"
        fd: int | None = None
        temporary: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix=".supplement-", dir=self.store.root)
            temporary = Path(name)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as output:
                fd = None
                output.write(response)
                output.flush()
                os.fsync(output.fileno())
            reference = self.store.retain(
                temporary,
                digest,
                len(response),
                role=role,
            )
        except Exception as exc:
            raise PilotRetentionError("pilot response could not be retained privately") from None
        finally:
            if fd is not None:
                os.close(fd)
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        return SupplementReceipt(
            request_id=request.request_id,
            endpoint=request.endpoint,
            params=request.params,
            response=reference,
            captured_at=self._clock().astimezone(timezone.utc).isoformat(),
            pilot_id=self.manifest.pilot_id,
            manifest_sha256=self.manifest.manifest_sha256,
            allowance_id=allowance.allowance_id,
            source_archive_sha256=self.manifest.source_archive_sha256,
            parent_snapshot_path=request.parent_snapshot_path,
            parent_snapshot_sha256=request.parent_snapshot_sha256,
            parent_snapshot_checksum=request.parent_snapshot_checksum,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cfb.excitement_source",
        description="Offline plan and explicitly allowed ADR-0021 source pilot.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    dry_run = subparsers.add_parser("dry-run", help="print the frozen plan without side effects")
    dry_run.add_argument("--pilot-config", type=Path, default=DEFAULT_CONFIG)
    acquire = subparsers.add_parser("acquire", help="run only with a fresh owner allowance")
    acquire.add_argument("--pilot-config", type=Path, default=DEFAULT_CONFIG)
    acquire.add_argument("--allowance", type=Path, required=True)
    acquire.add_argument("--source-binding", type=Path, required=True)
    acquire.add_argument("--store", type=Path, required=True)
    acquire.add_argument("--meter", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = PilotManifest.load(args.pilot_config)
        if args.command == "dry-run":
            print(json.dumps(PilotAdapter(None, manifest=manifest).dry_run(), indent=2, sort_keys=True))
            return 0
        allowance = PilotAllowance.load(args.allowance)
        binding = SourceBinding.load(args.source_binding)
        adapter = PilotAdapter(
            RequestMeter(args.meter),
            LocalInputStore(args.store),
            manifest=manifest,
        )
        print(json.dumps(adapter.acquire(allowance, binding).safe_dict(), indent=2, sort_keys=True))
        return 0
    except PilotError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "API_BASE_URL",
    "AcquisitionResult",
    "HttpSupplementTransport",
    "MAX_ATTEMPTS",
    "PILOT_ID",
    "PILOT_MANIFEST_SHA256",
    "PilotAdapter",
    "PilotAllowance",
    "PilotClaimError",
    "PilotError",
    "PilotManifest",
    "PilotManifestError",
    "PilotRequest",
    "PilotRetentionError",
    "PilotSourceBindingError",
    "PilotTransportError",
    "SOURCE_ARCHIVE_SHA256",
    "SourceBinding",
    "SupplementReceipt",
]
