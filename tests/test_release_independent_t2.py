"""Independent provenance checks for the staged recovery Release validator.

These checks deliberately reseal the outer manifest after changing an inner
artifact.  They exercise whether validation independently enforces the
provenance contracts rather than merely detecting an incidental byte mismatch.
"""

from datetime import datetime, timezone
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from types import MappingProxyType

from bs4 import BeautifulSoup

from cfb.release import build_release, validate_release
from cfb.ranking_engine import MODEL_VERSION, PreviousFinal, ranking_for_week, records_for_week
from cfb.request_meter import RequestMeter
from cfb import recovery
from cfb.season_snapshot import SeasonSnapshot
from cfb.season_snapshot import SeasonSnapshotService
from cfb.season_source import FixtureSeasonSource, SourceGame, SourceTeam
from cfb.snapshot_cache import SnapshotCache


FIXTURES = Path(__file__).parent / "fixtures" / "cfbd"
FIXED = "2026-09-04T12:00:00+00:00"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _reseal_manifest(candidate) -> None:
    """Recompute all listed artifact digests and the enclosing digest."""

    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    for relative in manifest["artifact_checksums"]:
        artifact = candidate.site / relative
        manifest["artifact_checksums"][relative] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest.pop("manifest_checksum", None)
    manifest["manifest_checksum"] = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
    candidate.manifest_path.write_text(_canonical_json(manifest), encoding="utf-8")


class ReleaseProvenanceIndependentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        source = FixtureSeasonSource(FIXTURES)
        service = SeasonSnapshotService(
            source,
            SnapshotCache(root / "cache"),
            clock=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
        )
        self.service = service
        self.snapshot = service.get(2025, "FBS")
        self.root = root
        self.base = root / "published"
        self.base.mkdir()
        (self.base / "index.html").write_text("legacy published bytes", encoding="utf-8")
        for snapshot in (self.snapshot, self.service.get(2024, "FBS")):
            prior_year = snapshot.year - 1
            path = self.base / f"cfb/years/{prior_year}/rankings/{prior_year}_FINAL_FBS_cors.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = "".join(
                f"<tr><td>{team.school}</td><td>{float(index)}</td><td>0.0</td></tr>"
                for index, team in enumerate(snapshot.teams)
            )
            path.write_text(
                f"<html><body><table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead><tbody>{rows}</tbody></table></body></html>",
                encoding="utf-8",
            )

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, year=2025):
        snapshot = self.snapshot if year == 2025 else self.service.get(year, "FBS")
        return build_release(
            snapshot,
            self.root / "candidate",
            release_id="candidate",
            timestamp=FIXED,
            code_revision="independent-test",
            published_site=self.base,
            previous_final=PreviousFinal(
                {team.school: float(index) for index, team in enumerate(snapshot.teams)},
                {team.school: 0.0 for team in snapshot.teams},
            ),
            phase="final" if year == 2024 else "week",
        )

    def test_every_owned_artifact_must_have_a_checksum_entry(self):
        candidate = self.build()
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        victim = manifest["owned_artifacts"][0]
        del manifest["artifact_checksums"][victim]
        manifest.pop("manifest_checksum", None)
        manifest["manifest_checksum"] = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
        candidate.manifest_path.write_text(_canonical_json(manifest), encoding="utf-8")

        report = validate_release(candidate, published_site=self.base)

        self.assertFalse(report.valid)
        self.assertTrue(report.failures)

    def test_production_smoke_meters_one_teams_call_without_games_or_secret_leak(self):
        cache_dir = self.root / "smoke-cache"
        secret = "smoke-test-secret"
        client = MagicMock()
        teams_api = MagicMock()
        teams_api.return_value.get_fbs_teams.return_value = [
            {"school": "Alpha State", "conference": "Test"}
        ]
        games_api = MagicMock()
        output = StringIO()
        errors = StringIO()
        with patch.dict(os.environ, {"CFBD_API_KEY": secret}, clear=True):
            with patch("cfb.season_source.create_api_client") as create_client:
                with patch("cfb.season_source.cfbd.TeamsApi", teams_api):
                    with patch("cfb.season_source.cfbd.GamesApi", games_api):
                        create_client.return_value.__enter__.return_value = client
                        with redirect_stdout(output), redirect_stderr(errors):
                            result = recovery.main(
                                [
                                    "smoke",
                                    "2025",
                                    "--classification",
                                    "FBS",
                                    "--cache-dir",
                                    str(cache_dir),
                                ]
                            )

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["calls"], 1)
        teams_api.return_value.get_fbs_teams.assert_called_once_with(year=2025)
        games_api.assert_not_called()
        records = RequestMeter(cache_dir / "cfbd_requests.sqlite3").audit_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["endpoint"], "teams")
        self.assertEqual(records[0]["purpose"], "scheduled")
        self.assertEqual(records[0]["category"], "scheduled")
        self.assertEqual(records[0]["cache_decision"], "miss")
        self.assertEqual(records[0]["budget_impact"], 1)
        self.assertEqual(records[0]["outcome"], "succeeded")
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())
        self.assertNotIn(secret.encode("utf-8"), (cache_dir / "cfbd_requests.sqlite3").read_bytes())

    def test_snapshot_checksum_is_recomputed_from_payload(self):
        candidate = self.build()
        snapshot_path = candidate.site / "cfb/years/2025/data/snapshot.json"
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        original_checksum = payload["checksum"]
        payload["teams"][0]["conference"] = "Tampered Conference"
        self.assertEqual(payload["checksum"], original_checksum)
        snapshot_path.write_text(_canonical_json(payload), encoding="utf-8")
        _reseal_manifest(candidate)

        report = validate_release(candidate, published_site=self.base)

        self.assertFalse(report.valid)
        self.assertTrue(report.failures)

    def test_final_ranking_values_are_checked_against_the_deterministic_engine(self):
        candidate = self.build(2024)
        final_path = candidate.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
        document = BeautifulSoup(final_path.read_text(encoding="utf-8"), "html.parser")
        headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
        cors_index = headers.index("cors")
        document.select_one("tbody tr").find_all("td")[cors_index].string = "999.0"
        final_path.write_text(str(document), encoding="utf-8")
        _reseal_manifest(candidate)

        report = validate_release(candidate, published_site=self.base)

        self.assertFalse(report.valid)
        self.assertTrue(report.failures)

    def test_incomplete_candidate_has_no_final_or_current_history_outcome(self):
        candidate = self.build()
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        final_relative = "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html"
        self.assertFalse(manifest["season_complete"])
        self.assertNotIn(final_relative, manifest["owned_artifacts"])
        self.assertNotIn(final_relative, manifest["required_artifacts"])
        self.assertFalse((candidate.site / final_relative).exists())
        for filename in ("nc_FBS_CFB_output.html", "wt_FBS_CFB_output.html"):
            document = BeautifulSoup(
                (candidate.site / "cfb/history" / filename).read_text(encoding="utf-8"),
                "html.parser",
            )
            self.assertFalse(document.select("tbody tr"))

    def test_resealed_manifest_boundary_metadata_cannot_forge_completion(self):
        candidate = self.build()
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        manifest["complete_through_week"] = 99
        manifest["scheduled_end_week"] = 99
        manifest["season_complete"] = True
        manifest.pop("manifest_checksum", None)
        manifest["manifest_checksum"] = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
        candidate.manifest_path.write_text(_canonical_json(manifest), encoding="utf-8")

        report = validate_release(candidate, published_site=self.base)

        self.assertFalse(report.valid)
        self.assertTrue({failure.code for failure in report.failures} & {"season.boundary", "season.completion"})

    def test_pre_1996_tie_display_does_not_change_cors_v040_formula(self):
        teams = (SourceTeam("Alpha", "Legacy"), SourceTeam("Beta", "Legacy"))
        games = (
            SourceGame(1, "Alpha", "FBS", 7, "Beta", "FBS", 0, False),
            SourceGame(1, "Alpha", "FBS", 3, "FCS One", "FCS", 3, False),
        )
        metadata = MappingProxyType({"complete_through_week": 1})
        legacy = SeasonSnapshot("cfb", "FBS", 1995, teams, games, metadata, "legacy")
        modern = SeasonSnapshot("cfb", "FBS", 2025, teams, games, metadata, "modern")
        legacy_record = next(row for row in records_for_week(legacy, 1) if row["school"] == "Alpha")
        modern_record = next(row for row in records_for_week(modern, 1) if row["school"] == "Alpha")
        legacy_ranking = {row["school"]: row for row in ranking_for_week(legacy, 1, {"Alpha": 10.0, "Beta": 5.0})}
        modern_ranking = {row["school"]: row for row in ranking_for_week(modern, 1, {"Alpha": 10.0, "Beta": 5.0})}

        self.assertEqual(MODEL_VERSION, "v0.4.0")
        self.assertEqual(legacy_record["record"], "1-0-1")
        self.assertEqual(legacy_record["win_pct"], 0.5)
        self.assertEqual(modern_record["win_pct"], 1.0)
        self.assertEqual(legacy_ranking["Alpha"]["cors"], modern_ranking["Alpha"]["cors"])
        self.assertEqual(legacy_ranking["Alpha"]["mov"], modern_ranking["Alpha"]["mov"])

    def test_legacy_full_season_path_uses_snapshot_derived_end_week(self):
        import cfb.main as legacy_main

        service = MagicMock()
        service.get.return_value = type(
            "SnapshotStub", (), {"games": (type("Game", (), {"week": 2})(), type("Game", (), {"week": 7})())}
        )()
        calculation = MagicMock()
        with patch.object(legacy_main, "single_week_calc", MagicMock()):
            with patch.object(legacy_main, "full_season_calc", calculation):
                legacy_main.run_calculations(
                    "full_season", 2025, 2, 0, "FBS", 2, 0, FIXED, service
                )

        self.assertEqual(calculation.call_args.args[2], 7)
        service.get.assert_called_once_with(2025, "FBS")


if __name__ == "__main__":
    unittest.main()
