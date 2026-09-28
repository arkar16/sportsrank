"""Focused Gate 1 regressions for provider-week release provenance."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
import tempfile
import unittest

from cfb.release import (
    _canonical_snapshot_checksum,
    _snapshot_from_payload,
    _snapshot_payload,
    _snapshot_week_calendar,
    _snapshot_uses_provider_metadata,
    build_release,
    validate_release,
)
from cfb.ranking_engine import PreviousFinal
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam
from cfb.week_calendar import calendar_provenance


STAMP = "2026-09-08T12:00:00+00:00"


def _snapshot(*, provider: bool = True, mixed: bool = False, partial: bool = False) -> SeasonSnapshot:
    teams = (
        SourceTeam("Alpha State", "Test"),
        SourceTeam("Beta Tech", "Test"),
    )
    provider_fields = {
        "provider_id": "401001",
        "date": "2024-08-24T12:00:00Z",
        "provider_week": 1,
    } if provider else {}
    if partial:
        provider_fields.pop("provider_id")
    first = SourceGame(
        week=0,
        home_team="Alpha State",
        home_classification="fbs",
        home_points=24,
        away_team="Beta Tech",
        away_classification="fbs",
        away_points=21,
        neutral_site=False,
        completed=True,
        disposition="completed",
        **provider_fields,
    )
    second = replace(first, provider_id=None, date=None, provider_week=None) if mixed else None
    games = (first, second) if second is not None else (first,)
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2024,
            "teams_fetched_at": STAMP,
            "games_fetched_at": STAMP,
            "complete_through_week": 0,
        }
    )
    return SeasonSnapshot("cfb", "FBS", 2024, teams, games, metadata, _checksum(metadata, teams, games))


def _base(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("published", encoding="utf-8")
    final = base / "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html"
    final.parent.mkdir(parents=True)
    final.write_text(
        "<html><body><table><thead><tr><th>school</th><th>cors</th>"
        "<th>wins_vs_expected</th></tr></thead><tbody>"
        "<tr><td>Alpha State</td><td>10</td><td>0</td></tr>"
        "<tr><td>Beta Tech</td><td>8</td><td>0</td></tr>"
        "</tbody></table></body></html>",
        encoding="utf-8",
    )
    return base


def _prior() -> PreviousFinal:
    return PreviousFinal(
        {"Alpha State": 10.0, "Beta Tech": 8.0},
        {"Alpha State": 0.0, "Beta Tech": 0.0},
        year=2023,
        classification="FBS",
    )


def _reseal(candidate, *, sync_release: bool = False) -> None:
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    if sync_release:
        release_path = candidate.site / "release.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        release["runs"] = manifest["runs"]
        release_path.write_text(
            json.dumps(release, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    for relative in manifest["artifact_checksums"]:
        path = candidate.site / relative
        if path.is_file():
            manifest["artifact_checksums"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.pop("manifest_checksum", None)
    manifest["manifest_checksum"] = hashlib.sha256(
        (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    ).hexdigest()
    candidate.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class Gate1WeekProvenanceTests(unittest.TestCase):
    def test_v3_payload_round_trip_retains_raw_provider_fields(self):
        snapshot = _snapshot()
        payload = _snapshot_payload(snapshot)
        self.assertEqual(
            {key: payload["games"][0][key] for key in ("week", "provider_week", "provider_id", "date")},
            {"week": 0, "provider_week": 1, "provider_id": "401001", "date": "2024-08-24T12:00:00Z"},
        )
        restored = _snapshot_from_payload(payload)
        self.assertEqual(restored.games, snapshot.games)
        self.assertEqual(_canonical_snapshot_checksum(restored), snapshot.checksum)
        self.assertTrue(_snapshot_uses_provider_metadata(restored))
        self.assertEqual(_snapshot_week_calendar(restored), calendar_provenance(2024))

    def test_release_seals_calendar_provenance_and_archive_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root)
            candidate = build_release(
                _snapshot(),
                root / "candidate",
                release_id="candidate",
                phase="preseason",
                previous_final=_prior(),
                published_site=base,
                timestamp=STAMP,
            )
            manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
            run = manifest["runs"][0]
            self.assertEqual(run["week_calendar"], calendar_provenance(2024))
            archived = json.loads((candidate.site / run["snapshot_archive_path"]).read_text(encoding="utf-8"))
            self.assertEqual(archived["games"][0]["provider_week"], 1)
            self.assertEqual(archived["games"][0]["provider_id"], "401001")
            self.assertEqual(archived["games"][0]["date"], "2024-08-24T12:00:00Z")
            report = validate_release(candidate, published_site=base)
            self.assertTrue(report.valid, [str(failure) for failure in report.failures])

    def test_forged_calendar_provenance_is_rejected_after_reseal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root)
            candidate = build_release(
                _snapshot(), root / "candidate", release_id="candidate", phase="preseason",
                previous_final=_prior(), published_site=base, timestamp=STAMP,
            )
            manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
            manifest["runs"][0]["week_calendar"]["boundary"] = "2024-08-25T00:00:00-04:00"
            candidate.manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            _reseal(candidate, sync_release=True)
            report = validate_release(candidate, published_site=base)
            self.assertFalse(report.valid)
            self.assertIn("runs.invalid", {failure.code for failure in report.failures})

    def test_forged_raw_provider_week_or_canonical_week_is_rejected_after_reseal(self):
        for field_name, value in (("provider_week", 2), ("week", 1)):
            with self.subTest(field_name=field_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                base = _base(root)
                candidate = build_release(
                    _snapshot(), root / "candidate", release_id="candidate", phase="preseason",
                    previous_final=_prior(), published_site=base, timestamp=STAMP,
                )
                manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
                archive_path = candidate.site / manifest["runs"][0]["snapshot_archive_path"]
                payload = json.loads(archive_path.read_text(encoding="utf-8"))
                payload["games"][0][field_name] = value
                archive_path.write_text(
                    json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                _reseal(candidate)
                report = validate_release(candidate, published_site=base)
                self.assertFalse(report.valid)
                self.assertIn("runs.invalid", {failure.code for failure in report.failures})

    def test_mixed_or_partial_provider_metadata_fails_before_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root)
            with self.assertRaisesRegex(ValueError, "mixed"):
                build_release(
                    _snapshot(mixed=True), root / "mixed", release_id="mixed", phase="preseason",
                    previous_final=_prior(), published_site=base, timestamp=STAMP,
                )
            with self.assertRaisesRegex(ValueError, "missing provider_id"):
                build_release(
                    _snapshot(partial=True), root / "partial", release_id="partial", phase="preseason",
                    previous_final=_prior(), published_site=base, timestamp=STAMP,
                )

    def test_refreshed_snapshot_root_is_compatible_without_provider_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "metadata-refresh-data" / "snapshots"
            root.mkdir(parents=True)
            source = _snapshot()
            payload = _snapshot_payload(source)
            path = root / "cfb-fbs-2024.json"
            path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            restored = _snapshot_from_payload(
                json.loads(path.read_text(encoding="utf-8"))
            )
            self.assertEqual(restored.year, 2024)
            self.assertEqual(len(restored.games), 1)
            self.assertTrue(_snapshot_uses_provider_metadata(restored))
            self.assertEqual(_canonical_snapshot_checksum(restored), restored.checksum)
            self.assertEqual(_snapshot_week_calendar(restored), calendar_provenance(2024))


if __name__ == "__main__":
    unittest.main()
