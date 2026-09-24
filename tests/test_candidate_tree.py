import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from cfb.candidate_tree import (
    CandidateTreeError,
    create_candidate_tree_archive,
    materialize_candidate_tree_archive,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout.strip()


class CandidateCurrentTreeTests(unittest.TestCase):
    def _repository(self, root: Path) -> tuple[Path, str, str]:
        repo = root / "repo"
        repo.mkdir()
        _git(repo, "init", "--quiet")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        (repo / "website").mkdir()
        (repo / "website/index.html").write_text("old", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "--quiet", "-m", "parent")
        parent = _git(repo, "rev-parse", "HEAD")
        (repo / "website/index.html").write_text("current", encoding="utf-8")
        _git(repo, "commit", "--quiet", "-am", "current")
        return repo, parent, _git(repo, "rev-parse", "HEAD")

    def test_round_trip_has_current_commit_tree_and_no_parent_object(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, parent, commit = self._repository(root)
            archive = root / "candidate-tree.tar.gz"
            create_candidate_tree_archive(repo, commit, archive)
            materialized = materialize_candidate_tree_archive(
                archive, candidate_commit=commit, destination=root / "bare.git"
            )
            self.assertEqual(_git(materialized, "show", f"{commit}:website/index.html"), "current")
            missing = subprocess.run(
                ["git", "-C", str(materialized), "cat-file", "-e", parent],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertNotEqual(missing.returncode, 0)

    def test_extra_unreachable_object_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _parent, commit = self._repository(root)
            archive = root / "candidate-tree.tar.gz"
            create_candidate_tree_archive(repo, commit, archive)
            unpacked = root / "unpacked"
            unpacked.mkdir()
            with tarfile.open(archive, "r:gz") as source:
                source.extractall(unpacked, filter="data")
            manifest_path = unpacked / "candidate-tree.json"
            manifest = json.loads(manifest_path.read_bytes())
            extra = next(item for item in manifest["objects"] if item["type"] == "blob")
            duplicate = dict(extra)
            duplicate["object"] = "0" * 40
            manifest["objects"].append(duplicate)
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            extra_path = unpacked / "objects" / "00" / ("0" * 38)
            extra_path.parent.mkdir(parents=True, exist_ok=True)
            extra_path.write_bytes(b"invalid")
            tampered = root / "tampered.tar.gz"
            with tarfile.open(tampered, "w:gz") as output:
                for path in sorted(unpacked.rglob("*")):
                    if path.is_file():
                        output.add(path, arcname=path.relative_to(unpacked).as_posix())
            with self.assertRaises(CandidateTreeError):
                materialize_candidate_tree_archive(
                    tampered, candidate_commit=commit, destination=root / "rejected.git"
                )

    def test_raw_snapshot_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _parent, _commit = self._repository(root)
            snapshot = repo / "website/cfb/years/2026/data/snapshot.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text('{"games":[],"teams":[]}', encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "--quiet", "-m", "raw")
            with self.assertRaisesRegex(CandidateTreeError, "unsafe public bytes"):
                create_candidate_tree_archive(repo, _git(repo, "rev-parse", "HEAD"), root / "raw.tar.gz")
            snapshot.unlink()
            (repo / "link").symlink_to("website/index.html")
            _git(repo, "add", "-A")
            _git(repo, "commit", "--quiet", "-m", "symlink")
            with self.assertRaisesRegex(CandidateTreeError, "symlink"):
                create_candidate_tree_archive(repo, _git(repo, "rev-parse", "HEAD"), root / "link.tar.gz")

    def test_raw_source_shapes_are_rejected_regardless_of_name_or_embedding(self):
        cases = {
            "config/leak.json": b'{"snapshot":{"teams":[],"games":[]}}',
            "private/raw.bin": b'{"snapshot":{"teams":[],"games":[]}}',
            "website/leak.html": (
                b'<script>window.__raw={"teams":[],"games":[]}</script>'
            ),
            "website/leak.js": b'const raw={"teams":[],"games":[]};',
            "website/leak.css": b'/* raw={"teams":[],"games":[]} */',
        }
        for relative, raw in cases.items():
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, _parent, _commit = self._repository(root)
                leak = repo / relative
                leak.parent.mkdir(parents=True, exist_ok=True)
                leak.write_bytes(raw)
                _git(repo, "add", ".")
                _git(repo, "commit", "--quiet", "-m", "raw source")
                with self.assertRaisesRegex(CandidateTreeError, "unsafe public bytes"):
                    create_candidate_tree_archive(
                        repo, _git(repo, "rev-parse", "HEAD"), root / "raw.tar.gz"
                    )

    def test_archives_and_private_paths_are_rejected_even_when_renamed(self):
        cases = {
            "public/renamed.dat": b"PK\x03\x04" + (b"\x00" * 32),
            "public/evidence.7z": b"ordinary-looking content",
            "private/readme.txt": b"private evidence",
        }
        for relative, raw in cases.items():
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, _parent, _commit = self._repository(root)
                leak = repo / relative
                leak.parent.mkdir(parents=True, exist_ok=True)
                leak.write_bytes(raw)
                _git(repo, "add", ".")
                _git(repo, "commit", "--quiet", "-m", "unsafe transport")
                with self.assertRaisesRegex(CandidateTreeError, "unsafe public bytes"):
                    create_candidate_tree_archive(
                        repo, _git(repo, "rev-parse", "HEAD"), root / "raw.tar.gz"
                    )

    def test_materialization_rechecks_every_transported_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _parent, _commit = self._repository(root)
            leak = repo / "config/leak.json"
            leak.parent.mkdir(parents=True)
            leak.write_text(
                '{"snapshot":{"teams":[],"games":[]}}', encoding="utf-8"
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "--quiet", "-m", "unsafe tree")
            commit = _git(repo, "rev-parse", "HEAD")
            archive = root / "candidate-tree.tar.gz"
            with patch("cfb.candidate_tree.assert_public_bytes", return_value=None):
                create_candidate_tree_archive(repo, commit, archive)
            with self.assertRaisesRegex(CandidateTreeError, "unsafe public bytes"):
                materialize_candidate_tree_archive(
                    archive, candidate_commit=commit, destination=root / "rejected.git"
                )

    def test_ordinary_json_and_synthetic_fixture_arrays_remain_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _parent, _commit = self._repository(root)
            config = repo / "config/public.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"title":"SportsRank","enabled":true}', encoding="utf-8")
            source_root = Path(__file__).parents[1]
            for relative in (
                "tests/fixtures/cfbd/2024/fbs/games.json",
                "tests/fixtures/cfbd/2024/fbs/teams.json",
                "tests/fixtures/cfbd/2025/fbs/games.json",
                "tests/fixtures/cfbd/2025/fbs/teams.json",
            ):
                fixture = repo / relative
                fixture.parent.mkdir(parents=True, exist_ok=True)
                fixture.write_bytes((source_root / relative).read_bytes())
            _git(repo, "add", ".")
            _git(repo, "commit", "--quiet", "-m", "safe fixtures")
            archive = root / "candidate-tree.tar.gz"
            create_candidate_tree_archive(repo, _git(repo, "rev-parse", "HEAD"), archive)
            self.assertTrue(archive.is_file())


if __name__ == "__main__":
    unittest.main()
