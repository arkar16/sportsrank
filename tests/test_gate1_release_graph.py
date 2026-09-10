"""Gate 1 release graph regressions.

These tests state the domain-required public paths directly instead of deriving
their expectations from the renderer or manifest.  That keeps a renderer and
validator that share the same omission from reporting a false green release.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

from bs4 import BeautifulSoup

from cfb.release import build_release, validate_release
from cfb.ranking_engine import PreviousFinal, season_rankings, spreads_for_week
from cfb.season_snapshot import SeasonSnapshot, SeasonSnapshotService, _checksum
from cfb.season_source import FixtureSeasonSource, SourceGame, SourceTeam
from cfb.snapshot_cache import SnapshotCache


FIXTURES = Path(__file__).parent / "fixtures" / "cfbd"
STAMP = "2026-09-08T12:00:00+00:00"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _reseal(candidate) -> None:
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    for relative in manifest["artifact_checksums"]:
        path = candidate.site / relative
        if path.is_file():
            manifest["artifact_checksums"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.pop("manifest_checksum", None)
    manifest["manifest_checksum"] = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
    candidate.manifest_path.write_text(_canonical_json(manifest), encoding="utf-8")


def _table_rows(path: Path) -> list[dict[str, str]]:
    document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
    return [
        dict(zip(headers, (cell.get_text(strip=True) for cell in row.find_all("td"))))
        for row in document.select("tbody tr")
    ]


def _replace_first_cell(path: Path, field: str, value: str) -> None:
    document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
    index = headers.index(field)
    document.select_one("tbody tr").find_all("td")[index].string = value
    path.write_text(str(document), encoding="utf-8")


class Gate1ReleaseGraphTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.service = SeasonSnapshotService(
            FixtureSeasonSource(FIXTURES),
            SnapshotCache(self.root / "cache"),
            clock=lambda: datetime(2026, 9, 8, 12, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _prior(snapshot: SeasonSnapshot) -> PreviousFinal:
        return PreviousFinal(
            {team.school: float(index + 10) for index, team in enumerate(snapshot.teams)},
            {team.school: 0.0 for team in snapshot.teams},
        )

    def _base(self, name: str, snapshot: SeasonSnapshot) -> Path:
        base = self.root / name
        base.mkdir()
        (base / "index.html").write_text("legacy published bytes", encoding="utf-8")
        prior = self._prior(snapshot)
        path = base / f"cfb/years/{snapshot.year - 1}/rankings/{snapshot.year - 1}_FINAL_FBS_cors.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = "".join(
            f"<tr><td>{school}</td><td>{cors}</td><td>0.0</td></tr>"
            for school, cors in prior.cors.items()
        )
        path.write_text(
            "<html><body><table><thead><tr><th>school</th><th>cors</th>"
            f"<th>wins_vs_expected</th></tr></thead><tbody>{rows}</tbody></table></body></html>",
            encoding="utf-8",
        )
        return base

    def _complete_2025(self) -> SeasonSnapshot:
        partial = self.service.get(2025, "FBS")
        future = partial.games[-1]
        completed = SourceGame(
            future.week,
            future.home_team,
            future.home_classification,
            28,
            future.away_team,
            future.away_classification,
            7,
            future.neutral_site,
            provider_id=future.provider_id,
            date=future.date,
            completed=True,
            disposition="completed",
            disposition_source=future.disposition_source,
        )
        games = partial.games[:-1] + (completed,)
        metadata = MappingProxyType({**dict(partial.metadata), "complete_through_week": 2})
        return SeasonSnapshot(
            partial.sport,
            partial.classification,
            partial.year,
            partial.teams,
            games,
            metadata,
            _checksum(metadata, partial.teams, games),
        )

    @staticmethod
    def _complete_with_week_zero() -> SeasonSnapshot:
        teams = (
            SourceTeam("Alpha State", "Test"),
            SourceTeam("Beta Tech", "Test"),
        )
        games = (
            SourceGame(
                0,
                "Alpha State",
                "fbs",
                21,
                "Beta Tech",
                "fbs",
                14,
                False,
                provider_id="w0",
                completed=True,
                disposition="completed",
            ),
            SourceGame(
                1,
                "Beta Tech",
                "fbs",
                17,
                "Alpha State",
                "fbs",
                10,
                False,
                provider_id="w1",
                completed=True,
                disposition="completed",
            ),
        )
        metadata = MappingProxyType(
            {
                "schema_version": 3,
                "sport": "cfb",
                "classification": "FBS",
                "year": 2025,
                "teams_fetched_at": "2026-09-08T12:00:00+00:00",
                "games_fetched_at": "2026-09-08T12:00:00+00:00",
                "complete_through_week": 1,
            }
        )
        return SeasonSnapshot(
            "cfb",
            "FBS",
            2025,
            teams,
            games,
            metadata,
            _checksum(metadata, teams, games),
        )

    def test_final_overlays_publish_preseason_and_week_zero_spread_for_2024_and_2025(self):
        for year, snapshot in ((2024, self.service.get(2024, "FBS")), (2025, self._complete_2025())):
            with self.subTest(year=year):
                base = self._base(f"published-{year}", snapshot)
                candidate = build_release(
                    snapshot,
                    self.root / f"candidate-{year}",
                    release_id=f"candidate-{year}",
                    phase="final",
                    timestamp=STAMP,
                    published_site=base,
                    previous_final=self._prior(snapshot),
                )
                year_root = candidate.site / f"cfb/years/{year}"

                # These are required by the lifecycle contract even when W0 has
                # no scheduled FBS game in the historical snapshot.
                self.assertTrue((year_root / f"rankings/{year}_PRESEASON_FBS_cors.html").is_file())
                self.assertTrue((year_root / f"spread/{year}_W0_FBS_spread.html").is_file())

    def test_resealed_final_overlay_cannot_hide_required_preseason_artifact(self):
        snapshot = self.service.get(2024, "FBS")
        base = self._base("published", snapshot)
        candidate = build_release(
            snapshot,
            self.root / "candidate",
            release_id="candidate",
            phase="final",
            timestamp=STAMP,
            published_site=base,
            previous_final=self._prior(snapshot),
        )
        victim = "cfb/years/2024/rankings/2024_PRESEASON_FBS_cors.html"
        victim_path = candidate.site / victim
        self.assertTrue(victim_path.is_file())
        victim_path.unlink()
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        manifest["owned_artifacts"].remove(victim)
        manifest["required_artifacts"].remove(victim)
        manifest["artifact_checksums"].pop(victim)
        candidate.manifest_path.write_text(_canonical_json(manifest), encoding="utf-8")
        _reseal(candidate)

        report = validate_release(candidate, published_site=base)

        self.assertFalse(report.valid)
        self.assertIn("artifact.required", {failure.code for failure in report.failures})

    def test_final_preseason_and_w0_spread_values_validate_independently(self):
        preseason_snapshot = self.service.get(2024, "FBS")
        preseason_base = self._base("published-preseason", preseason_snapshot)
        preseason_candidate = build_release(
            preseason_snapshot,
            self.root / "candidate-preseason",
            release_id="candidate-preseason",
            phase="final",
            timestamp=STAMP,
            published_site=preseason_base,
            previous_final=self._prior(preseason_snapshot),
        )
        preseason_path = (
            preseason_candidate.site
            / "cfb/years/2024/rankings/2024_PRESEASON_FBS_cors.html"
        )
        _replace_first_cell(preseason_path, "cors", "999999.0")
        _reseal(preseason_candidate)
        preseason_report = validate_release(preseason_candidate, published_site=preseason_base)
        self.assertFalse(preseason_report.valid)
        self.assertIn("ranking.value", {failure.code for failure in preseason_report.failures})

        w0_snapshot = self._complete_with_week_zero()
        w0_base = self._base("published-w0", w0_snapshot)
        w0_candidate = build_release(
            w0_snapshot,
            self.root / "candidate-w0",
            release_id="candidate-w0",
            phase="final",
            timestamp=STAMP,
            published_site=w0_base,
            previous_final=self._prior(w0_snapshot),
        )
        w0_path = w0_candidate.site / "cfb/years/2025/spread/2025_W0_FBS_spread.html"
        _replace_first_cell(w0_path, "spread_value", "999999.0")
        _reseal(w0_candidate)
        w0_report = validate_release(w0_candidate, published_site=w0_base)
        self.assertFalse(w0_report.valid)
        self.assertIn("spread.reconcile", {failure.code for failure in w0_report.failures})

    def test_week_overlay_emits_next_scheduled_slate_and_spread_from_target_ranking(self):
        snapshot = self.service.get(2025, "FBS")
        base = self._base("published", snapshot)
        prior = self._prior(snapshot)
        candidate = build_release(
            snapshot,
            self.root / "candidate",
            release_id="candidate",
            phase="week",
            target_week=1,
            timestamp=STAMP,
            published_site=base,
            previous_final=prior,
        )
        year_root = candidate.site / "cfb/years/2025"
        slate = year_root / "data/slate/weekly_slate/2025_W2_FBS_slate.html"
        spread = year_root / "spread/2025_W2_FBS_spread.html"
        self.assertTrue(slate.is_file())
        self.assertTrue(spread.is_file())
        self.assertFalse((year_root / "rankings/2025_W2_FBS_cors.html").exists())
        self.assertFalse((year_root / "data/results/weekly_results/2025_W2_FBS_results.html").exists())

        target_rows = season_rankings(snapshot, 1, prior)[1]
        expected = spreads_for_week(snapshot, 2, target_rows)
        actual = _table_rows(spread)
        self.assertEqual(len(actual), len(expected))
        self.assertEqual(float(actual[0]["home_cors"]), expected[0]["home_cors"])
        self.assertEqual(float(actual[0]["away_cors"]), expected[0]["away_cors"])
        self.assertEqual(actual[0]["home_team"], expected[0]["home_team"])

    def test_next_week_prediction_is_independent_of_future_scores(self):
        partial = self.service.get(2025, "FBS")
        future = partial.games[-1]
        scored_future = SourceGame(
            future.week,
            future.home_team,
            future.home_classification,
            999,
            future.away_team,
            future.away_classification,
            0,
            future.neutral_site,
            provider_id=future.provider_id,
            date=future.date,
            completed=True,
            disposition="scheduled",
            disposition_source=future.disposition_source,
        )
        metadata = MappingProxyType(dict(partial.metadata))
        scored_snapshot = SeasonSnapshot(
            partial.sport,
            partial.classification,
            partial.year,
            partial.teams,
            partial.games[:-1] + (scored_future,),
            metadata,
            _checksum(metadata, partial.teams, partial.games[:-1] + (scored_future,)),
        )
        base = self._base("published", partial)
        prior = self._prior(partial)
        plain = build_release(
            partial,
            self.root / "plain",
            release_id="plain",
            phase="week",
            target_week=1,
            timestamp=STAMP,
            published_site=base,
            previous_final=prior,
        )
        scored = build_release(
            scored_snapshot,
            self.root / "scored",
            release_id="scored",
            phase="week",
            target_week=1,
            timestamp=STAMP,
            published_site=base,
            previous_final=prior,
        )
        spread_relative = "cfb/years/2025/spread/2025_W2_FBS_spread.html"

        self.assertEqual(
            (plain.site / spread_relative).read_bytes(),
            (scored.site / spread_relative).read_bytes(),
        )
        self.assertFalse((scored.site / "cfb/years/2025/rankings/2025_W2_FBS_cors.html").exists())
        self.assertFalse((scored.site / "cfb/years/2025/data/results/weekly_results/2025_W2_FBS_results.html").exists())


if __name__ == "__main__":
    unittest.main()
