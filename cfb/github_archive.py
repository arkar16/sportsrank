"""Immutable GitHub Release asset storage behind one exact-byte adapter seam."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from types import MappingProxyType
from typing import Mapping, Protocol

from .publication_records import ArchiveReference


class ArchiveError(RuntimeError):
    """An archive could not prove permanent exact-byte retrieval."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
    def retrieve_and_verify(self, reference: ArchiveReference, destination: str | Path) -> Path: ...


class GitHubReleaseArchive:
    """Concrete `gh` adapter; release notes and labels carry no authority."""

    def _run(self, arguments: list[str], *, binary: bool = False) -> bytes:
        try:
            return subprocess.run(
                ["gh", *arguments], check=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.decode("utf-8", errors="replace").strip()
            raise ArchiveError(f"GitHub archive operation failed: {message}") from exc

    def _release(self, repository: str, tag: str) -> Mapping[str, object] | None:
        result = subprocess.run(
            ["gh", "api", f"repos/{repository}/releases/tags/{tag}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            if b"404" in result.stderr or b"Not Found" in result.stderr:
                return None
            raise ArchiveError("could not inspect GitHub archive release")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ArchiveError("GitHub archive response is invalid") from exc
        if not isinstance(value, Mapping):
            raise ArchiveError("GitHub archive response is invalid")
        return value

    @staticmethod
    def _references(spec: ArchiveSpec, release: Mapping[str, object]) -> dict[str, ArchiveReference]:
        if release.get("tag_name") != spec.tag or release.get("target_commitish") != spec.target_commit:
            raise ArchiveError("immutable release target or tag differs from the archive specification")
        if release.get("draft") is not False or release.get("immutable") is not True:
            raise ArchiveError("GitHub release is not published and immutable")
        raw_assets = release.get("assets")
        if not isinstance(raw_assets, list):
            raise ArchiveError("GitHub release has no asset inventory")
        by_name = {asset.get("name"): asset for asset in raw_assets if isinstance(asset, Mapping)}
        if set(by_name) != set(spec.assets):
            raise ArchiveError("immutable release assets differ from the archive specification")
        references: dict[str, ArchiveReference] = {}
        for name, path in spec.assets.items():
            expected_bytes = Path(path).read_bytes()
            asset = by_name[name]
            digest = asset.get("digest")
            expected_sha = _sha256(expected_bytes)
            if digest != f"sha256:{expected_sha}" or asset.get("size") != len(expected_bytes) or asset.get("state") != "uploaded":
                raise ArchiveError(f"GitHub asset identity mismatch: {name}")
            references[name] = ArchiveReference(
                spec.repository, str(release.get("id")), spec.tag, spec.target_commit,
                str(asset.get("id")),
                name, expected_sha, len(expected_bytes), True,
            )
        return references

    def seal_or_reconcile(self, spec: ArchiveSpec) -> Mapping[str, ArchiveReference]:
        release = self._release(spec.repository, spec.tag)
        if release is None:
            self._run(["release", "create", spec.tag, "--repo", spec.repository, "--target", spec.target_commit, "--title", spec.title, "--notes", "Immutable SportsRank publication evidence", "--draft"])
            for name, path in spec.assets.items():
                self._run(["release", "upload", spec.tag, str(path), "--repo", spec.repository])
            self._run(["release", "edit", spec.tag, "--repo", spec.repository, "--draft=false"])
            release = self._release(spec.repository, spec.tag)
            if release is None:
                raise ArchiveError("published GitHub release cannot be retrieved")
        elif release.get("draft") is True and release.get("immutable") is not True:
            if release.get("tag_name") != spec.tag or release.get("target_commitish") != spec.target_commit:
                raise ArchiveError("draft archive target or tag differs from the specification")
            raw_assets = release.get("assets")
            if not isinstance(raw_assets, list):
                raise ArchiveError("draft archive asset inventory is invalid")
            existing = {value.get("name"): value for value in raw_assets if isinstance(value, Mapping)}
            if not set(existing).issubset(spec.assets):
                raise ArchiveError("draft archive contains an unexpected asset")
            for name, asset in existing.items():
                value = Path(spec.assets[name]).read_bytes()
                if asset.get("digest") != f"sha256:{_sha256(value)}" or asset.get("size") != len(value):
                    raise ArchiveError(f"draft archive asset differs from requested bytes: {name}")
            for name, path in spec.assets.items():
                if name not in existing:
                    self._run(["release", "upload", spec.tag, str(path), "--repo", spec.repository])
            self._run(["release", "edit", spec.tag, "--repo", spec.repository, "--draft=false"])
            release = self._release(spec.repository, spec.tag)
            if release is None:
                raise ArchiveError("published GitHub release cannot be retrieved")
        return self._references(spec, release)

    def retrieve_and_verify(self, reference: ArchiveReference, destination: str | Path) -> Path:
        reference = ArchiveReference.from_value(reference)
        release = self._release(reference.repository, reference.tag)
        if (
            release is None
            or release.get("immutable") is not True
            or str(release.get("id")) != reference.release_id
            or release.get("target_commitish") != reference.target_commit
        ):
            raise ArchiveError("archive release identity or immutability is unavailable")
        assets = release.get("assets")
        match = next((v for v in assets if isinstance(v, Mapping) and str(v.get("id")) == reference.asset_id), None) if isinstance(assets, list) else None
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

    def seal_or_reconcile(self, spec: ArchiveSpec) -> Mapping[str, ArchiveReference]:
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
