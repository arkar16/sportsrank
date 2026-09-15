"""Immutable GitHub Release asset storage behind one exact-byte adapter seam."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
from types import MappingProxyType
from typing import Mapping, Protocol
from urllib.parse import quote

from .publication_records import ArchiveReference


class ArchiveError(RuntimeError):
    """An archive could not prove permanent exact-byte retrieval."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _api_id(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArchiveError(f"GitHub archive {name} is not a positive numeric ID")
    return value


def _requested_id(value: object, name: str) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return value
    raise ArchiveError(f"GitHub archive {name} is not a positive numeric ID")


@dataclass(frozen=True)
class ArchiveSpec:
    repository: str
    tag: str
    target_commit: str
    assets: Mapping[str, Path]
    title: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "assets", MappingProxyType(dict(self.assets)))
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise ArchiveError("archive repository must be OWNER/REPOSITORY")
        if not self.tag or self.tag.startswith("-") or any(value.isspace() for value in self.tag):
            raise ArchiveError("archive tag is invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", self.target_commit):
            raise ArchiveError("archive target must be an immutable commit SHA")
        if not self.assets or len(self.assets) != len(set(self.assets)):
            raise ArchiveError("archive assets must be a non-empty unique mapping")
        for name, path in self.assets.items():
            if not name or "/" in name or name != Path(path).name or not Path(path).is_file():
                raise ArchiveError("archive asset names and paths must identify files")


class ImmutableArchive(Protocol):
    def seal_or_reconcile(self, spec: ArchiveSpec) -> Mapping[str, ArchiveReference]: ...
    def seal_exclusive(self, spec: ArchiveSpec) -> Mapping[str, ArchiveReference]: ...
    def retrieve_and_verify(self, reference: ArchiveReference, destination: str | Path) -> Path: ...


