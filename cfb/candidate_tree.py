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


def _object_bytes(repository: Path, oid: str) -> tuple[str, bytes]:
    kind = _run(repository, "cat-file", "-t", oid).decode("ascii").strip()
    if kind not in {"commit", "tree", "blob"}:
        raise CandidateTreeError("candidate tree contains an unsupported Git object")
    return kind, _run(repository, "cat-file", kind, oid)


def _object_id(kind: str, value: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(value)}\0".encode("ascii") + value).hexdigest()


def _tree_objects(repository: Path, tree: str) -> tuple[set[str], list[dict[str, str]]]:
    objects = {tree}
    files: list[dict[str, str]] = []

    def visit(tree_oid: str, prefix: str) -> None:
        raw = _run(repository, "ls-tree", "-z", tree_oid)
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            try:
                meta, name_bytes = entry.split(b"\t", 1)
                mode, kind, oid = meta.decode("ascii").split(" ")
                name = name_bytes.decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise CandidateTreeError("candidate tree entry is malformed") from exc
            path = f"{prefix}{name}"
            if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
                raise CandidateTreeError("candidate tree path is unsafe")
            objects.add(oid)
            if kind == "tree":
                if mode != "040000":
                    raise CandidateTreeError("candidate directory mode is invalid")
                visit(oid, path + "/")
            elif kind == "blob":
                if mode not in {"100644", "100755"}:
                    raise CandidateTreeError(
                        "candidate tree contains a symlink or unsupported file mode"
                    )
                files.append({"path": path, "mode": mode, "object": oid})
            else:
                raise CandidateTreeError("candidate tree contains a non-file entry")

    visit(tree, "")
    return objects, sorted(files, key=lambda item: item["path"])


def _assert_public_safe(repository: Path, files: list[dict[str, str]]) -> None:
    for item in files:
        path = item["path"]
        value = _run(repository, "cat-file", "blob", item["object"])
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
    kind, commit_bytes = _object_bytes(repo, candidate_commit)
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
    _assert_public_safe(repo, files)
    object_ids.add(candidate_commit)
    objects: list[dict[str, object]] = []
    encoded: dict[str, bytes] = {}
    for oid in sorted(object_ids):
        object_kind, value = _object_bytes(repo, oid)
        if _object_id(object_kind, value) != oid:
            raise CandidateTreeError("candidate Git object failed identity verification")
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
    if _run(target, "cat-file", "-t", candidate_commit).strip() != b"commit":
        raise CandidateTreeError("candidate commit object is unavailable")
    _commit_kind, materialized_commit = _object_bytes(target, candidate_commit)
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
    declared = {item["object"] for item in raw["objects"]}
    if reachable != declared:
        raise CandidateTreeError(
            "candidate archive contains missing or unreachable Git objects"
        )
    if actual_files != raw["files"]:
        raise CandidateTreeError("candidate file tree differs from its manifest")
    _assert_public_safe(target, actual_files)
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
