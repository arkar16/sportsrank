"""Read-only Firebase Hosting adapter for complete baseline capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile
import tempfile
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

try:
    from .baseline import BaselineValidationError, VerifiedBaseline, import_baseline
    from .publication_records import (
        ManagedResourceEvidence,
        ProviderIdentity,
        ProviderTarget,
        ValidatedPackageRecord,
        canonical_json,
    )
except ImportError:  # Direct execution from the cfb directory.
    from baseline import BaselineValidationError, VerifiedBaseline, import_baseline
    from publication_records import (
        ManagedResourceEvidence,
        ProviderIdentity,
        ProviderTarget,
        ValidatedPackageRecord,
        canonical_json,
    )


_API = "https://firebasehosting.googleapis.com/v1beta1/"


@dataclass(frozen=True)
class ProviderDownload:
    raw: bytes
    provider_payload: bytes
    verification: str
    url: str
    etag: str | None = None
    last_modified: str | None = None


class FirebaseReadBackend(Protocol):
    """GET-only operations required by the baseline capture module."""

    def channel(self, target: ProviderTarget) -> Mapping[str, Any]: ...
    def version(self, identity: ProviderIdentity) -> Mapping[str, Any]: ...
    def inventory(self, identity: ProviderIdentity) -> Sequence[Mapping[str, Any]]: ...
    def download(self, target: ProviderTarget, file: Mapping[str, Any]) -> ProviderDownload: ...


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _provider_identity(channel: Mapping[str, Any], target: ProviderTarget) -> ProviderIdentity:
    try:
        release = channel["release"]
        version = release["version"]
        return ProviderIdentity.from_value(
            {"release": release["name"], "version": version["name"]}, target=target
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BaselineValidationError("live Firebase channel has no valid release/version") from exc


def _safe_inventory_path(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(part in {"", ".", ".."} for part in value[1:].split("/"))
    ):
        raise BaselineValidationError("Firebase inventory contains an unsafe path")
    return value[1:]


def _verify_download(body: bytes, encoding: str | None, expected: str) -> tuple[bytes, bytes, str]:
    if encoding in (None, "", "identity"):
        raw = body
    elif encoding == "gzip":
        try:
            raw = gzip.decompress(body)
        except (OSError, EOFError) as exc:
            raise BaselineValidationError("Firebase returned invalid gzip content") from exc
        if _digest(body) == expected:
            return raw, body, "exact-provider-gzip"
    else:
        raise BaselineValidationError("Firebase returned an unsupported content encoding")
    for level in (9, 6, 1, 2, 3, 4, 5, 7, 8, 0):
        encoded = gzip.compress(raw, compresslevel=level, mtime=0)
        for os_byte in (encoded[9], 3, 0, 255):
            candidate = bytearray(encoded)
            candidate[9] = os_byte
            payload = bytes(candidate)
            if _digest(payload) == expected:
                return raw, payload, f"reencoded-gzip-level-{level}-os-{os_byte}"
    raise BaselineValidationError("public bytes do not reconcile to the provider upload hash")


class FirebaseRestReadBackend:
    """Concrete authenticated GET-only Firebase Hosting backend.

    The caller supplies a short-lived access-token function.  Tokens remain in
    request headers and are never written into capture evidence.
    """

    def __init__(self, access_token: Callable[[], str], *, timeout: float = 30.0) -> None:
        self._access_token = access_token
        self._timeout = timeout

    def _get(self, resource: str, query: Mapping[str, str] | None = None) -> tuple[bytes, Mapping[str, str]]:
        url = _API + resource
        if query:
            url += "?" + urlencode({key: value for key, value in query.items() if value})
        token = self._access_token()
        if not isinstance(token, str) or not token:
            raise BaselineValidationError("Firebase access token is unavailable")
        request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "identity"})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                body = response.read(64 * 1024 * 1024 + 1)
                if len(body) > 64 * 1024 * 1024:
                    raise BaselineValidationError("Firebase response exceeds the read limit")
                return body, {key.lower(): value for key, value in response.headers.items()}
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise BaselineValidationError("Firebase read failed") from exc

    def _json(self, resource: str, query: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        body, _ = self._get(resource, query)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BaselineValidationError("Firebase metadata response is not JSON") from exc
        if not isinstance(value, Mapping):
            raise BaselineValidationError("Firebase metadata response is not an object")
        return value

    def channel(self, target: ProviderTarget) -> Mapping[str, Any]:
        return self._json(f"sites/{target.site}/channels/{target.channel}")

    def version(self, identity: ProviderIdentity) -> Mapping[str, Any]:
        return self._json(identity.version)

    def inventory(self, identity: ProviderIdentity) -> Sequence[Mapping[str, Any]]:
        files: list[Mapping[str, Any]] = []
        paths: set[str] = set()
        tokens: set[str] = set()
        token = ""
        while True:
            page = self._json(
                f"{identity.version}/files",
                {"status": "ACTIVE", "pageSize": "1000", "pageToken": token},
            )
            values = page.get("files", [])
            if not isinstance(values, list):
                raise BaselineValidationError("Firebase inventory page is invalid")
            for value in values:
                if not isinstance(value, Mapping):
                    raise BaselineValidationError("Firebase inventory entry is invalid")
                relative = _safe_inventory_path(value.get("path"))
                if relative in paths or value.get("status") != "ACTIVE":
                    raise BaselineValidationError("Firebase inventory is duplicate or mixed")
                provider_sha = value.get("hash")
                if not isinstance(provider_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", provider_sha):
                    raise BaselineValidationError("Firebase inventory hash is invalid")
                paths.add(relative)
                files.append({"path": value["path"], "relative": relative, "provider_sha256": provider_sha.lower(), "status": "ACTIVE"})
            next_token = page.get("nextPageToken", "")
            if not next_token:
                break
            if not isinstance(next_token, str) or next_token in tokens or len(files) > 100_000:
                raise BaselineValidationError("Firebase inventory pagination is invalid")
            tokens.add(next_token)
            token = next_token
        return sorted(files, key=lambda value: str(value["relative"]))

    def download(self, target: ProviderTarget, file: Mapping[str, Any]) -> ProviderDownload:
        relative = _safe_inventory_path(file.get("path"))
        url = f"https://{target.site}.web.app/" + "/".join(quote(part, safe="") for part in relative.split("/"))
        request = Request(url, headers={"Accept-Encoding": "gzip", "Cache-Control": "no-cache"})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                body = response.read(64 * 1024 * 1024 + 1)
                if response.geturl() != url or response.status != 200 or len(body) > 64 * 1024 * 1024:
                    raise BaselineValidationError("Firebase public resource retrieval failed")
                headers = {key.lower(): value for key, value in response.headers.items()}
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise BaselineValidationError("Firebase public resource retrieval failed") from exc
        raw, payload, method = _verify_download(body, headers.get("content-encoding"), str(file["provider_sha256"]))
        return ProviderDownload(raw, payload, method, url, headers.get("etag"), headers.get("last-modified"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _archive_evidence(root: Path, archive: Path) -> str:
    with tarfile.open(archive, "w:gz") as target:
        for path in sorted(root.rglob("*")):
            target.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return _digest(archive.read_bytes())


class FirebaseReadAdapter:
    """Capture one stable complete live baseline through a read-only backend."""

    def __init__(self, target: Mapping[str, Any] | ProviderTarget, backend: FirebaseReadBackend) -> None:
        self.target = ProviderTarget.from_value(target)
        self.backend = backend

    def observe(self) -> ProviderIdentity:
        return _provider_identity(self.backend.channel(self.target), self.target)

    def capture(self, destination: str | Path, *, materialize_to: str | Path | None = None) -> VerifiedBaseline:
        destination = Path(destination)
        if destination.exists() and any(destination.iterdir()):
            raise BaselineValidationError("capture destination must be absent or empty")
        destination.mkdir(parents=True, exist_ok=True)
        evidence = destination / "evidence"
        evidence.mkdir()
        started = datetime.now(timezone.utc).isoformat()
        before_raw = self.backend.channel(self.target)
        before = _provider_identity(before_raw, self.target)
        version = self.backend.version(before)
        if version.get("name") != before.version or version.get("status") != "FINALIZED":
            raise BaselineValidationError("live Firebase version is not finalized")
        inventory = list(self.backend.inventory(before))
        try:
            finalized_count = int(version.get("fileCount"))
        except (TypeError, ValueError) as exc:
            raise BaselineValidationError("live Firebase version has no file count") from exc
        if finalized_count != len(inventory):
            raise BaselineValidationError("Firebase inventory does not match finalized file count")
        _write_json(evidence / "channel-before.json", before_raw)
        _write_json(evidence / "version.json", version)
        _write_json(evidence / "inventory.json", inventory)
        captured = []
        total_bytes = 0
        for file in inventory:
            download = self.backend.download(self.target, file)
            relative = _safe_inventory_path(file["path"])
            raw_path = evidence / "site" / relative
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(download.raw)
            provider_sha = str(file["provider_sha256"])
            payload_path = evidence / "provider-payloads" / f"{provider_sha}.gz"
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            if not payload_path.exists():
                payload_path.write_bytes(download.provider_payload)
            total_bytes += len(download.raw)
            captured.append({
                **file,
                "sha256": _digest(download.raw),
                "bytes": len(download.raw),
                "verification": download.verification,
                "provider_payload": f"provider-payloads/{provider_sha}.gz",
                "url": download.url,
                "etag": download.etag,
                "last_modified": download.last_modified,
            })
        after_raw = self.backend.channel(self.target)
        after = _provider_identity(after_raw, self.target)
        if after != before:
            raise BaselineValidationError("live provider identity changed during capture")
        version_after = self.backend.version(after)
        if version_after.get("config", {}) != version.get("config", {}):
            raise BaselineValidationError("live provider configuration changed during capture")
        _write_json(evidence / "channel-after.json", after_raw)
        metadata_sha = {
            name: _digest((evidence / name).read_bytes())
            for name in ("channel-before.json", "channel-after.json", "version.json", "inventory.json")
        }
        config_bytes = json.dumps(version.get("config", {}), separators=(",", ":"), ensure_ascii=False).encode()
        _write_json(evidence / "capture.json", {
            "schema_version": 1,
            "site": self.target.site,
            "started_at": started,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "live": before.to_dict(include_target=False),
            "source_commit": None,
            "source_commit_status": "unknown",
            "status": "verified",
            "file_count": len(captured),
            "total_bytes": total_bytes,
            "files": sorted(captured, key=lambda value: str(value["relative"])),
            "config_sha256": _digest(config_bytes),
            "metadata_sha256": metadata_sha,
        })
        archive = destination / "baseline.tar.gz"
        archive_sha = _archive_evidence(evidence, archive)
        if materialize_to is None:
            materialize_to = destination / "application-site"
        return import_baseline(
            archive,
            target=self.target,
            expected_archive_sha256=archive_sha,
            materialize_to=materialize_to,
        )


class FakeFirebaseReadBackend:
    """Offline Firebase read adapter used at the external I/O seam."""

    def __init__(
        self,
        target: Mapping[str, Any] | ProviderTarget,
        files: Mapping[str, bytes],
        *,
        serving_config: Mapping[str, Any],
        change_after_capture: bool = False,
        reported_file_count: int | None = None,
        change_configuration_after_capture: bool = False,
    ) -> None:
        self.target = ProviderTarget.from_value(target)
        self.files = dict(files)
        self.serving_config = dict(serving_config)
        self.change_after_capture = change_after_capture
        self.reported_file_count = reported_file_count
        self.change_configuration_after_capture = change_configuration_after_capture
        self.release = f"sites/{self.target.site}/channels/live/releases/fake-release"
        self.version_name = f"sites/{self.target.site}/versions/fake-version"
        self._channel_reads = 0
        self.write_count = 0
        self._entries: list[dict[str, str]] = []
        self._payloads: dict[str, bytes] = {}
        for relative, raw in sorted(self.files.items()):
            relative = relative.removeprefix("/")
            payload = gzip.compress(raw, compresslevel=9, mtime=0)
            provider_sha = _digest(payload)
            self._payloads[relative] = payload
            self._entries.append({"path": f"/{relative}", "relative": relative, "provider_sha256": provider_sha, "status": "ACTIVE"})

    def channel(self, target: ProviderTarget) -> Mapping[str, Any]:
        self._channel_reads += 1
        release = self.release
        if self.change_after_capture and self._channel_reads > 1:
            release = f"sites/{self.target.site}/channels/live/releases/changed"
        return {"name": f"sites/{self.target.site}/channels/live", "release": {"name": release, "version": {"name": self.version_name}}}

    def version(self, identity: ProviderIdentity) -> Mapping[str, Any]:
        config = dict(self.serving_config)
        if self.change_configuration_after_capture and self._channel_reads > 1:
            config["cleanUrls"] = not bool(config.get("cleanUrls", False))
        count = len(self._entries) if self.reported_file_count is None else self.reported_file_count
        return {"name": identity.version, "status": "FINALIZED", "config": config, "fileCount": str(count)}

    def inventory(self, identity: ProviderIdentity) -> Sequence[Mapping[str, Any]]:
        return tuple(dict(value) for value in self._entries)

    def download(self, target: ProviderTarget, file: Mapping[str, Any]) -> ProviderDownload:
        relative = str(file["relative"])
        return ProviderDownload(self.files[relative], self._payloads[relative], "exact-provider-gzip", f"https://{self.target.site}.web.app/{relative}")


class FirebasePublicationError(RuntimeError):
    """Provider input, receipt, or verification failed closed."""


class ProviderRejectedError(FirebasePublicationError):
    """Firebase returned a definite rejection response to a write."""


class ProviderWriteUncertain(FirebasePublicationError):
    """A write was sent but its response was lost or unreadable."""


_DEFINITE_REJECTION_HTTP_STATUSES = frozenset({
    400, 401, 403, 404, 405, 410, 411, 413, 414, 415, 422,
})


def _raise_write_http_failure(status: int) -> None:
    """Classify only unambiguously client-rejected requests as rejected."""

    if (
        not isinstance(status, bool)
        and isinstance(status, int)
        and status in _DEFINITE_REJECTION_HTTP_STATUSES
    ):
        raise ProviderRejectedError("Firebase rejected a provider write")
    raise ProviderWriteUncertain(
        "Firebase write returned an ambiguous HTTP response"
    )


def _deterministic_gzip(value: bytes) -> bytes:
    payload = bytearray(gzip.compress(value, compresslevel=9, mtime=0))
    # Pin RFC 1952 OS to unknown so provider hashes do not vary by runner OS.
    payload[9] = 255
    return bytes(payload)


def _serving_config(firebase_json: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(firebase_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FirebasePublicationError("packaged firebase.json is invalid") from exc
    if not isinstance(value, Mapping) or set(value) != {"hosting"}:
        raise FirebasePublicationError("packaged Firebase configuration is unsupported")
    hosting = value["hosting"]
    if not isinstance(hosting, Mapping) or set(hosting) - {"public", "ignore"}:
        raise FirebasePublicationError("packaged Hosting configuration is unsupported")
    if hosting.get("public") != "website" or not isinstance(hosting.get("ignore", []), list):
        raise FirebasePublicationError("packaged Hosting root is invalid")
    # public/ignore are CLI packaging inputs, not REST ServingConfig fields.
    return MappingProxyType({})


@dataclass(frozen=True)
class FirebaseDeployArtifact:
    """Exact retrieved package bytes materialized once for Firebase."""

    package: ValidatedPackageRecord
    files: Mapping[str, bytes]
    firebase_json: bytes
    serving_config: Mapping[str, Any]
    provider_hashes: Mapping[str, str]
    provider_payloads: Mapping[str, bytes]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))
        object.__setattr__(
            self, "serving_config", MappingProxyType(dict(self.serving_config))
        )
        object.__setattr__(
            self, "provider_hashes", MappingProxyType(dict(self.provider_hashes))
        )
        object.__setattr__(
            self, "provider_payloads", MappingProxyType(dict(self.provider_payloads))
        )

    @classmethod
    def from_archive(
        cls, archive: str | Path, package: ValidatedPackageRecord
    ) -> "FirebaseDeployArtifact":
        path = Path(archive)
        if not path.is_file() or _digest(path.read_bytes()) != package.bundle_sha256:
            raise FirebasePublicationError(
                "retrieved package does not match the validated bundle"
            )
        files: dict[str, bytes] = {}
        firebase_json: bytes | None = None
        try:
            with tarfile.open(path, "r:gz") as source:
                names: set[str] = set()
                for member in source.getmembers():
                    name = member.name
                    parts = Path(name).parts
                    if (
                        not member.isfile()
                        or name in names
                        or name.startswith("/")
                        or ".." in parts
                        or "\\" in name
                    ):
                        raise FirebasePublicationError(
                            "retrieved package inventory is unsafe"
                        )
                    names.add(name)
                    stream = source.extractfile(member)
                    if stream is None:
                        raise FirebasePublicationError(
                            "retrieved package member is unreadable"
                        )
                    value = stream.read()
                    if name == "firebase.json":
                        firebase_json = value
                    elif name.startswith("website/"):
                        relative = name.removeprefix("website/")
                        _safe_deploy_path(relative)
                        files[relative] = value
                    else:
                        raise FirebasePublicationError(
                            "retrieved package has an unexpected member"
                        )
        except (OSError, tarfile.TarError) as exc:
            raise FirebasePublicationError("retrieved package is unreadable") from exc
        if firebase_json is None or not files:
            raise FirebasePublicationError("retrieved package is incomplete")
        inventory = tuple(
            (f"website/{name}", _digest(value), len(value))
            for name, value in sorted(files.items())
        )
        inventory_sha = _digest(canonical_json({
            "files": [
                {"path": name, "sha256": sha, "size": size}
                for name, sha, size in inventory
            ]
        }))
        if inventory_sha != package.inventory_sha256:
            raise FirebasePublicationError(
                "retrieved package inventory differs from the validated package"
            )
        if _digest(firebase_json) != package.configuration_sha256:
            raise FirebasePublicationError(
                "retrieved firebase.json differs from the validated package"
            )
        hashes: dict[str, str] = {}
        payloads: dict[str, bytes] = {}
        for relative, value in sorted(files.items()):
            payload = _deterministic_gzip(value)
            provider_sha = _digest(payload)
            hashes[f"/{relative}"] = provider_sha
            payloads.setdefault(provider_sha, payload)
        return cls(
            package, files, firebase_json, _serving_config(firebase_json),
            hashes, payloads,
        )


def _safe_deploy_path(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or value.startswith("__/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise FirebasePublicationError("package contains an unsafe Firebase path")
    return value


@dataclass(frozen=True)
class PublicResource:
    path: str
    body: bytes
    url: str
    status: int


class FirebasePublicationBackend(Protocol):
    def observe(self, target: ProviderTarget) -> ProviderIdentity: ...
    def create_version(
        self, target: ProviderTarget, config: Mapping[str, Any], labels: Mapping[str, str]
    ) -> Mapping[str, Any]: ...
    def populate_files(
        self, version: str, files: Mapping[str, str]
    ) -> Mapping[str, Any]: ...
    def upload_file(self, upload_url: str, digest: str, payload: bytes) -> None: ...
    def inventory(self, version: str) -> Sequence[Mapping[str, Any]]: ...
    def version(self, version: str) -> Mapping[str, Any]: ...
    def finalize_version(self, version: str) -> Mapping[str, Any]: ...
    def release_version(
        self, target: ProviderTarget, version: str
    ) -> Mapping[str, Any]: ...
    def public_resource(self, target: ProviderTarget, path: str) -> PublicResource: ...


class FirebaseRestPublicationBackend:
    """Concrete Hosting REST backend with explicit uncertain-write errors."""

    def __init__(
        self, access_token: Callable[[], str], *, timeout: float = 30.0
    ) -> None:
        self._access_token = access_token
        self._timeout = timeout

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
        write: bool = False,
        authenticated: bool = True,
    ) -> tuple[bytes, str, int]:
        headers = {"Accept-Encoding": "identity"}
        if body is not None:
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(len(body))
        if authenticated:
            token = self._access_token()
            if not isinstance(token, str) or not token:
                raise FirebasePublicationError("Firebase access token is unavailable")
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self._timeout) as response:
                value = response.read(64 * 1024 * 1024 + 1)
                final_url = response.geturl()
                status = response.status
        except HTTPError as exc:
            if write:
                try:
                    _raise_write_http_failure(exc.code)
                except FirebasePublicationError as classified:
                    raise classified from exc
            raise FirebasePublicationError("Firebase provider read failed") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if write:
                raise ProviderWriteUncertain(
                    "Firebase write response is unavailable"
                ) from exc
            raise FirebasePublicationError("Firebase provider read failed") from exc
        if len(value) > 64 * 1024 * 1024 or final_url != url:
            if write:
                raise ProviderWriteUncertain(
                    "Firebase write response identity is unavailable"
                )
            raise FirebasePublicationError("Firebase provider read is unsafe")
        if isinstance(status, bool) or not isinstance(status, int):
            if write:
                raise ProviderWriteUncertain(
                    "Firebase write response status is invalid"
                )
            raise FirebasePublicationError(
                "Firebase provider read status is invalid"
            )
        if status < 200 or status >= 300:
            if write:
                _raise_write_http_failure(status)
            raise FirebasePublicationError("Firebase provider read failed")
        return value, final_url, status

    def _json(
        self,
        method: str,
        resource: str,
        *,
        query: Mapping[str, str] | None = None,
        value: Mapping[str, Any] | None = None,
        write: bool = False,
    ) -> Mapping[str, Any]:
        url = _API + resource
        if query:
            url += "?" + urlencode(query)
        body = canonical_json(value) if value is not None else None
        raw, _, _ = self._request(method, url, body=body, write=write)
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if write:
                raise ProviderWriteUncertain(
                    "Firebase write receipt is not valid JSON"
                ) from exc
            raise FirebasePublicationError("Firebase provider response is not JSON") from exc
        if not isinstance(result, Mapping):
            if write:
                raise ProviderWriteUncertain(
                    "Firebase write receipt is not an object"
                )
            raise FirebasePublicationError("Firebase provider response is invalid")
        return result

    def observe(self, target: ProviderTarget) -> ProviderIdentity:
        channel = self._json(
            "GET", f"sites/{target.site}/channels/{target.channel}"
        )
        try:
            return _provider_identity(channel, target)
        except BaselineValidationError as exc:
            raise FirebasePublicationError(
                "Firebase live identity response is invalid"
            ) from exc

    def create_version(
        self, target: ProviderTarget, config: Mapping[str, Any], labels: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return self._json(
            "POST", f"sites/{target.site}/versions",
            value={"config": dict(config), "labels": dict(labels)}, write=True,
        )

    def populate_files(
        self, version: str, files: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return self._json(
            "POST", f"{version}:populateFiles", value={"files": dict(files)},
            write=True,
        )

    def upload_file(self, upload_url: str, digest: str, payload: bytes) -> None:
        _, _, status = self._request(
            "POST", f"{upload_url}/{digest}", body=payload,
            content_type="application/octet-stream", write=True,
        )
        if status != 200:
            raise ProviderWriteUncertain(
                "Firebase upload receipt did not return HTTP 200"
            )

    def inventory(self, version: str) -> Sequence[Mapping[str, Any]]:
        files: list[Mapping[str, Any]] = []
        tokens: set[str] = set()
        token = ""
        while True:
            page = self._json(
                "GET", f"{version}/files",
                query={"status": "ACTIVE", "pageSize": "1000", "pageToken": token},
            )
            values = page.get("files", [])
            if not isinstance(values, list) or any(
                not isinstance(item, Mapping) for item in values
            ):
                raise FirebasePublicationError("Firebase version inventory is invalid")
            files.extend(values)
            next_token = page.get("nextPageToken", "")
            if not next_token:
                break
            if (
                not isinstance(next_token, str)
                or next_token in tokens
                or len(files) > 100_000
            ):
                raise FirebasePublicationError(
                    "Firebase version inventory pagination is invalid"
                )
            tokens.add(next_token)
            token = next_token
        return tuple(files)

    def version(self, version: str) -> Mapping[str, Any]:
        return self._json("GET", version)

    def finalize_version(self, version: str) -> Mapping[str, Any]:
        return self._json(
            "PATCH", version, query={"update_mask": "status"},
            value={"status": "FINALIZED"}, write=True,
        )

    def release_version(
        self, target: ProviderTarget, version: str
    ) -> Mapping[str, Any]:
        return self._json(
            "POST", f"sites/{target.site}/channels/{target.channel}/releases",
            query={"versionName": version}, value={}, write=True,
        )

    def public_resource(self, target: ProviderTarget, path: str) -> PublicResource:
        if not path.startswith("/") or path.startswith("//"):
            raise FirebasePublicationError("public resource path is invalid")
        encoded = "/".join(quote(part, safe="") for part in path[1:].split("/"))
        url = f"https://{target.site}.web.app/{encoded}"
        body, final_url, status = self._request(
            "GET", url, authenticated=False
        )
        return PublicResource(path, body, final_url, status)


def _provider_inventory(
    values: Sequence[Mapping[str, Any]],
) -> Mapping[str, str]:
    if not values or len(values) > 100_000:
        raise FirebasePublicationError("Firebase version inventory size is invalid")
    result: dict[str, str] = {}
    for value in values:
        path = value.get("path")
        provider_sha = value.get("hash")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path in result
            or path.startswith("/__/")
            or value.get("status") != "ACTIVE"
            or not isinstance(provider_sha, str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", provider_sha)
        ):
            raise FirebasePublicationError("Firebase version inventory is invalid")
        _safe_deploy_path(path[1:])
        result[path] = provider_sha.lower()
    return MappingProxyType(result)


def _configuration_digest(value: Mapping[str, Any]) -> str:
    return _digest(
        json.dumps(dict(value), separators=(",", ":"), ensure_ascii=False).encode()
    )


@dataclass(frozen=True)
class FirebaseDeploymentReceipt:
    identity: ProviderIdentity
    source: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", MappingProxyType(dict(self.source)))


@dataclass(frozen=True)
class FirebaseVerificationObservation:
    outcome: str
    identity: ProviderIdentity | None
    inventory_sha256: str | None
    configuration_sha256: str | None
    managed_findings: Mapping[str, str]
    public_findings: Mapping[str, str]
    findings: tuple[str, ...]
    source: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "managed_findings", MappingProxyType(dict(self.managed_findings))
        )
        object.__setattr__(
            self, "public_findings", MappingProxyType(dict(self.public_findings))
        )
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "source", MappingProxyType(dict(self.source)))


class FirebasePublicationAdapter:
    """Exact-artifact Firebase deploy and complete post-write verification."""

    def __init__(
        self, target: Mapping[str, Any] | ProviderTarget,
        backend: FirebasePublicationBackend,
    ) -> None:
        self.target = ProviderTarget.from_value(target)
        self.backend = backend

    def observe(self) -> ProviderIdentity:
        return self.backend.observe(self.target)

    def deploy(
        self, artifact: FirebaseDeployArtifact, *, attempt_id: str
    ) -> FirebaseDeploymentReceipt:
        created = self.backend.create_version(
            self.target, artifact.serving_config, {},
        )
        version = created.get("name")
        if (
            not isinstance(version, str)
            or not re.fullmatch(
                re.escape(f"sites/{self.target.site}/versions/") + r"[A-Za-z0-9_-]+",
                version,
            )
            or created.get("status") != "CREATED"
            or created.get("config", {}) != dict(artifact.serving_config)
        ):
            raise ProviderWriteUncertain("Firebase create-version receipt is invalid")

        items = sorted(artifact.provider_hashes.items())
        required: set[str] = set()
        expected_upload = (
            "https://upload-firebasehosting.googleapis.com/upload/"
            f"sites/{self.target.site}/versions/{version.rsplit('/', 1)[-1]}/files"
        )
        for offset in range(0, len(items), 1000):
            response = self.backend.populate_files(
                version, dict(items[offset:offset + 1000])
            )
            hashes = response.get("uploadRequiredHashes", [])
            upload_url = response.get("uploadUrl")
            if (
                not isinstance(hashes, list)
                or any(not isinstance(value, str) for value in hashes)
                or not set(hashes).issubset(artifact.provider_payloads)
                or upload_url != expected_upload
            ):
                raise ProviderWriteUncertain("Firebase populate receipt is invalid")
            required.update(hashes)
        for provider_sha in sorted(required):
            self.backend.upload_file(
                expected_upload, provider_sha,
                artifact.provider_payloads[provider_sha],
            )

        try:
            inventory = _provider_inventory(self.backend.inventory(version))
        except FirebasePublicationError as exc:
            raise ProviderWriteUncertain(
                "Firebase pre-finalization inventory receipt is unavailable"
            ) from exc
        if dict(inventory) != dict(artifact.provider_hashes):
            raise ProviderWriteUncertain(
                "Firebase pre-finalization inventory is incomplete"
            )
        finalized = self.backend.finalize_version(version)
        if (
            finalized.get("name") != version
            or finalized.get("status") != "FINALIZED"
            or finalized.get("config", {}) != dict(artifact.serving_config)
            or str(finalized.get("fileCount")) != str(len(artifact.files))
        ):
            raise ProviderWriteUncertain("Firebase finalize receipt is invalid")
        released = self.backend.release_version(self.target, version)
        try:
            identity = ProviderIdentity.from_value(
                {
                    "release": released["name"],
                    "version": released["version"]["name"],
                },
                target=self.target,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderWriteUncertain("Firebase release receipt is invalid") from exc
        try:
            observed = self.backend.observe(self.target)
        except FirebasePublicationError as exc:
            raise ProviderWriteUncertain(
                "Firebase live release observation is unavailable"
            ) from exc
        if (
            identity.version != version
            or released.get("type") != "DEPLOY"
            or released.get("version", {}).get("status") != "FINALIZED"
            or not isinstance(released.get("releaseTime"), str)
            or observed != identity
        ):
            raise ProviderWriteUncertain("Firebase live release receipt is incomplete")
        return FirebaseDeploymentReceipt(
            identity,
            {
                "schema_version": 1,
                "record_type": "firebase_deployment_observation",
                "outcome": "accepted",
                "release": identity.release,
                "version": identity.version,
                "file_count": len(artifact.files),
                "provider_inventory_sha256": _digest(canonical_json({
                    "files": dict(sorted(artifact.provider_hashes.items()))
                })),
                "serving_configuration_sha256": _configuration_digest(
                    artifact.serving_config
                ),
                "version_bytes": str(finalized.get("versionBytes", "unknown")),
            },
        )

    def verify(
        self,
        artifact: FirebaseDeployArtifact,
        identity: ProviderIdentity,
        *,
        expected_managed: Sequence[ManagedResourceEvidence],
    ) -> FirebaseVerificationObservation:
        from tools.scripts.postdeploy_smoke import REQUIRED_PATHS, SmokeError, run_smoke

        findings: list[str] = []
        managed_findings: dict[str, str] = {}
        public_findings: dict[str, str] = {}
        observed: ProviderIdentity | None = None
        inventory_sha: str | None = None
        configuration_sha: str | None = None
        expected_identity = {
            key: expected_managed[0].app_identity[key]
            for key in ("project_id", "messaging_sender_id", "auth_domain", "storage_bucket")
        } if len(expected_managed) == 2 else {}
        try:
            if tuple(item.path for item in expected_managed) != (
                "/__/firebase/init.js", "/__/firebase/init.json"
            ):
                raise FirebasePublicationError(
                    "managed-resource expectation is incomplete"
                )
            if (
                expected_managed[0].app_identity
                != expected_managed[1].app_identity
                or expected_identity.get("project_id") != self.target.project
            ):
                raise FirebasePublicationError(
                    "managed-resource expectation has the wrong app identity"
                )
            observed = self.backend.observe(self.target)
            if observed != identity:
                raise FirebasePublicationError("live identity differs from deployment")
            version = self.backend.version(identity.version)
            if (
                version.get("name") != identity.version
                or version.get("status") != "FINALIZED"
                or str(version.get("fileCount")) != str(len(artifact.files))
            ):
                raise FirebasePublicationError("deployed version receipt is invalid")
            if version.get("config", {}) != dict(artifact.serving_config):
                raise FirebasePublicationError("serving configuration differs")
            configuration_sha = artifact.package.configuration_sha256
            inventory = _provider_inventory(self.backend.inventory(identity.version))
            if dict(inventory) != dict(artifact.provider_hashes):
                raise FirebasePublicationError("complete provider inventory differs")

            for relative, expected in artifact.files.items():
                resource = self._public(f"/{relative}")
                if resource.body != expected:
                    raise FirebasePublicationError(
                        f"public application bytes differ: /{relative}"
                    )
            inventory_sha = artifact.package.inventory_sha256

            identities: list[Mapping[str, str]] = []
            for path in ("/__/firebase/init.js", "/__/firebase/init.json"):
                resource = self._public(path)
                app = _managed_app_identity(path, resource.body)
                identities.append(app)
                if app != expected_identity:
                    raise FirebasePublicationError(
                        f"managed resource app identity differs: {path}"
                    )
                managed_findings[path] = f"verified:sha256:{_digest(resource.body)}"
            if identities[0] != identities[1]:
                raise FirebasePublicationError(
                    "managed Firebase configurations disagree"
                )

            with tempfile.TemporaryDirectory(prefix="sportsrank-public-smoke-") as directory:
                site = Path(directory)
                for relative, value in artifact.files.items():
                    destination = site / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(value)

                def fetch_page(base_url: str, relative: str, timeout: float) -> bytes:
                    path = "/" if relative == "index.html" else f"/{relative}"
                    return self._public(path).body

                run_smoke(
                    f"https://{self.target.site}.web.app", site,
                    attempts=1, retry_delay=0, fetch_page=fetch_page,
                )
            for path in REQUIRED_PATHS:
                public_findings[f"/{path}"] = "verified:exact-bytes"
            findings.append("complete inventory, configuration, managed resources, and public pages verified")
        except (FirebasePublicationError, SmokeError) as exc:
            findings.append(str(exc))
            outcome = "unknown" if observed is None else "failed"
        else:
            outcome = "verified"

        source = {
            "schema_version": 1,
            "record_type": "firebase_verification_observation",
            "outcome": outcome,
            "release": observed.release if observed is not None else None,
            "version": observed.version if observed is not None else None,
            "inventory_sha256": inventory_sha,
            "configuration_sha256": configuration_sha,
            "managed_resource_findings": dict(managed_findings),
            "public_page_findings": dict(public_findings),
            "findings": list(findings),
        }
        return FirebaseVerificationObservation(
            outcome, observed, inventory_sha, configuration_sha,
            managed_findings, public_findings, tuple(findings), source,
        )

    def _public(self, path: str) -> PublicResource:
        resource = self.backend.public_resource(self.target, path)
        expected_path = "" if path == "/" else "/".join(
            quote(part, safe="") for part in path.split("/")
        )
        expected_url = f"https://{self.target.site}.web.app{expected_path or '/'}"
        if (
            resource.status != 200
            or resource.url != expected_url
            or len(resource.body) > 64 * 1024 * 1024
        ):
            raise FirebasePublicationError("public resource retrieval is unsafe")
        return resource


def _managed_app_identity(path: str, body: bytes) -> Mapping[str, str]:
    try:
        text = body.decode("utf-8")
        if path.endswith("init.js"):
            prefix = "firebase.initializeApp("
            if not text.startswith(prefix) or not text.rstrip().endswith(");"):
                raise ValueError
            text = text[len(prefix):text.rfind(");")]
        value = json.loads(text)
        if not isinstance(value, Mapping):
            raise ValueError
        source = value.get("config", value)
        if not isinstance(source, Mapping):
            raise ValueError
        names = {
            "project_id": "projectId",
            "messaging_sender_id": "messagingSenderId",
            "auth_domain": "authDomain",
            "storage_bucket": "storageBucket",
        }
        result = {target: source[key] for target, key in names.items()}
        if any(not isinstance(item, str) or not item for item in result.values()):
            raise ValueError
        return MappingProxyType(result)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise FirebasePublicationError(
            f"managed Firebase configuration is invalid: {path}"
        ) from exc


class FakeFirebasePublicationBackend:
    """Stateful offline fake for all Firebase publication writes and reads."""

    def __init__(
        self,
        target: Mapping[str, Any] | ProviderTarget,
        predecessor: ProviderIdentity,
        *,
        initial_files: Mapping[str, bytes] | None = None,
        initial_config: Mapping[str, Any] | None = None,
        managed_identity: Mapping[str, str],
        reject_at: str | None = None,
        lose_response_at: str | None = None,
        verification_failure: str | None = None,
        stale_identity: ProviderIdentity | None = None,
    ) -> None:
        self.target = ProviderTarget.from_value(target)
        if predecessor.target != self.target:
            raise FirebasePublicationError("fake predecessor target differs")
        self.live = stale_identity or predecessor
        self.initial_predecessor = predecessor
        self.files = dict(initial_files or {})
        self.config = dict(initial_config or {})
        self.managed_identity = dict(managed_identity)
        self.reject_at = reject_at
        self.lose_response_at = lose_response_at
        self.verification_failure = verification_failure
        self.write_count = 0
        self.write_steps: list[str] = []
        self.populate_batch_sizes: list[int] = []
        self.uploaded_payloads: dict[str, bytes] = {}
        self._expected: dict[str, str] = {}
        self._deployment_number = 0
        self._created_version = f"sites/{self.target.site}/versions/published-version-0"
        self._created_status = "NONE"
        self._released_files: dict[str, bytes] | None = None
        self._observation_reads = 0

    def _write(self, step: str, *, apply: Callable[[], None] | None = None) -> None:
        self.write_count += 1
        self.write_steps.append(step)
        if self.reject_at == step:
            raise ProviderRejectedError(f"fake definite rejection at {step}")
        if apply is not None:
            apply()
        if self.lose_response_at == step:
            raise ProviderWriteUncertain(f"fake lost response at {step}")

    def observe(self, target: ProviderTarget) -> ProviderIdentity:
        if target != self.target:
            raise FirebasePublicationError("fake observation target differs")
        self._observation_reads += 1
        if (
            self.verification_failure == "observation"
            and self._observation_reads >= 3
        ):
            raise FirebasePublicationError("fake live observation is unavailable")
        return self.live

    def create_version(
        self, target: ProviderTarget, config: Mapping[str, Any], labels: Mapping[str, str]
    ) -> Mapping[str, Any]:
        def apply() -> None:
            self._deployment_number += 1
            self._created_version = (
                f"sites/{self.target.site}/versions/"
                f"published-version-{self._deployment_number}"
            )
            self._created_status = "CREATED"
            self.config = dict(config)
            self._expected.clear()
            self.uploaded_payloads.clear()
        self._write("create", apply=apply)
        return {"name": self._created_version, "status": "CREATED", "config": dict(config)}

    def populate_files(
        self, version: str, files: Mapping[str, str]
    ) -> Mapping[str, Any]:
        self.populate_batch_sizes.append(len(files))
        def apply() -> None:
            self._expected.update(files)
        self._write("populate", apply=apply)
        return {
            "uploadRequiredHashes": sorted(set(files.values())),
            "uploadUrl": (
                "https://upload-firebasehosting.googleapis.com/upload/"
                f"sites/{self.target.site}/versions/"
                f"{self._created_version.rsplit('/', 1)[-1]}/files"
            ),
        }

    def upload_file(self, upload_url: str, digest: str, payload: bytes) -> None:
        self._write("upload", apply=lambda: self.uploaded_payloads.setdefault(digest, payload))

    def inventory(self, version: str) -> Sequence[Mapping[str, Any]]:
        values = [
            {"path": path, "hash": digest, "status": "ACTIVE"}
            for path, digest in sorted(self._expected.items())
        ]
        if self.verification_failure == "inventory" and self._created_status == "FINALIZED":
            values = values[:-1]
        return tuple(values)

    def version(self, version: str) -> Mapping[str, Any]:
        config = dict(self.config)
        if self.verification_failure == "configuration":
            config["cleanUrls"] = True
        return {
            "name": version,
            "status": self._created_status,
            "config": config,
            "fileCount": str(len(self._expected)),
        }

    def finalize_version(self, version: str) -> Mapping[str, Any]:
        def apply() -> None:
            self._created_status = "FINALIZED"
        self._write("finalize", apply=apply)
        return {
            "name": version, "status": "FINALIZED", "config": dict(self.config),
            "fileCount": str(len(self._expected)), "versionBytes": "1",
        }

    def release_version(
        self, target: ProviderTarget, version: str
    ) -> Mapping[str, Any]:
        release = (
            f"sites/{self.target.site}/channels/live/releases/"
            f"published-release-{self._deployment_number}"
        )
        identity = ProviderIdentity(self.target, release, version)
        def apply() -> None:
            self.live = identity
            self._released_files = {
                path[1:]: gzip.decompress(self.uploaded_payloads[digest])
                for path, digest in self._expected.items()
                if digest in self.uploaded_payloads
            }
        self._write("release", apply=apply)
        return {
            "name": release, "type": "DEPLOY", "releaseTime": "2026-09-14T00:00:00Z",
            "version": {"name": version, "status": "FINALIZED"},
        }

    def public_resource(self, target: ProviderTarget, path: str) -> PublicResource:
        if path in {"/__/firebase/init.js", "/__/firebase/init.json"}:
            if self.verification_failure == "managed-missing":
                raise FirebasePublicationError("fake managed resource is missing")
            config = {
                "projectId": self.managed_identity["project_id"],
                "messagingSenderId": self.managed_identity["messaging_sender_id"],
                "authDomain": self.managed_identity["auth_domain"],
                "storageBucket": self.managed_identity["storage_bucket"],
            }
            if self.verification_failure == "managed":
                config["projectId"] = "other-project"
            text = json.dumps(config, sort_keys=True)
            body = text.encode() if path.endswith(".json") else f"firebase.initializeApp({text});".encode()
            if self.verification_failure == "managed-generated" and path.endswith(".js"):
                body = b"not firebase configuration"
        else:
            relative = "index.html" if path == "/" else path[1:]
            source = self._released_files if self._released_files is not None else self.files
            if relative not in source:
                raise FirebasePublicationError("fake public path is missing")
            body = source[relative]
            if self.verification_failure == "public" and relative == "index.html":
                body += b"tampered"
        return PublicResource(
            path, body,
            f"https://{self.target.site}.web.app{path}", 200,
        )
