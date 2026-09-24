"""Current-tree-only Git evidence for durable publication recovery.

The transport contains the candidate commit object and exactly the tree/blob
objects reachable from its root tree.  Parent commit objects are deliberately
absent: publication needs an authenticated candidate tree, not repository
history.
"""

from __future__ import annotations

import gzip
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zlib

from .publication_records import canonical_json
from .public_safety import assert_public_bytes


class CandidateTreeError(ValueError):
    """The current-tree evidence is malformed, unsafe, or inconsistent."""


_SHA = re.compile(r"[0-9a-f]{40}\Z")
def _run(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments], input=input_bytes,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CandidateTreeError("cannot read the candidate Git tree") from exc


def _object_id(kind: str, value: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(value)}\0".encode("ascii") + value).hexdigest()


def _read_objects(
    repository: Path, object_ids: set[str] | list[str] | tuple[str, ...]
) -> dict[str, tuple[str, bytes]]:
    requested = sorted(set(object_ids))
    if not requested or any(not _SHA.fullmatch(oid) for oid in requested):
        raise CandidateTreeError("candidate Git object request is invalid")
    stream = _run(
        repository, "cat-file", "--batch",
        input_bytes=("\n".join(requested) + "\n").encode("ascii"),
    )
    offset = 0
    objects: dict[str, tuple[str, bytes]] = {}
    for expected_oid in requested:
        newline = stream.find(b"\n", offset)
        if newline < 0:
            raise CandidateTreeError("candidate Git object batch is truncated")
        try:
            oid, kind, size_text = stream[offset:newline].decode("ascii").split(" ")
            size = int(size_text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise CandidateTreeError("candidate Git object header is malformed") from exc
        if (
            oid != expected_oid
            or kind not in {"commit", "tree", "blob"}
            or size < 0
        ):
            raise CandidateTreeError("candidate Git object header is invalid")
        body_start = newline + 1
        body_end = body_start + size
        if body_end >= len(stream) or stream[body_end:body_end + 1] != b"\n":
            raise CandidateTreeError("candidate Git object batch is truncated")
        body = stream[body_start:body_end]
        if _object_id(kind, body) != oid:
            raise CandidateTreeError("candidate Git object failed identity verification")
        objects[oid] = (kind, body)
        offset = body_end + 1
    if offset != len(stream):
        raise CandidateTreeError("candidate Git object batch has unexpected output")
    return objects


def _tree_objects(repository: Path, tree: str) -> tuple[set[str], list[dict[str, str]]]:
    objects = {tree}
    files: list[dict[str, str]] = []
    raw = _run(repository, "ls-tree", "-r", "-t", "-z", tree)
    paths: set[str] = set()
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            meta, path_bytes = entry.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split(" ")
            path = path_bytes.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise CandidateTreeError("candidate tree entry is malformed") from exc
        if (
            not path
            or path in paths
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or not _SHA.fullmatch(oid)
        ):
            raise CandidateTreeError("candidate tree path is unsafe")
        paths.add(path)
        objects.add(oid)
        if kind == "tree":
            if mode != "040000":
                raise CandidateTreeError("candidate directory mode is invalid")
        elif kind == "blob":
            if mode not in {"100644", "100755"}:
                raise CandidateTreeError(
                    "candidate tree contains a symlink or unsupported file mode"
                )
            files.append({"path": path, "mode": mode, "object": oid})
        else:
            raise CandidateTreeError("candidate tree contains a non-file entry")
    return objects, sorted(files, key=lambda item: item["path"])


def _assert_public_safe(
    files: list[dict[str, str]], objects: dict[str, tuple[str, bytes]]
) -> None:
    for item in files:
        path = item["path"]
        kind, value = objects[item["object"]]
        if kind != "blob":
            raise CandidateTreeError("candidate file object is not a blob")
        try:
            assert_public_bytes(path, value)
        except ValueError as exc:
            raise CandidateTreeError(
                f"candidate tree contains unsafe public bytes: {path}: {exc}"
            ) from exc


def create_candidate_tree_archive(
    repository: str | Path, candidate_commit: str, output: str | Path
) -> str:
    """Create deterministic evidence without traversing the commit's parents."""

    repo = Path(repository).resolve()
    if not _SHA.fullmatch(candidate_commit):
        raise CandidateTreeError("candidate commit must be an exact SHA")
    kind, commit_bytes = _read_objects(repo, {candidate_commit})[candidate_commit]
    if kind != "commit":
        raise CandidateTreeError("candidate identity is not a commit")
    tree_line = next(
        (line for line in commit_bytes.splitlines() if line.startswith(b"tree ")), None
    )
    if tree_line is None:
        raise CandidateTreeError("candidate commit has no root tree")
    tree = tree_line[5:].decode("ascii")
    if not _SHA.fullmatch(tree):
        raise CandidateTreeError("candidate root tree identity is invalid")
    object_ids, files = _tree_objects(repo, tree)
    object_ids.add(candidate_commit)
    object_values = _read_objects(repo, object_ids)
    _assert_public_safe(files, object_values)
    objects: list[dict[str, object]] = []
    encoded: dict[str, bytes] = {}
    for oid in sorted(object_ids):
        object_kind, value = object_values[oid]
        objects.append({"object": oid, "type": object_kind, "size": len(value)})
        encoded[oid] = zlib.compress(
            f"{object_kind} {len(value)}\0".encode("ascii") + value
        )
    manifest = canonical_json({
        "schema_version": 1,
        "record_type": "candidate_current_tree",
        "candidate_commit": candidate_commit,
        "root_tree": tree,
        "objects": objects,
        "files": files,
    })
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                entries = [("candidate-tree.json", manifest)] + [
                    (f"objects/{oid[:2]}/{oid[2:]}", encoded[oid]) for oid in sorted(encoded)
                ]
                for name, value in entries:
                    info = tarfile.TarInfo(name)
                    info.size = len(value)
                    info.mode = 0o644
                    info.uid = info.gid = info.mtime = 0
                    archive.addfile(info, io.BytesIO(value))
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def materialize_candidate_tree_archive(
    archive_path: str | Path, *, candidate_commit: str, destination: str | Path
) -> Path:
    """Verify and materialize a bare object store usable by GitCommitTreeReader."""

    archive_file = Path(archive_path).resolve()
    target = Path(destination).resolve()
    if target.exists():
        raise CandidateTreeError("candidate tree destination must not exist")
    try:
        with tarfile.open(archive_file, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)) or "candidate-tree.json" not in names:
                raise CandidateTreeError("candidate tree archive members are invalid")
            if any(
                not member.isfile()
                or (member.name != "candidate-tree.json" and not re.fullmatch(r"objects/[0-9a-f]{2}/[0-9a-f]{38}", member.name))
                for member in members
            ):
                raise CandidateTreeError("candidate tree archive contains an unexpected member")
            manifest_member = archive.getmember("candidate-tree.json")
            stream = archive.extractfile(manifest_member)
            if stream is None:
                raise CandidateTreeError("candidate tree manifest is unavailable")
            manifest_bytes = stream.read()
            raw = json.loads(manifest_bytes)
            if canonical_json(raw) != manifest_bytes:
                raise CandidateTreeError("candidate tree manifest is not canonical")
            if (
                not isinstance(raw, dict)
                or set(raw) != {"schema_version", "record_type", "candidate_commit", "root_tree", "objects", "files"}
                or raw["schema_version"] != 1
                or raw["record_type"] != "candidate_current_tree"
                or raw["candidate_commit"] != candidate_commit
            ):
                raise CandidateTreeError("candidate tree manifest identity is invalid")
            if not isinstance(raw["objects"], list) or not isinstance(raw["files"], list):
                raise CandidateTreeError("candidate tree manifest collections are invalid")
            for item in raw["objects"]:
                if (
                    not isinstance(item, dict)
                    or set(item) != {"object", "type", "size"}
                    or not isinstance(item["object"], str)
                    or not _SHA.fullmatch(item["object"])
                    or item["type"] not in {"commit", "tree", "blob"}
                    or isinstance(item["size"], bool)
                    or not isinstance(item["size"], int)
                    or item["size"] < 0
                ):
                    raise CandidateTreeError("candidate tree object manifest is invalid")
            if len({item["object"] for item in raw["objects"]}) != len(raw["objects"]):
                raise CandidateTreeError("candidate tree object manifest has duplicates")
            expected = {f"objects/{item['object'][:2]}/{item['object'][2:]}" for item in raw["objects"]}
            if set(names) != expected | {"candidate-tree.json"}:
                raise CandidateTreeError("candidate tree object set differs from its manifest")
            subprocess.run(["git", "init", "--bare", "--quiet", str(target)], check=True)
            for item in raw["objects"]:
                oid = item["object"]
                member = archive.getmember(f"objects/{oid[:2]}/{oid[2:]}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise CandidateTreeError("candidate Git object is unavailable")
                compressed = stream.read()
                value = zlib.decompress(compressed)
                header, body = value.split(b"\0", 1)
                kind, size = header.decode("ascii").split(" ")
                if (
                    kind != item["type"] or int(size) != len(body)
                    or len(body) != item["size"] or _object_id(kind, body) != oid
                ):
                    raise CandidateTreeError("candidate Git object is corrupt")
                output = target / "objects" / oid[:2] / oid[2:]
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(compressed)
    except CandidateTreeError:
        raise
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, tarfile.TarError, subprocess.CalledProcessError, zlib.error) as exc:
        raise CandidateTreeError("candidate tree archive is invalid") from exc
    # Git independently authenticates the commit and exact recursive path/mode/object map.
    declared = {item["object"] for item in raw["objects"]}
    materialized_objects = _read_objects(target, declared)
    commit_kind, materialized_commit = materialized_objects[candidate_commit]
    if commit_kind != "commit":
        raise CandidateTreeError("candidate commit object is unavailable")
    tree_line = next(
        (line for line in materialized_commit.splitlines() if line.startswith(b"tree ")),
        None,
    )
    if tree_line is None:
        raise CandidateTreeError("candidate commit has no root tree")
    actual_tree = tree_line[5:].decode("ascii")
    if actual_tree != raw["root_tree"]:
        raise CandidateTreeError("candidate root tree differs from the commit")
    reachable, actual_files = _tree_objects(target, actual_tree)
    reachable.add(candidate_commit)
    if reachable != declared:
        raise CandidateTreeError(
            "candidate archive contains missing or unreachable Git objects"
        )
    if actual_files != raw["files"]:
        raise CandidateTreeError("candidate file tree differs from its manifest")
    _assert_public_safe(actual_files, materialized_objects)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("candidate_commit")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    digest = create_candidate_tree_archive(
        args.repository, args.candidate_commit, args.output
    )
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
