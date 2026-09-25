"""Focused tests for the trusted external recovery-input seam."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from cfb.recovery_inputs import RecoveryInputBundle, RecoveryInputError
from cfb import recovery
from cfb.recovery import compare_site_trees
from cfb import season_snapshot as season_snapshot_module
from cfb import postseason_registry as postseason_registry_module
from cfb.release import build_release
from cfb.season_snapshot import (
    SeasonSnapshot,
    _checksum,
    migrate_postseason_cache,
)
from types import MappingProxyType


def _write_bundle(root: Path) -> dict[str, str]:
    snapshots = root / "snapshots"
    snapshots.mkdir(parents=True)
    pins: dict[str, str] = {}
    manifest_files = []
    for year in (2024, 2025, 2026):
        state = {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": year,
            "teams_fetched_at": f"2026-09-08T20:01:0{year - 2024}+00:00",
            "games_fetched_at": f"2026-09-09T02:21:5{year - 2024}+00:00",
            "complete_through_week": -1,
            "teams": [],
            "games": [],
        }
        state["checksum"] = _checksum(state, (), ())
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
        relative = f"snapshots/cfb-fbs-{year}.json"
        path = root / relative
        path.write_text(encoded, encoding="utf-8")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        pins[relative] = digest
        manifest_files.append({"path": relative, "sha256": digest, "bytes": len(encoded.encode("utf-8"))})
    (root / "inputs-manifest.json").write_text(
        json.dumps(
            {
                "purpose": "Original immutable Schema 3 source snapshots for offline recovery replay",
                "source_location": "metadata-refresh-data/snapshots",
                "files": manifest_files,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return pins


class RecoveryInputBundleTests(unittest.TestCase):
    def test_resolves_pinned_raw_file_and_canonical_snapshot_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            bundle = RecoveryInputBundle.from_directory(
                root,
                trusted_file_sha256=pins,
            )

            identity = bundle.resolve(2024, "FBS")

            self.assertEqual(identity.source_file_sha256, pins["snapshots/cfb-fbs-2024.json"])
            self.assertEqual(len(identity.source_snapshot_checksum), 64)
            provenance = bundle.provenance()
            self.assertEqual(provenance["files"][0]["source_file_sha256"], identity.source_file_sha256)
            self.assertEqual(provenance["files"][0]["source_snapshot_checksum"], identity.source_snapshot_checksum)

    def test_rechecks_external_bytes_before_every_consume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            bundle = RecoveryInputBundle.from_directory(root, trusted_file_sha256=pins)
            source = root / "snapshots/cfb-fbs-2025.json"
            source.write_text(source.read_text(encoding="utf-8").replace('"games":[]', '"games":[{}]'), encoding="utf-8")

            with self.assertRaisesRegex(RecoveryInputError, "digest"):
                bundle.resolve(2025, "FBS")

    def test_manifest_cannot_authorize_a_tampered_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            source = root / "snapshots/cfb-fbs-2024.json"
            source.write_text(source.read_text(encoding="utf-8").replace('"year":2024', '"year":2099'), encoding="utf-8")
            manifest = json.loads((root / "inputs-manifest.json").read_text(encoding="utf-8"))
            changed = source.read_bytes()
            manifest["files"][0]["sha256"] = hashlib.sha256(changed).hexdigest()
            manifest["files"][0]["bytes"] = len(changed)
            (root / "inputs-manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RecoveryInputError, "digest"):
                RecoveryInputBundle.from_directory(root, trusted_file_sha256=pins)

    def test_canonical_checksum_is_checked_after_raw_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            source = root / "snapshots/cfb-fbs-2026.json"
            state = json.loads(source.read_text(encoding="utf-8"))
            state["checksum"] = "0" * 64
            encoded = json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
            source.write_text(encoded, encoding="utf-8")
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            pins["snapshots/cfb-fbs-2026.json"] = digest
            manifest = json.loads((root / "inputs-manifest.json").read_text(encoding="utf-8"))
            manifest["files"][2]["sha256"] = digest
            manifest["files"][2]["bytes"] = len(encoded.encode("utf-8"))
            (root / "inputs-manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RecoveryInputError, "canonical checksum"):
                RecoveryInputBundle.from_directory(root, trusted_file_sha256=pins)

    def test_archive_pin_binds_the_manifest_and_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            archive = root.parent / "source-inputs.tar.gz"
            with tarfile.open(archive, "w:gz") as target:
                for relative in ("inputs-manifest.json", *sorted(pins)):
                    target.add(root / relative, arcname=relative)
            archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()

            bundle = RecoveryInputBundle.from_directory(
                root,
                trusted_file_sha256=pins,
                archive=archive,
                expected_archive_sha256=archive_digest,
            )

            self.assertEqual(bundle.provenance()["bundle_sha256"], archive_digest)

    def test_bundle_rejects_public_tree_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            bundle = RecoveryInputBundle.from_directory(root, trusted_file_sha256=pins)

            with self.assertRaisesRegex(RecoveryInputError, "outside"):
                bundle.assert_external_to(root / "public")

    def test_bundle_archive_cannot_live_in_public_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "inputs"
            root.mkdir()
            pins = _write_bundle(root)
            public = root.parent / "public"
            public.mkdir()
            archive = public / "source-inputs.tar.gz"
            with tarfile.open(archive, "w:gz") as target:
                for relative in ("inputs-manifest.json", *sorted(pins)):
                    target.add(root / relative, arcname=relative)
            bundle = RecoveryInputBundle.from_directory(
                root,
                trusted_file_sha256=pins,
                archive=archive,
                expected_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            )

            with self.assertRaisesRegex(RecoveryInputError, "outside"):
                bundle.assert_external_to(public)

    def test_recovery_cli_carries_explicit_source_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            arguments = [
                "migrate-postseason",
                "--source-root",
                str(root / "snapshots"),
                "--destination-root",
                str(root / "destination"),
                "--source-input-root",
                str(root),
            ]
            for relative, digest in sorted(pins.items()):
                arguments.extend(("--source-input-pin", f"{relative}={digest}"))
            parsed = recovery.build_parser().parse_args(arguments)

            bundle = recovery._source_inputs(parsed)

            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle.source_root, (root / "snapshots").resolve())

    def test_trusted_migration_preserves_the_existing_schema4_checksum(self):
        """Raw input trust must not become part of migration checksum bytes."""

        class FakeRegistry:
            version = "test-registry"
            entries = ()
            official_sources = {}
            source_evidence_sha256 = None
            computed_checksum = "b" * 64

            def __init__(self, snapshot_checksums):
                self.snapshot_checksums = snapshot_checksums
                self.provenance = {
                    "version": self.version,
                    "policy_id": "test-policy",
                    "checksum": self.computed_checksum,
                    "entry_count": 0,
                    "official_sources": {},
                    "snapshot_checksums": {
                        str(year): checksum
                        for year, checksum in sorted(snapshot_checksums.items())
                    },
                    "source_evidence_sha256": None,
                }

            def lookup(self, season, provider_id):
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "inputs"
            pins = _write_bundle(input_root)
            source_checksums = {
                year: json.loads(
                    (input_root / f"snapshots/cfb-fbs-{year}.json").read_text(
                        encoding="utf-8"
                    )
                )["checksum"]
                for year in (2024, 2025, 2026)
            }
            registry = FakeRegistry(source_checksums)
            trusted_inputs = RecoveryInputBundle.from_directory(
                input_root,
                trusted_file_sha256=pins,
            )

            # A tiny synthetic registry keeps this regression independent of
            # the large retained evidence bundle while exercising the same
            # migration checksum boundary.
            with patch.object(season_snapshot_module, "PINNED_REGISTRY", registry), patch.object(
                season_snapshot_module, "trusted_registry", return_value=registry
            ), patch.object(postseason_registry_module, "PINNED_REGISTRY", registry):
                legacy = migrate_postseason_cache(
                    input_root / "snapshots",
                    root / "legacy-migration",
                    seasons=(2024, 2025, 2026),
                )
                trusted = migrate_postseason_cache(
                    input_root / "snapshots",
                    root / "trusted-migration",
                    seasons=(2024, 2025, 2026),
                    source_inputs=trusted_inputs,
                )

            for legacy_path, trusted_path in zip(legacy, trusted, strict=True):
                legacy_state = json.loads(legacy_path.read_text(encoding="utf-8"))
                trusted_state = json.loads(trusted_path.read_text(encoding="utf-8"))
                self.assertEqual(legacy_state["checksum"], trusted_state["checksum"])
                self.assertEqual(
                    legacy_state["migration_provenance"],
                    trusted_state["migration_provenance"],
                )
                self.assertNotIn(
                    "source_input_provenance",
                    trusted_state["migration_provenance"],
                )


class RecoveryReportTests(unittest.TestCase):
    def test_public_tree_report_classifies_timestamp_and_content_differences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference"
            candidate = root / "candidate"
            reference.mkdir()
            candidate.mkdir()
            (reference / "timestamp.html").write_text(
                "<p>Last updated: 2026-09-08T12:00:00+00:00</p><p>value 1</p>\n",
                encoding="utf-8",
            )
            (candidate / "timestamp.html").write_text(
                "<p>Last updated: 2026-09-09T12:00:00+00:00</p><p>value 1</p>\n",
                encoding="utf-8",
            )
            (reference / "numeric.html").write_text("<p>score 1.0</p>\n", encoding="utf-8")
            (candidate / "numeric.html").write_text("<p>score 1.5</p>\n", encoding="utf-8")
            (reference / "deleted.html").write_text("old\n", encoding="utf-8")
            (candidate / "added.html").write_text("new\n", encoding="utf-8")
            (reference / "same.txt").write_text("same\n", encoding="utf-8")
            (candidate / "same.txt").write_text("same\n", encoding="utf-8")

            report = compare_site_trees(candidate, {"captured-live": reference})[
                "captured-live"
            ]

            self.assertEqual(report["changed"], ["numeric.html", "timestamp.html"])
            self.assertEqual(report["timestamp_or_provenance"], ["timestamp.html"])
            self.assertEqual(report["numerical_or_content"], ["added.html", "deleted.html", "numeric.html"])
            self.assertEqual(report["added"], ["added.html"])
            self.assertEqual(report["deleted"], ["deleted.html"])
            self.assertEqual(report["unchanged"], ["same.txt"])
            self.assertEqual(
                report["unexplained"],
                ["added.html", "deleted.html", "numeric.html", "timestamp.html"],
            )

    def test_explicit_explanation_ledger_is_required_to_close_a_public_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference"
            candidate = root / "candidate"
            reference.mkdir()
            candidate.mkdir()
            (reference / "ranking.html").write_text("<p>score 1.0</p>\n", encoding="utf-8")
            (candidate / "ranking.html").write_text("<p>score 1.5</p>\n", encoding="utf-8")

            without_ledger = compare_site_trees(candidate, {"prepared": reference})[
                "prepared"
            ]
            self.assertIn("ranking.html", without_ledger["unexplained"])

            with_ledger = compare_site_trees(
                candidate,
                {"prepared": reference},
                explanation_ledger={
                    "prepared": {
                        "ranking.html": "independently validated numerical output",
                    }
                },
            )["prepared"]
            self.assertEqual(with_ledger["unexplained"], [])
            self.assertEqual(
                with_ledger["explanation_ledger"],
                {"ranking.html": "independently validated numerical output"},
            )


class ReleaseRecoveryBindingTests(unittest.TestCase):
    def test_migrated_snapshot_cannot_build_without_trusted_external_inputs(self):
        migration = {
            "kind": "postseason-calendar-repair",
            "source_schema_version": 3,
            "source_snapshot_checksum": "a" * 64,
            "target_schema_version": 4,
            "registry_version": "postseason-recovery-v2",
            "registry_checksum": "b" * 64,
        }
        metadata = {
            "schema_version": 4,
            "teams_fetched_at": "2026-09-08T20:01:00+00:00",
            "games_fetched_at": "2026-09-09T02:21:00+00:00",
            "complete_through_week": -1,
            "calendar_provenance": None,
            "correction_registry_provenance": None,
            "migration_provenance": migration,
        }
        snapshot = SeasonSnapshot(
            "cfb",
            "FBS",
            2026,
            (),
            (),
            MappingProxyType(metadata),
            _checksum(metadata, (), ()),
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "published"
            base.mkdir()
            (base / "index.html").write_text("baseline\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "trusted external source inputs"):
                build_release(
                    snapshot,
                    Path(directory) / "candidate",
                    release_id="candidate",
                    phase="preseason",
                    published_site=base,
                )


if __name__ == "__main__":
    unittest.main()
