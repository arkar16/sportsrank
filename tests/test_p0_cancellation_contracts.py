"""Offline cancellation contracts for the historical Season Snapshot seam."""

from datetime import datetime, timezone
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

from cfb.ranking_engine import PreviousFinal, RankingContractError, final_ranking, season_is_complete
from cfb.game_dispositions import APP_STATE_CANCELLATION_SOURCES, apply_cancellation_registry
from cfb.recovery import P0_EXPECTED_REQUESTS, run_p0_backfill
from cfb.request_meter import RequestMeter
from cfb.season_snapshot import SeasonSnapshot, SeasonSnapshotService
from cfb.season_source import SourceGame, SourceTeam, normalize_game
from cfb.snapshot_cache import SnapshotCache


def _teams() -> tuple[SourceTeam, ...]:
    return (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))


def _game(**overrides) -> SourceGame:
    value = {
        "week": 1,
        "home_team": "Alpha",
        "home_classification": "fbs",
        "home_points": 21,
        "away_team": "Beta",
        "away_classification": "fbs",
        "away_points": 14,
        "neutral_site": False,
    }
    value.update(overrides)
    return normalize_game(value)


def _cancelled_game() -> SourceGame:
    return normalize_game(
        {
            "id": "cancelled-alpha-beta",
            "week": 2,
            "home_team": "Alpha",
            "home_classification": "fbs",
            "home_points": None,
            "away_team": "Beta",
            "away_classification": "fbs",
            "away_points": None,
            "neutral_site": False,
            "start_date": "2024-10-19T00:00:00Z",
            "completed": False,
            "notes": "Canceled by the authoritative provider",
            "disposition": "canceled",
        }
    )


def _app_state_liberty_game() -> SourceGame:
    return normalize_game(
        {
            "id": "app-state-liberty-2024",
            "week": 5,
            "home_team": "App State",
            "home_classification": "fbs",
            "home_points": None,
            "away_team": "Liberty",
            "away_classification": "fbs",
            "away_points": None,
            "neutral_site": False,
            "start_date": "2024-09-28T00:00:00Z",
            "completed": False,
        }
    )


def _snapshot(games: tuple[SourceGame, ...], complete_through_week: int) -> SeasonSnapshot:
    return SeasonSnapshot(
        "cfb",
        "FBS",
        2024,
        _teams(),
        games,
        MappingProxyType({"complete_through_week": complete_through_week}),
        "offline-fixture-checksum",
    )


class _NoCallSource:
    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def fetch_teams(self, year, classification, *, cache_decision):
        self.calls.append(("teams", int(year)))
        raise AssertionError("cache resume must not fetch teams")

    def fetch_games(self, year, classification, *, cache_decision):
        self.calls.append(("games", int(year)))
        raise AssertionError("cache resume must not fetch games")


