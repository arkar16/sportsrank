"""Independent offline regressions for the configured 2026 postseason window.

These tests cross the provider adapter and cache reload seams.  Schedule identity
and playoff labels are deliberately varied while canonical week derives only from
positive phase plus the New York local date and the code-owned window.
"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cfb import week_calendar
from cfb.ranking_engine import PreviousFinal, records_for_week
from cfb.release import (
    _snapshot_checksum,
    _snapshot_from_payload,
    build_release,
    validate_release,
)
from cfb.request_meter import RequestMeter
from cfb.season_snapshot import SeasonSnapshotService, _checksum
from cfb.season_source import ProductionSeasonSource, SourceTeam, normalize_game
from cfb.snapshot_cache import SnapshotCache

EASTERN = ZoneInfo("America/New_York")
WEEK_ONE_2026 = date(2026, 8, 31)
WINDOW_START_2026 = date(2026, 12, 12)  # Bowl-season policy includes the FCS Celebration Bowl.
FIRST_FBS_BOWL_2026 = date(2026, 12, 15)
WINDOW_END_2026 = date(2027, 1, 25)


def _utc_for_local(day: date, hour: int = 12, minute: int = 0) -> str:
    return datetime.combine(day, time(hour, minute), tzinfo=EASTERN).astimezone(timezone.utc).isoformat()


def _provider_game(
    *,
    game_id: str = "202600001",
    home: str = "Alpha",
    away: str = "Beta",
    start: str | None = None,
    playoff: dict | None = None,
    season_type: str | None = "postseason",
    week: int = 1,
) -> dict:
    return {
        "id": game_id,
        "week": week,
        "startDate": start or _utc_for_local(FIRST_FBS_BOWL_2026),
        "seasonType": season_type,
        "homeTeam": home,
        "awayTeam": away,
        "homeClassification": "fbs",
        "awayClassification": "fbs",
        "homePoints": 24,
        "awayPoints": 17,
        "completed": True,
        "neutralSite": True,
        "notes": "offline schedule fixture",
        "playoff": playoff,
    }


@contextmanager
def _fake_api_client():
    yield object()


class _TeamsApi:
    def __init__(self, _client):
        pass

    def get_fbs_teams(self, *, year):
        assert year == 2026
        return [
            {"school": "Alpha", "conference": "Test"},
            {"school": "Beta", "conference": "Test"},
        ]


class _GamesApi:
    payloads: list[dict] = []

    def __init__(self, _client):
        pass

    def get_games(self, *, year, classification):
        assert year == 2026
        assert str(classification).lower().endswith("fbs")
        return list(self.payloads)


class _ProductionBackedSource:
    def __init__(self, production):
        self.production = production

    def fetch_teams(self, year, classification, *, cache_decision):
        return self.production.fetch_teams(
            year, classification, cache_decision=cache_decision
        )

    def fetch_games(self, year, classification, *, cache_decision):
        return self.production.fetch_games(
            year, classification, cache_decision=cache_decision
        )


def _minimal_published_site(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("published", encoding="utf-8")
    final = base / "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html"
    final.parent.mkdir(parents=True)
    final.write_text(
        "<table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
        "<tbody><tr><td>Alpha</td><td>10</td><td>0</td></tr>"
        "<tr><td>Beta</td><td>9</td><td>0</td></tr></tbody></table>",
        encoding="utf-8",
    )
    return base


def _reseal_provider_phase(site: Path, *, provider_season_type: str | None) -> None:
    snapshot_path = site / "cfb/years/2026/data/snapshot.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if provider_season_type is None:
        payload["games"][0].pop("provider_season_type", None)
    else:
        payload["games"][0]["provider_season_type"] = provider_season_type
    snapshot = _snapshot_from_payload(payload)
    metadata = dict(snapshot.metadata)
    state = {
        "schema_version": metadata.get("schema_version", 4),
        "sport": snapshot.sport,
        "classification": snapshot.classification,
        "year": snapshot.year,
        "teams_fetched_at": metadata.get("teams_fetched_at"),
        "games_fetched_at": metadata.get("games_fetched_at"),
        "complete_through_week": metadata.get("complete_through_week"),
        "teams": [asdict(team) for team in snapshot.teams],
        "games": payload["games"],
        "calendar_provenance": metadata.get("calendar_provenance"),
        "correction_registry_provenance": metadata.get("correction_registry_provenance"),
        "migration_provenance": metadata.get("migration_provenance"),
    }
    # Recompute the release checksum after tampering so validators cannot pass
    # or fail merely on a stale digest.  The metadata validator must reject the
    # contradictory/missing raw phase itself.
    normalized = tuple(normalize_game(item) for item in payload["games"])
    teams = tuple(SourceTeam(**item) for item in state["teams"])
    state["games"] = [asdict(game) for game in normalized]
    new_checksum = _snapshot_checksum(state, teams, normalized)
    payload["checksum"] = new_checksum
    snapshot_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest_path = site / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runs = manifest["runs"]
    old_archive = runs[-1]["snapshot_archive_path"]
    old_checksum = runs[-1]["snapshot_archive_checksum"]
    new_archive = f"cfb/years/2026/data/snapshots/{new_checksum}.json"
    archive_path = site / new_archive
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    old_archive_path = site / old_archive
    if old_archive_path.exists() and old_archive_path != archive_path:
        old_archive_path.unlink()
    for document in (manifest,):
        document["snapshot_checksum"] = new_checksum
        document["source_snapshot"] = new_checksum
        document["owned_artifacts"] = [new_archive if x == old_archive else x for x in document["owned_artifacts"]]
        document["required_artifacts"] = [new_archive if x == old_archive else x for x in document["required_artifacts"]]
        document["artifact_checksums"].pop(old_archive, None)
        document["artifact_checksums"][new_archive] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        document["artifact_checksums"]["cfb/years/2026/data/snapshot.json"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        run = document["runs"][-1]
        run["snapshot_archive_path"] = new_archive
        run["snapshot_archive_checksum"] = new_checksum
        run["source_snapshot"] = new_checksum
        document.pop("manifest_checksum", None)
        document["manifest_checksum"] = hashlib.sha256(
            (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    release_path = site / "release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["snapshot_checksum"] = new_checksum
    release["source_snapshot"] = new_checksum
    release["owned_artifacts"] = manifest["owned_artifacts"]
    release["required_artifacts"] = manifest["required_artifacts"]
    release["runs"] = manifest["runs"]
    release_path.write_text(
        json.dumps(release, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class PostseasonWindow2026IndependentTests(unittest.TestCase):
    def test_code_owned_window_is_inclusive_and_has_official_fbs_start(self):
        window = week_calendar.POSTSEASON_WINDOWS[2026]
        self.assertEqual(window.start_date, WINDOW_START_2026)
        self.assertEqual(window.end_date, WINDOW_END_2026)
        # The wider policy starts Dec 12; the first FBS bowl is Dec 15.
        self.assertEqual(FIRST_FBS_BOWL_2026, date(2026, 12, 15))
        self.assertEqual(
            week_calendar.canonical_postseason_week(2026, _utc_for_local(WINDOW_START_2026)),
            15,
        )
        self.assertEqual(
            week_calendar.canonical_postseason_week(2026, _utc_for_local(FIRST_FBS_BOWL_2026)),
            16,
        )
        self.assertEqual(
            week_calendar.canonical_postseason_week(2026, _utc_for_local(WINDOW_END_2026)),
            22,
        )

    def test_cfplayoff_dates_follow_independent_week_one_lattice(self):
        # Expected weeks are independently derived from the existing local Week 1
        # boundary; no expected value is obtained from the production helper.
        cases = {
            date(2026, 12, 18): 16,
            date(2026, 12, 19): 16,
            date(2026, 12, 30): 18,
            date(2026, 12, 31): 18,
            date(2027, 1, 1): 18,
            date(2027, 1, 14): 20,
            date(2027, 1, 15): 20,
            date(2027, 1, 25): 22,
        }
        for local_day, expected in cases.items():
            with self.subTest(local_day=local_day):
                independent = 1 + (local_day - WEEK_ONE_2026).days // 7
                self.assertEqual(independent, expected)
                self.assertEqual(
                    week_calendar.canonical_postseason_week(
                        2026, _utc_for_local(local_day)
                    ),
                    expected,
                )

    def test_window_boundaries_fail_closed_in_new_york_local_date(self):
        before = _utc_for_local(date(2026, 12, 11), 23, 59)
        after = _utc_for_local(date(2027, 1, 26), 0, 1)
        for value in (before, after):
            with self.subTest(value=value), self.assertRaises(ValueError):
                week_calendar.canonical_postseason_week(2026, value)
        for value in (
            _utc_for_local(WINDOW_START_2026, 0, 1),
            _utc_for_local(WINDOW_END_2026, 23, 59),
        ):
            with self.subTest(value=value):
                week_calendar.canonical_postseason_week(2026, value)

    def test_provider_identity_and_playoff_labels_do_not_change_week(self):
        start = _utc_for_local(date(2026, 12, 30), 23, 59)
        variants = (
            _provider_game(
                game_id="401900001",
                home="Alpha",
                away="Beta",
                start=start,
                playoff={"round": "Quarterfinal", "bracketSlot": "QF-A", "homeSeed": 1},
            ),
            _provider_game(
                game_id="schedule-placeholder-qf",
                home="TBD",
                away="TBD",
                start=start,
                playoff={"round": "unknown", "bracketSlot": "TBD", "homeSeed": None},
            ),
            _provider_game(
                game_id="different-provider-id",
                home="Winner First Round Game 1",
                away="Winner First Round Game 2",
                start=start,
                playoff={"roundName": "Quarterfinal", "format": "bracket"},
            ),
        )
        normalized = [normalize_game(item, season=2026, from_provider=True) for item in variants]
        self.assertEqual({game.week for game in normalized}, {18})
        self.assertEqual({game.phase for game in normalized}, {"postseason"})
        self.assertEqual({game.phase_source for game in normalized}, {"provider"})
        self.assertEqual({game.provider_week for game in normalized}, {1})

    def test_production_adapter_and_cache_reload_keep_unknown_schedule_unranked(self):
        start = _utc_for_local(date(2027, 1, 1), 0, 1)
        _GamesApi.payloads = [
            _provider_game(
                game_id="schedule-placeholder-qf",
                home="TBD",
                away="TBD",
                start=start,
                playoff={"round": "Quarterfinal", "bracketSlot": "QF-A"},
            ),
            _provider_game(
                game_id="schedule-known-qf",
                home="Alpha",
                away="Beta",
                start=start,
                playoff={"round": "Quarterfinal", "bracketSlot": "QF-B"},
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meter = RequestMeter(root / "requests.sqlite3")
            production = ProductionSeasonSource(meter, category="historical")
            source = _ProductionBackedSource(production)
            service = SeasonSnapshotService(source, SnapshotCache(root / "snapshots"))
            with patch("cfb.season_source.create_api_client", _fake_api_client), patch(
                "cfb.season_source.cfbd.TeamsApi", _TeamsApi
            ), patch("cfb.season_source.cfbd.GamesApi", _GamesApi):
                snapshot = service.get(2026, "FBS")
                reloaded = service.load_cached(2026, "FBS")
            self.assertEqual([game.week for game in snapshot.games], [18, 18])
            self.assertEqual(reloaded.games, snapshot.games)
            self.assertEqual(reloaded.metadata["calendar_provenance"]["postseason"]["season"], 2026)
            # Unknown schedule placeholders remain outside the ranking team set;
            # the adapter does not invent rankable teams from matchup text.
            rows = records_for_week(reloaded, 18)
            by_school = {row["school"]: row for row in rows}
            self.assertEqual(set(by_school), {"Alpha", "Beta"})
            self.assertEqual((by_school["Alpha"]["wins"], by_school["Alpha"]["losses"]), (1, 0))
            self.assertEqual((by_school["Beta"]["wins"], by_school["Beta"]["losses"]), (0, 1))

            # A resealed cache must still reject missing or contradictory raw
            # provider phase metadata; this is the negative provenance boundary.
            cache_path = service.cache.path_for(2026, "FBS")
            valid_state = json.loads(cache_path.read_text(encoding="utf-8"))
            for raw_phase in (None, "regular"):
                forged = deepcopy(valid_state)
                if raw_phase is None:
                    forged["games"][0].pop("provider_season_type", None)
                else:
                    forged["games"][0]["provider_season_type"] = raw_phase
                forged_teams = tuple(SourceTeam(**item) for item in forged["teams"])
                forged_games = tuple(normalize_game(item) for item in forged["games"])
                forged["checksum"] = _checksum(forged, forged_teams, forged_games)
                service.cache.save(2026, "FBS", forged)
                with self.subTest(raw_phase=raw_phase), self.assertRaisesRegex(ValueError, "phase|seasonType"):
                    service.load_cached(2026, "FBS")
            service.cache.save(2026, "FBS", valid_state)
            reloaded = service.load_cached(2026, "FBS")

            # The same fresh provider-backed snapshot must pass the public
            # release validator; this catches a cache/release provenance split.
            base = _minimal_published_site(root)
            candidate = build_release(
                reloaded,
                root / "candidate",
                release_id="2026-window",
                target_week=18,
                phase="final",
                previous_final=PreviousFinal(
                    {"Alpha": 10.0, "Beta": 9.0},
                    {"Alpha": 0.0, "Beta": 0.0},
                    year=2025,
                    classification="FBS",
                ),
                published_site=base,
                timestamp="2026-09-09T12:00:00+00:00",
            )
            report = validate_release(candidate, published_site=base)
            self.assertTrue(report.valid, [str(failure) for failure in report.failures])

            _reseal_provider_phase(candidate.site, provider_season_type="regular")
            forged_release = validate_release(candidate, published_site=base)
            self.assertFalse(forged_release.valid)
            self.assertTrue(
                any(failure.code in {"runs.invalid", "snapshot.mapping", "snapshot.checksum"}
                    for failure in forged_release.failures),
                [str(failure) for failure in forged_release.failures],
            )

    def test_unknown_or_missing_phase_is_rejected_at_provider_boundary(self):
        for season_type in (None, "playoffs", "championship"):
            with self.subTest(season_type=season_type), self.assertRaises(ValueError):
                normalize_game(
                    _provider_game(season_type=season_type),
                    season=2026,
                    from_provider=True,
                )


if __name__ == "__main__":
    unittest.main()
