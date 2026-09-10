"""Independent adversarial checks for the confirmed P0 acceptance gaps.

These tests deliberately reseal candidate manifests after changing one owned
artifact.  They are verification-owned and should fail until the production
contracts reject the tampering.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
import tempfile
import unittest

from bs4 import BeautifulSoup

from cfb.ranking_engine import (
    PreviousFinal,
    RankingContractError,
    ranking_for_week,
    season_rankings,
    week_ranking,
)
from cfb.recovery import run_p0_backfill
from cfb.release import build_release, validate_release
from cfb.request_meter import RequestMeter
from cfb.season_snapshot import SeasonSnapshot, SeasonSnapshotService, _checksum
from cfb.season_source import FixtureSeasonSource, SourceGame, SourceTeam
from cfb.snapshot_cache import SnapshotCache


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "cfbd"
STAMP = "2026-01-01T00:00:00+00:00"


def _reseal(candidate) -> None:
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    for relative in manifest["artifact_checksums"]:
        manifest["artifact_checksums"][relative] = hashlib.sha256(
            (candidate.site / relative).read_bytes()
        ).hexdigest()
    manifest.pop("manifest_checksum", None)
    unsigned = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ) + "\n"
    manifest["manifest_checksum"] = hashlib.sha256(unsigned.encode("utf-8")).hexdigest()
    candidate.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _set_first_cell(path: Path, column: str, value: str) -> None:
    document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
    index = headers.index(column)
    document.select_one("tbody tr").find_all("td")[index].string = value
    path.write_text(str(document), encoding="utf-8")


def _set_ranking_values(path: Path, rows: list[dict[str, object]]) -> None:
    document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
    indexes = {name: headers.index(name) for name in ("school", "cors", "mov", "sos", "expected_wins", "wins_vs_expected")}
    expected = {str(row["school"]): row for row in rows}
    for tr in document.select("tbody tr"):
        cells = tr.find_all("td")
        row = expected[str(cells[indexes["school"]].get_text(strip=True))]
        for field in ("cors", "mov", "sos", "expected_wins", "wins_vs_expected"):
            cells[indexes[field]].string = str(row[field])
    path.write_text(str(document), encoding="utf-8")


def _base(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("legacy published bytes", encoding="utf-8")
    return base


def _add_prior_final(base: Path, year: int, values: dict[str, float]) -> None:
    path = base / f"cfb/years/{year}/rankings/{year}_FINAL_FBS_cors.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(
        f"<tr><td>{school}</td><td>{cors}</td></tr>"
        for school, cors in values.items()
    )
    path.write_text(
        "<html><body><table><thead><tr><th>school</th><th>cors</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></body></html>",
        encoding="utf-8",
    )


def _service(root: Path) -> SeasonSnapshotService:
    return SeasonSnapshotService(
        FixtureSeasonSource(FIXTURES),
        SnapshotCache(root / "cache"),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _prior(snapshot: SeasonSnapshot, value_offset: float = 0.0) -> PreviousFinal:
    return PreviousFinal(
        {
            team.school: float(index) + value_offset
            for index, team in enumerate(snapshot.teams)
        },
        {team.school: 0.0 for team in snapshot.teams},
    )


def _preseason_snapshot() -> SeasonSnapshot:
    teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
    games = (SourceGame(0, "Alpha", "FBS", None, "Beta", "FBS", None, False),)
    metadata = MappingProxyType(
        {
            "schema_version": 2,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2025,
            "teams_fetched_at": STAMP,
            "games_fetched_at": STAMP,
            "complete_through_week": -1,
        }
    )
    return SeasonSnapshot(
        "cfb", "FBS", 2025, teams, games, metadata, _checksum(metadata, teams, games)
    )


class _P0Source:
    """Metered offline source for the complete three-release chain."""

    def __init__(self, meter: RequestMeter, category: str):
        self.meter = meter
        self.category = category

    def fetch_teams(self, year, classification, *, cache_decision):
        return self.meter.execute(
            purpose=self.category,
            endpoint="teams",
            season=year,
            cache_decision=cache_decision,
            transport=lambda: (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test")),
        )

    def fetch_games(self, year, classification, *, cache_decision):
        def transport():
            if year == 2026:
                return (SourceGame(0, "Alpha", "FBS", None, "Beta", "FBS", None, False),)
            return (
                SourceGame(0, "Alpha", "FBS", 21, "Beta", "FBS", 14, False),
                SourceGame(1, "Beta", "FBS", 17, "Alpha", "FBS", 14, False),
            )

        return self.meter.execute(
            purpose=self.category,
            endpoint="games",
            season=year,
            cache_decision=cache_decision,
            transport=transport,
        )


class P0AdversarialContractTests(unittest.TestCase):
    def test_ci_materializes_base_sha_before_recovery_validation(self):
        source = (REPO_ROOT / ".github/workflows/firebase-hosting-publish.yml").read_text(
            encoding="utf-8"
        )
        validation = source.split("Validate reviewed website Release", 1)[1].split(
            "Package the validated site once", 1
        )[0]
        self.assertIn("--published-site", validation)
        self.assertRegex(validation, r"git (archive|worktree|show).*BASE_SHA")

    def test_resealed_candidate_with_wrong_finite_prior_final_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root)
            prior_path = base / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
            prior_path.parent.mkdir(parents=True)
            prior_path.write_text(
                "<html><body><table><thead><tr><th>school</th><th>cors</th></tr></thead>"
                "<tbody><tr><td>Alpha State</td><td>100</td></tr>"
                "<tr><td>Beta Tech</td><td>90</td></tr></tbody></table></body></html>",
                encoding="utf-8",
            )
            snapshot = _service(root).get(2025, "FBS")
            candidate = build_release(
                snapshot,
                root / "candidate",
                release_id="candidate",
                phase="week",
                target_week=1,
                published_site=base,
                timestamp=STAMP,
            )
            wrong = PreviousFinal({"Alpha State": 1.0, "Beta Tech": 2.0}, {})
            ranking_values = season_rankings(snapshot, 1, wrong)
            for week in (0, 1):
                _set_ranking_values(
                    candidate.site
                    / f"cfb/years/2025/rankings/2025_W{week}_FBS_cors.html",
                    ranking_values[week],
                )
            manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
            manifest["previous_final"] = {"cors": dict(wrong.cors), "wins_vs_expected": {}}
            candidate.manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            _reseal(candidate)
            self.assertFalse(validate_release(candidate, published_site=base).valid)

    def test_resealed_release_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = self._build_week_candidate(root)
            manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
            metadata = json.loads(candidate.metadata_path.read_text(encoding="utf-8"))
            manifest["release_id"] = "forged-release-id"
            metadata["release_id"] = "forged-release-id"
            candidate.metadata_path.write_text(
                json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            candidate.manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            _reseal(candidate)
            self.assertFalse(
                validate_release(candidate, published_site=candidate.base_site).valid
            )

    def test_resealed_nan_w0_spread_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = _preseason_snapshot()
            base = _base(root)
            _add_prior_final(base, 2024, {"Alpha": 10.0, "Beta": 9.0})
            candidate = build_release(
                snapshot,
                root / "candidate",
                release_id="candidate",
                phase="preseason",
                previous_final=PreviousFinal({"Alpha": 10.0, "Beta": 9.0}, {}),
                published_site=base,
                timestamp=STAMP,
            )
            path = candidate.site / "cfb/years/2025/spread/2025_W0_FBS_spread.html"
            _set_first_cell(path, "spread_value", "nan")
            _reseal(candidate)
            self.assertFalse(
                validate_release(candidate, published_site=candidate.base_site).valid
            )

    def _build_week_candidate(self, root: Path):
        snapshot = _service(root).get(2025, "FBS")
        base = _base(root)
        _add_prior_final(base, 2024, {"Alpha State": 0.0, "Beta Tech": 1.0})
        return build_release(
            snapshot,
            root / "candidate",
            release_id="candidate",
            phase="week",
            target_week=1,
            previous_final=_prior(snapshot),
            published_site=base,
            timestamp=STAMP,
        )

    def test_resealed_nan_w1_spread_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = self._build_week_candidate(root)
            path = candidate.site / "cfb/years/2025/spread/2025_W1_FBS_spread_results.html"
            _set_first_cell(path, "spread_value", "nan")
            _reseal(candidate)
            self.assertFalse(validate_release(candidate, published_site=candidate.base_site).valid)

    def test_resealed_wrong_result_score_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = self._build_week_candidate(root)
            path = candidate.site / "cfb/years/2025/data/results/weekly_results/2025_W1_FBS_results.html"
            _set_first_cell(path, "home_score", "999")
            _reseal(candidate)
            self.assertFalse(validate_release(candidate, published_site=candidate.base_site).valid)

    def test_resealed_nan_record_win_pct_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = self._build_week_candidate(root)
            path = candidate.site / "cfb/years/2025/data/records/2025_W1_FBS_records.html"
            _set_first_cell(path, "win_pct", "nan")
            _reseal(candidate)
            self.assertFalse(validate_release(candidate, published_site=candidate.base_site).valid)

    def test_resealed_wrong_current_champion_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = _service(root).get(2024, "FBS")
            base = _base(root)
            _add_prior_final(base, 2023, {"Old Alpha": 0.0, "Old Beta": 1.0})
            candidate = build_release(
                snapshot,
                root / "candidate",
                release_id="candidate",
                phase="final",
                previous_final=_prior(snapshot),
                published_site=base,
                timestamp=STAMP,
            )
            path = candidate.site / "cfb/history/nc_FBS_CFB_output.html"
            _set_first_cell(path, "school", "Wrong School")
            _reseal(candidate)
            self.assertFalse(
                validate_release(candidate, published_site=candidate.base_site).valid
            )

    def test_post_genesis_week_zero_requires_a_prior_final_at_public_api(self):
        teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
        games = (SourceGame(0, "Alpha", "FBS", 21, "Beta", "FBS", 14, False),)
        snapshot = SeasonSnapshot(
            "cfb",
            "FBS",
            2025,
            teams,
            games,
            MappingProxyType({"complete_through_week": 0}),
            "synthetic",
        )
        with self.assertRaises(RankingContractError):
            ranking_for_week(snapshot, 0, {"Alpha": 10.0, "Beta": 9.0})
        with self.assertRaises(RankingContractError):
            week_ranking(snapshot, 0, previous_ranking={"Alpha": 10.0, "Beta": 9.0})

    def test_final_chain_validates_against_original_published_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            meter = RequestMeter(
                cache / "cfbd_requests.sqlite3",
                clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            scheduled = SeasonSnapshotService(
                _P0Source(meter, "scheduled"),
                SnapshotCache(cache / "snapshots"),
                clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            historical = SeasonSnapshotService(
                _P0Source(meter, "historical"),
                SnapshotCache(cache / "snapshots"),
                clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            published = _base(root)
            prior_path = published / "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html"
            prior_path.parent.mkdir(parents=True)
            prior_path.write_text(
                "<html><body><table><thead><tr><th>school</th><th>cors</th></tr></thead>"
                "<tbody><tr><td>Alpha</td><td>10</td></tr><tr><td>Beta</td><td>9</td></tr>"
                "</tbody></table></body></html>",
                encoding="utf-8",
            )
            result = run_p0_backfill(
                scheduled_service=scheduled,
                historical_service=historical,
                published_site=published,
                output_root=root / "releases",
                timestamp=STAMP,
            )
            self.assertTrue(
                validate_release(result.releases[-1], published_site=published).valid
            )


if __name__ == "__main__":
    unittest.main()