def _seed_resume_cache(root: Path) -> tuple[RequestMeter, SnapshotCache]:
    """Build the smallest valid six-call resume state entirely in temp storage."""

    cache = SnapshotCache(root / "snapshots")
    seed_service = SeasonSnapshotService(
        _NoCallSource(),
        cache,
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    games = (asdict(_game(week=0)),)
    teams = [asdict(team) for team in _teams()]
    for year in (2024, 2025, 2026):
        state = seed_service._empty_state(year, "FBS")
        state["teams"] = teams
        state["games"] = games
        seed_service._persist(state)

    meter = RequestMeter(
        root / "cfbd_requests.sqlite3",
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    for request in P0_EXPECTED_REQUESTS:
        meter.execute(
            purpose=request.category,
            endpoint=request.endpoint,
            season=request.season,
            cache_decision=request.cache_decision,
            transport=lambda: None,
        )
    return meter, cache


def _published_site(root: Path) -> Path:
    site = root / "published"
    final = site / "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html"
    final.parent.mkdir(parents=True)
    (site / "index.html").write_text("portable published base", encoding="utf-8")
    final.write_text(
        "<table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
        "<tbody><tr><td>Alpha</td><td>1</td><td>0</td></tr>"
        "<tr><td>Beta</td><td>2</td><td>0</td></tr></tbody></table>",
        encoding="utf-8",
    )
    return site


class CancellationContractTests(unittest.TestCase):
    def test_explicit_cancellation_is_complete_and_final_capable(self):
        snapshot = _snapshot((_game(), _cancelled_game()), complete_through_week=2)

        self.assertTrue(season_is_complete(snapshot))
        ranking = final_ranking(
            snapshot,
            PreviousFinal({"Alpha": 10.0, "Beta": 9.0}, {}),
        )
        self.assertEqual({row["school"] for row in ranking}, {"Alpha", "Beta"})

    def test_generic_null_score_game_remains_incomplete(self):
        snapshot = _snapshot(
            (
                _game(),
                _game(week=2, home_points=None, away_points=None),
            ),
            complete_through_week=1,
        )

        self.assertFalse(season_is_complete(snapshot))
        with self.assertRaises(RankingContractError):
            final_ranking(snapshot, PreviousFinal({"Alpha": 10.0, "Beta": 9.0}, {}))

    def test_source_game_metadata_or_canonical_disposition_survives_cache_roundtrip(self):
        raw = {
            "id": "provider-123",
            "week": 2,
            "home_team": "Alpha",
            "home_classification": "fbs",
            "home_points": None,
            "away_team": "Beta",
            "away_classification": "fbs",
            "away_points": None,
            "neutral_site": False,
            "start_date": "2024-10-19T00:00:00Z",
            "completed": False,
            "notes": "Canceled by the authoritative provider",
            "disposition": "canceled",
        }

        class Source:
            def fetch_teams(self, year, classification, *, cache_decision):
                return _teams()

            def fetch_games(self, year, classification, *, cache_decision):
                return (normalize_game(raw),)

        with tempfile.TemporaryDirectory() as directory:
            service = SeasonSnapshotService(
                Source(),
                SnapshotCache(Path(directory) / "cache"),
                clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            first = service.get(2024, "FBS")
            persisted = json.loads(service.cache.path_for(2024, "FBS").read_text())
            reloaded = service.load_cached(2024, "FBS")

        game = first.games[0]
        if getattr(game, "disposition", None) == "canceled":
            self.assertEqual(persisted["games"][0].get("disposition"), "canceled")
            self.assertEqual(getattr(reloaded.games[0], "disposition", None), "canceled")
        else:
            for name, expected in (
                ("provider_id", "provider-123"),
                ("date", "2024-10-19T00:00:00Z"),
                ("completed", False),
                ("notes", "Canceled by the authoritative provider"),
            ):
                self.assertEqual(getattr(game, name, None), expected)
                self.assertEqual(persisted["games"][0].get(name), expected)
                self.assertEqual(getattr(reloaded.games[0], name, None), expected)

    def test_known_app_state_liberty_record_gets_cited_cancellation_only(self):
        known = apply_cancellation_registry(
            2024, "FBS", (_app_state_liberty_game(),)
        )[0]
        self.assertEqual(getattr(known, "disposition", None), "canceled")
        citation = " ".join(
            str(getattr(known, name, ""))
            for name in ("disposition_source", "source", "notes", "provider_notes")
        ).lower()
        self.assertIn(APP_STATE_CANCELLATION_SOURCES[0].lower(), citation)
        self.assertRegex(citation, r"cfbd|college football|cancel")

        unrelated = apply_cancellation_registry(
            2024,
            "FBS",
            (_game(week=5, home_points=None, away_points=None),),
        )[0]
        self.assertNotEqual(getattr(unrelated, "disposition", None), "canceled")

    def test_six_call_cache_resumes_without_transport_and_builds_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meter, cache = _seed_resume_cache(root)
            before = meter.audit_records()
            scheduled_source = _NoCallSource()
            historical_source = _NoCallSource()
            scheduled = SeasonSnapshotService(scheduled_source, cache)
            historical = SeasonSnapshotService(historical_source, cache)

            result = run_p0_backfill(
                scheduled_service=scheduled,
                historical_service=historical,
                meter=meter,
                published_site=_published_site(root),
                output_root=root / "releases",
                timestamp="2026-01-01T00:00:00+00:00",
                code_revision="cancellation-regression",
            )
            self.assertTrue(result.releases[-1].site.is_dir())

            self.assertEqual(len(before), 6)
            self.assertEqual(meter.audit_records(), before)
            self.assertEqual(scheduled_source.calls, [])
            self.assertEqual(historical_source.calls, [])
            self.assertEqual(len(result.releases), 3)


if __name__ == "__main__":
    unittest.main()