class GitHubReleaseArchive:
    """Concrete `gh` adapter; release notes and labels carry no authority."""

    def _run(self, arguments: list[str], *, binary: bool = False) -> bytes:
        result = subprocess.run(
            ["gh", *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise ArchiveError("GitHub archive command failed")
        return result.stdout

    def _api_json(self, endpoint: str, *, not_found: bool = False) -> object | None:
        result = subprocess.run(
            ["gh", "api", endpoint], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            if not_found and (b"404" in result.stderr or b"Not Found" in result.stderr):
                return None
            raise ArchiveError("GitHub archive API request failed")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ArchiveError("GitHub archive response is invalid") from exc

    def _release(self, repository: str, tag: str) -> Mapping[str, object] | None:
        value = self._api_json(
            f"repos/{repository}/releases/tags/{quote(tag, safe='')}",
            not_found=True,
        )
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ArchiveError("GitHub archive response is invalid")
        return value

    def _release_by_id(self, repository: str, release_id: object) -> Mapping[str, object]:
        requested = _requested_id(release_id, "release ID")
        value = self._api_json(f"repos/{repository}/releases/{requested}")
        if not isinstance(value, Mapping):
            raise ArchiveError("GitHub archive release response is invalid")
        if str(_api_id(value.get("id"), "release ID")) != requested:
            raise ArchiveError("GitHub archive release ID changed during reconciliation")
        return value

    def _draft(self, repository: str, tag: str) -> Mapping[str, object] | None:
        matches: list[Mapping[str, object]] = []
        for page in range(1, 1001):
            value = self._api_json(
                f"repos/{repository}/releases?per_page=100&page={page}"
            )
            if not isinstance(value, list):
                raise ArchiveError("GitHub release listing response is invalid")
            matches.extend(
                item for item in value
                if isinstance(item, Mapping)
                and item.get("draft") is True
                and item.get("tag_name") == tag
            )
            if len(value) < 100:
                break
        else:
            raise ArchiveError("GitHub release listing exceeded the safety limit")
        if len(matches) > 1:
            raise ArchiveError("multiple draft releases claim the archive tag")
        if not matches:
            return None
        release_id = _api_id(matches[0].get("id"), "release ID")
        exact = self._release_by_id(repository, release_id)
        if exact.get("id") != release_id or exact.get("draft") is not True or exact.get("tag_name") != tag:
            raise ArchiveError("draft archive identity changed during discovery")
        return exact

    def _tag_commit(self, repository: str, tag: str) -> str | None:
        value = self._api_json(
            f"repos/{repository}/git/ref/tags/{quote(tag, safe='')}",
            not_found=True,
        )
        if value is None:
            return None
        if not isinstance(value, Mapping) or not isinstance(value.get("object"), Mapping):
            raise ArchiveError("archive tag reference is invalid")
        target = value["object"]
        seen: set[str] = set()
        for _ in range(16):
            kind = target.get("type")
            sha = target.get("sha")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ArchiveError("archive tag object has an invalid identity")
            if kind == "commit":
                return sha
            if kind != "tag" or sha in seen:
                raise ArchiveError("archive tag does not resolve to one commit")
            seen.add(sha)
            annotated = self._api_json(f"repos/{repository}/git/tags/{sha}")
            if not isinstance(annotated, Mapping) or not isinstance(annotated.get("object"), Mapping):
                raise ArchiveError("annotated archive tag object is invalid")
            target = annotated["object"]
        raise ArchiveError("archive tag indirection exceeds the safety limit")

    def _require_tag_commit(self, repository: str, tag: str, expected: str) -> None:
        if self._tag_commit(repository, tag) != expected:
            raise ArchiveError("archive tag does not resolve to the expected commit")

    def _ensure_tag_commit(self, repository: str, tag: str, expected: str) -> None:
        actual = self._tag_commit(repository, tag)
        if actual is None:
            self._run([
                "api", "--method", "POST", f"repos/{repository}/git/refs",
                "-f", f"ref=refs/tags/{tag}", "-f", f"sha={expected}",
            ])
        self._require_tag_commit(repository, tag, expected)

    @staticmethod
    def _draft_assets(
        spec: ArchiveSpec,
        release: Mapping[str, object],
        *,
        allow_missing: bool,
    ) -> dict[str, Mapping[str, object]]:
        if release.get("draft") is not True or release.get("tag_name") != spec.tag:
            raise ArchiveError("draft archive identity changed during reconciliation")
        raw_assets = release.get("assets")
        if not isinstance(raw_assets, list):
            raise ArchiveError("draft archive asset inventory is invalid")
        existing = {
            value.get("name"): value for value in raw_assets
            if isinstance(value, Mapping) and isinstance(value.get("name"), str)
        }
        if len(existing) != len(raw_assets) or not set(existing).issubset(spec.assets):
            raise ArchiveError("draft archive contains an unexpected or duplicate asset")
        if not allow_missing and set(existing) != set(spec.assets):
            raise ArchiveError("draft archive is missing a required asset")
        for name, asset in existing.items():
            value = Path(spec.assets[name]).read_bytes()
            if (
                asset.get("digest") != f"sha256:{_sha256(value)}"
                or asset.get("size") != len(value)
                or asset.get("state") != "uploaded"
            ):
                raise ArchiveError(f"draft archive asset differs from requested bytes: {name}")
        return existing

    def _upload_to_draft(
        self, spec: ArchiveSpec, release_id: object, name: str, path: Path
    ) -> None:
        self._run([
            "api", "--hostname", "uploads.github.com", "--method", "POST",
            f"repos/{spec.repository}/releases/{release_id}/assets?name={quote(name, safe='')}",
            "-H", "Content-Type: application/octet-stream", "--input", str(path),
        ])

    def _publish_draft(self, repository: str, release_id: object) -> None:
        self._run([
            "api", "--method", "PATCH", f"repos/{repository}/releases/{release_id}",
            "-F", "draft=false",
        ])

    def _references(self, spec: ArchiveSpec, release: Mapping[str, object]) -> dict[str, ArchiveReference]:
        if release.get("tag_name") != spec.tag:
            raise ArchiveError("immutable release tag differs from the archive specification")
        if release.get("draft") is not False or release.get("immutable") is not True:
            raise ArchiveError("GitHub release is not published and immutable")
        self._require_tag_commit(spec.repository, spec.tag, spec.target_commit)
        raw_assets = release.get("assets")
        if not isinstance(raw_assets, list):
            raise ArchiveError("GitHub release has no asset inventory")
        by_name = {asset.get("name"): asset for asset in raw_assets if isinstance(asset, Mapping)}
        if len(by_name) != len(raw_assets) or set(by_name) != set(spec.assets):
            raise ArchiveError("immutable release assets differ from the archive specification")
        release_id = _api_id(release.get("id"), "release ID")
        references: dict[str, ArchiveReference] = {}
        for name, path in spec.assets.items():
            expected_bytes = Path(path).read_bytes()
            asset = by_name[name]
            digest = asset.get("digest")
            expected_sha = _sha256(expected_bytes)
            if digest != f"sha256:{expected_sha}" or asset.get("size") != len(expected_bytes) or asset.get("state") != "uploaded":
                raise ArchiveError(f"GitHub asset identity mismatch: {name}")
            references[name] = ArchiveReference(
                spec.repository, str(release_id), spec.tag, spec.target_commit,
                str(_api_id(asset.get("id"), "asset ID")),
                name, expected_sha, len(expected_bytes), True,
            )
        return references

    def seal_or_reconcile(self, spec: ArchiveSpec) -> Mapping[str, ArchiveReference]:
        release = self._release(spec.repository, spec.tag)
        if release is None:
            release = self._draft(spec.repository, spec.tag)
        if release is None:
            self._ensure_tag_commit(spec.repository, spec.tag, spec.target_commit)
            self._run(["release", "create", spec.tag, "--repo", spec.repository, "--target", spec.target_commit, "--title", spec.title, "--notes", "Immutable SportsRank publication evidence", "--draft"])
            release = self._draft(spec.repository, spec.tag)
            if release is None:
                raise ArchiveError("created draft archive cannot be discovered by stable ID")
        if release.get("draft") is True and release.get("immutable") is not True:
            if release.get("tag_name") != spec.tag:
                raise ArchiveError("draft archive tag differs from the specification")
            release_id = _api_id(release.get("id"), "release ID")
            self._require_tag_commit(spec.repository, spec.tag, spec.target_commit)
            existing = self._draft_assets(spec, release, allow_missing=True)
            for name, path in spec.assets.items():
                if name not in existing:
                    self._upload_to_draft(spec, release_id, name, Path(path))
            release = self._release_by_id(spec.repository, release_id)
            self._draft_assets(spec, release, allow_missing=False)
            self._require_tag_commit(spec.repository, spec.tag, spec.target_commit)
            self._publish_draft(spec.repository, release_id)
            release = self._release_by_id(spec.repository, release_id)
        return self._references(spec, release)

    def seal_exclusive(self, spec: ArchiveSpec) -> Mapping[str, ArchiveReference]:
        """Create one immutable claim; any pre-existing claim is a hard failure."""

        if self._release(spec.repository, spec.tag) is not None:
            raise ArchiveError("exclusive archive claim already exists")
        if self._draft(spec.repository, spec.tag) is not None:
            raise ArchiveError("exclusive archive claim already exists")
        self._ensure_tag_commit(spec.repository, spec.tag, spec.target_commit)
        self._run([
            "release", "create", spec.tag, "--repo", spec.repository,
            "--target", spec.target_commit, "--title", spec.title,
            "--notes", "Immutable SportsRank publication evidence", "--draft",
        ])
        release = self._draft(spec.repository, spec.tag)
        if release is None:
            raise ArchiveError("exclusive archive claim creation is uncertain")
        release_id = _api_id(release.get("id"), "release ID")
        self._require_tag_commit(spec.repository, spec.tag, spec.target_commit)
        if self._draft_assets(spec, release, allow_missing=True):
            raise ArchiveError("exclusive archive claim was not empty")
        for name, path in spec.assets.items():
            self._upload_to_draft(spec, release_id, name, Path(path))
        release = self._release_by_id(spec.repository, release_id)
        self._draft_assets(spec, release, allow_missing=False)
        self._require_tag_commit(spec.repository, spec.tag, spec.target_commit)
        self._publish_draft(spec.repository, release_id)
        return self._references(
            spec, self._release_by_id(spec.repository, release_id)
        )

    def retrieve_and_verify(self, reference: ArchiveReference, destination: str | Path) -> Path:
        reference = ArchiveReference.from_value(reference)
        release = self._release_by_id(reference.repository, reference.release_id)
        if (
            release.get("immutable") is not True
            or str(release.get("id")) != reference.release_id
            or release.get("tag_name") != reference.tag
        ):
            raise ArchiveError("archive release identity or immutability is unavailable")
        self._require_tag_commit(
            reference.repository, reference.tag, reference.target_commit
        )
        assets = release.get("assets")
        match = None
        if isinstance(assets, list):
            for value in assets:
                if not isinstance(value, Mapping):
                    raise ArchiveError("archive asset inventory is invalid")
                if str(_api_id(value.get("id"), "asset ID")) == reference.asset_id:
                    match = value
        if match is None or match.get("name") != reference.asset_name or match.get("digest") != f"sha256:{reference.sha256}" or match.get("size") != reference.size:
            raise ArchiveError("archive asset metadata no longer matches its reference")
        value = self._run(["api", f"repos/{reference.repository}/releases/assets/{reference.asset_id}", "-H", "Accept: application/octet-stream"], binary=True)
        if len(value) != reference.size or _sha256(value) != reference.sha256:
            raise ArchiveError("retrieved archive bytes failed digest verification")
        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(value)
        return output


class FakeImmutableArchive:
    """Offline fake with the same seal-once and exact-retrieval behavior."""

    def __init__(self, *, immutability_available: bool = True) -> None:
        self.immutability_available = immutability_available
        self._releases: dict[tuple[str, str], tuple[str, str, dict[str, bytes], dict[str, ArchiveReference]]] = {}
        self._lock = threading.Lock()

    def seal_or_reconcile(self, spec: ArchiveSpec) -> Mapping[str, ArchiveReference]:
        with self._lock:
            return self._seal_or_reconcile(spec)

    def _seal_or_reconcile(self, spec: ArchiveSpec) -> Mapping[str, ArchiveReference]:
        if not self.immutability_available:
            raise ArchiveError("archive immutability is unavailable")
        key = (spec.repository, spec.tag)
        assets = {name: Path(path).read_bytes() for name, path in spec.assets.items()}
        existing = self._releases.get(key)
        if existing is not None:
            target, _release_id, prior, references = existing
            if target != spec.target_commit or prior != assets:
                raise ArchiveError("sealed archive cannot be changed or appended")
            return dict(references)
        release_id = str(len(self._releases) + 1)
        references = {
            name: ArchiveReference(
                spec.repository, release_id, spec.tag, spec.target_commit,
                str(index), name, _sha256(value), len(value), True,
            )
            for index, (name, value) in enumerate(sorted(assets.items()), 1)
        }
        self._releases[key] = (spec.target_commit, release_id, assets, references)
        return dict(references)

    def seal_exclusive(self, spec: ArchiveSpec) -> Mapping[str, ArchiveReference]:
        with self._lock:
            key = (spec.repository, spec.tag)
            if key in self._releases:
                raise ArchiveError("exclusive archive claim already exists")
            return self._seal_or_reconcile(spec)

    def retrieve_and_verify(self, reference: ArchiveReference, destination: str | Path) -> Path:
        reference = ArchiveReference.from_value(reference)
        release = self._releases.get((reference.repository, reference.tag))
        if (
            not self.immutability_available
            or release is None
            or release[0] != reference.target_commit
            or release[1] != reference.release_id
        ):
            raise ArchiveError("archive identity or immutability is unavailable")
        expected_reference = release[3].get(reference.asset_name)
        value = release[2].get(reference.asset_name)
        if (
            expected_reference != reference
            or value is None
            or _sha256(value) != reference.sha256
            or len(value) != reference.size
        ):
            raise ArchiveError("retrieved archive bytes failed digest verification")
        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(value)
        return output

    def corrupt(self, reference: ArchiveReference, value: bytes) -> None:
        release = self._releases[(reference.repository, reference.tag)]
        release[2][reference.asset_name] = value
