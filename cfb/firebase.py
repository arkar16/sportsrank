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
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    from .baseline import BaselineValidationError, VerifiedBaseline, import_baseline
    from .publication_records import ProviderIdentity, ProviderTarget
except ImportError:  # Direct execution from the cfb directory.
    from baseline import BaselineValidationError, VerifiedBaseline, import_baseline
    from publication_records import ProviderIdentity, ProviderTarget


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
