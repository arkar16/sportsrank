"""Regressions for coexisting immutable recovery source versions."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb import postseason_registry as postseason_registry_module
from cfb import season_snapshot as season_snapshot_module
from cfb.recovery_inputs import RecoveryInputBundle, RecoveryInputError
from cfb.release import _source_input_provenance
from cfb.season_snapshot import SeasonSnapshot, _checksum, migrate_postseason_cache
from cfb.season_source import SourceGame, SourceTeam, normalize_game
from cfb.week_calendar import calendar_provenance

from tests.test_recovery_inputs import _write_bundle


class _EmptyRegistry:
    version = "test-registry"
    entries = ()
    official_sources: dict[str, object] = {}
    source_evidence_sha256 = None
    computed_checksum = "b" * 64

    def __init__(self, source_checksum: str):
        self.snapshot_checksums = {2026: source_checksum}
        self.provenance = {
            "version": self.version,
            "policy_id": "test-policy",
            "checksum": self.computed_checksum,
            "entry_count": 0,
            "official_sources": {},
            "snapshot_checksums": {"2026": source_checksum},
            "source_evidence_sha256": None,
        }

    def lookup(self, season: int, provider_id: str):
        return None


def _native_schema4_state() -> dict[str, object]:
    teams = (
        SourceTeam(school="Home", conference="Test"),
        SourceTeam(school="Away", conference="Test"),
    )
    games = (
        SourceGame(
            week=1,
            home_team="Home",
            home_classification="fbs",
            home_points=24,
            away_team="Away",
            away_classification="fbs",
            away_points=17,
            neutral_site=False,
            provider_id="provider-1",
            date="2026-09-05T16:00:00+00:00",
            provider_week=1,
            completed=True,
            disposition="completed",
            provider_season_type="regular",
            phase="regular",
            phase_source="provider",
        ),
    )
    state: dict[str, object] = {
        "schema_version": 4,
        "sport": "cfb",
        "classification": "FBS",
        "year": 2026,
        "teams_fetched_at": "2026-09-25T12:00:00+00:00",
        "games_fetched_at": "2026-09-25T12:01:00+00:00",
        "complete_through_week": 1,
        "calendar_provenance": calendar_provenance(2026),
        "correction_registry_provenance": None,
        "migration_provenance": None,
        "teams": [asdict(team) for team in teams],
        "games": [asdict(game) for game in games],
    }
    state["checksum"] = _checksum(state, teams, games)
    return state


def _add_versioned_snapshot(
    root: Path,
    pins: dict[str, str],
    state: dict[str, object],
    *,
    directory_checksum: str | None = None,
) -> str:
    checksum = str(directory_checksum or state["checksum"])
    relative = f"snapshots/{checksum}/cfb-fbs-2026.json"
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    pins[relative] = digest
    manifest_path = root / "inputs-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {"path": relative, "sha256": digest, "bytes": len(encoded.encode())}
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return relative


class VersionedRecoveryInputTests(unittest.TestCase):
    def test_original_and_fresh_2026_resolve_by_exact_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "inputs"
            pins = _write_bundle(root)
            original = json.loads(
                (root / "snapshots/cfb-fbs-2026.json").read_text(encoding="utf-8")
            )
            fresh = _native_schema4_state()
            fresh_relative = _add_versioned_snapshot(root, pins, fresh)

            bundle = RecoveryInputBundle.from_directory(
                root, trusted_file_sha256=pins
            )

            old_identity = bundle.resolve(
                2026, source_snapshot_checksum=str(original["checksum"])
            )
            fresh_identity = bundle.resolve(
                2026, source_snapshot_checksum=str(fresh["checksum"])
            )
            self.assertEqual(old_identity.relative_path, "snapshots/cfb-fbs-2026.json")
            self.assertEqual(old_identity.schema_version, 3)
            self.assertEqual(fresh_identity.relative_path, fresh_relative)
            self.assertEqual(fresh_identity.schema_version, 4)
            with self.assertRaisesRegex(RecoveryInputError, "ambiguous"):
                bundle.resolve(2026)

            registry = _EmptyRegistry(str(original["checksum"]))
            with patch.object(
                season_snapshot_module, "PINNED_REGISTRY", registry
            ), patch.object(
                season_snapshot_module, "trusted_registry", return_value=registry
            ), patch.object(
                postseason_registry_module, "PINNED_REGISTRY", registry
            ):
                migrated_paths = migrate_postseason_cache(
                    root / "snapshots",
                    base / "migrated",
                    seasons=(2026,),
                    source_inputs=bundle,
                )
            migrated = json.loads(migrated_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(
                migrated["migration_provenance"]["source_snapshot_checksum"],
                original["checksum"],
            )
            migrated_snapshot = SeasonSnapshot(
                sport="cfb",
                classification="FBS",
                year=2026,
                teams=tuple(SourceTeam(**item) for item in migrated["teams"]),
                games=tuple(normalize_game(item) for item in migrated["games"]),
                metadata={
                    key: migrated.get(key)
                    for key in (
                        "schema_version",
                        "teams_fetched_at",
                        "games_fetched_at",
                        "complete_through_week",
                        "calendar_provenance",
                        "correction_registry_provenance",
                        "migration_provenance",
                    )
                },
                checksum=str(migrated["checksum"]),
            )
            migrated_provenance = _source_input_provenance(
                migrated_snapshot, bundle
            )
            self.assertIsNotNone(migrated_provenance)
            assert migrated_provenance is not None
            self.assertEqual(
                migrated_provenance["path"], "snapshots/cfb-fbs-2026.json"
            )
            self.assertEqual(migrated_provenance["schema_version"], 3)

            snapshot = SeasonSnapshot(
                sport="cfb",
                classification="FBS",
                year=2026,
                teams=tuple(SourceTeam(**item) for item in fresh["teams"]),
                games=tuple(SourceGame(**item) for item in fresh["games"]),
                metadata={
                    key: fresh.get(key)
                    for key in (
                        "schema_version",
                        "teams_fetched_at",
                        "games_fetched_at",
                        "complete_through_week",
                        "calendar_provenance",
                        "correction_registry_provenance",
                        "migration_provenance",
                    )
                },
                checksum=str(fresh["checksum"]),
            )
            provenance = _source_input_provenance(snapshot, bundle)
            self.assertIsNotNone(provenance)
            assert provenance is not None
            self.assertEqual(provenance["path"], fresh_relative)
            self.assertEqual(provenance["schema_version"], 4)

    def test_duplicate_source_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            original = json.loads(
                (root / "snapshots/cfb-fbs-2026.json").read_text(encoding="utf-8")
            )
            _add_versioned_snapshot(root, pins, original)

            with self.assertRaisesRegex(RecoveryInputError, "duplicate source version"):
                RecoveryInputBundle.from_directory(root, trusted_file_sha256=pins)

    def test_version_directory_must_equal_verified_snapshot_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            fresh = _native_schema4_state()
            _add_versioned_snapshot(
                root, pins, fresh, directory_checksum="f" * 64
            )

            with self.assertRaisesRegex(RecoveryInputError, "versioned path"):
                RecoveryInputBundle.from_directory(root, trusted_file_sha256=pins)

    def test_native_schema4_tampering_is_rejected_with_repinned_outer_bytes(self):
        mutations = {
            "phase": lambda state: state["games"][0].update(phase="postseason"),
            "calendar": lambda state: state.update(calendar_provenance={"policy_id": "forged"}),
            "checksum": lambda state: state.update(checksum="0" * 64),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pins = _write_bundle(root)
                fresh = _native_schema4_state()
                original_checksum = str(fresh["checksum"])
                mutate(fresh)
                _add_versioned_snapshot(
                    root,
                    pins,
                    fresh,
                    directory_checksum=original_checksum,
                )

                with self.assertRaises(RecoveryInputError):
                    RecoveryInputBundle.from_directory(
                        root, trusted_file_sha256=pins
                    )

    def test_unsupported_schema_is_rejected_even_when_outer_bytes_are_repinned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = _write_bundle(root)
            fresh = _native_schema4_state()
            fresh["schema_version"] = 5
            _add_versioned_snapshot(root, pins, fresh)

            with self.assertRaisesRegex(RecoveryInputError, "schema is unsupported"):
                RecoveryInputBundle.from_directory(root, trusted_file_sha256=pins)


if __name__ == "__main__":
    unittest.main()
