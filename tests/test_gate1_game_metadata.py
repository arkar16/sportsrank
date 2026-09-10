"""Gate 1 regressions for provider game metadata and Week 0 mapping."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb.request_meter import RequestMeter
from cfb.season_snapshot import SeasonSnapshotService, SnapshotUnavailable
from cfb.season_source import (
    FixtureSeasonSource,
    ProductionSeasonSource,
    SourceTeam,
    normalize_game,
)
from cfb.snapshot_cache import SnapshotCache
from cfb.week_calendar import WEEK_ONE_BOUNDARIES, canonical_week


PROVIDER_WEEK_ONE = {
    "id": 401001,
    "week": 1,
    "seasonType": "regular",
    "startDate": "2024-08-24T12:00:00Z",
    "completed": True,
    "neutralSite": False,
    "homeTeam": "Georgia Tech",
    "homeClassification": "fbs",
    "homePoints": 24,
    "awayTeam": "Florida State",
    "awayClassification": "fbs",
    "awayPoints": 21,
}


@contextmanager
def _fake_api_client():
    yield object()


class _GamesApi:
    def __init__(self, _api_client):
        pass

    def get_games(self, *, year, classification):
        assert year == 2024
        assert str(classification).lower().endswith("fbs")
        return [PROVIDER_WEEK_ONE]


class _SourceWithProductionGames:
    def __init__(self, production):
        self.production = production

    def fetch_teams(self, year, classification, *, cache_decision):
        return (SourceTeam("Georgia Tech", "ACC"), SourceTeam("Florida State", "ACC"))

    def fetch_games(self, year, classification, *, cache_decision):
        return self.production.fetch_games(
            year, classification, cache_decision=cache_decision
        )


class Gate1GameMetadataTests(unittest.TestCase):
    def test_production_metadata_and_provider_week_survive_snapshot_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meter = RequestMeter(
                root / "requests.sqlite3",
                clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc),
            )
            production = ProductionSeasonSource(meter, category="historical")
            service = SeasonSnapshotService(
                _SourceWithProductionGames(production),
                SnapshotCache(root / "snapshots"),
                clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc),
            )

            with patch("cfb.season_source.create_api_client", _fake_api_client), patch(
                "cfb.season_source.cfbd.GamesApi", _GamesApi
            ):
                snapshot = service.get(2024, "FBS")

            game = snapshot.games[0]
            self.assertEqual(game.week, 0)
            self.assertEqual(game.provider_week, 1)
            self.assertEqual(game.provider_id, "401001")
            self.assertEqual(game.date, "2024-08-24T12:00:00Z")
            self.assertTrue(game.completed)
            self.assertEqual((game.home_points, game.away_points), (24, 21))

            persisted = json.loads(
                service.cache.path_for(2024, "FBS").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["games"][0]["week"], 0)
            self.assertEqual(persisted["games"][0]["provider_week"], 1)
            self.assertEqual(persisted["games"][0]["provider_id"], "401001")
            reloaded = service.load_cached(2024, "FBS").games[0]
            self.assertEqual(reloaded, game)
            self.assertEqual(
                [(row["endpoint"], row["season"], row["outcome"])
                 for row in meter.audit_records()],
                [("games", 2024, "succeeded")],
            )

    def test_fixture_normalization_does_not_apply_provider_week_mapping(self):
        game = normalize_game(
            {
                "week": 0,
                "home_team": "Georgia Tech",
                "home_classification": "fbs",
                "home_points": 24,
                "away_team": "Florida State",
                "away_classification": "fbs",
                "away_points": 21,
                "neutral_site": False,
                "start_date": "2024-08-24T12:00:00Z",
            }
        )
        self.assertEqual(game.week, 0)
        self.assertIsNone(game.provider_week)

    def test_explicit_calendar_boundaries_are_inclusive_and_year_scoped(self):
        for season, boundary in WEEK_ONE_BOUNDARIES.items():
            before = boundary - timedelta(minutes=1)
            self.assertEqual(canonical_week(season, 1, before.isoformat()), 0)
            self.assertEqual(canonical_week(season, 1, boundary.isoformat()), 1)
            self.assertEqual(canonical_week(season, 2, None), 2)
        with self.assertRaisesRegex(ValueError, "No explicit provider Week 1 boundary"):
            canonical_week(2027, 1, "2027-08-20T12:00:00Z")

    def test_provider_week_one_without_valid_date_fails_closed_before_snapshot(self):
        malformed = dict(PROVIDER_WEEK_ONE)
        malformed.pop("startDate")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meter = RequestMeter(root / "requests.sqlite3")
            production = ProductionSeasonSource(meter, category="historical")
            with patch("cfb.season_source.create_api_client", _fake_api_client), patch(
                "cfb.season_source.cfbd.GamesApi",
                type(
                    "MalformedGamesApi",
                    (),
                    {
                        "__init__": lambda self, _client: None,
                        "get_games": lambda self, **_kwargs: [malformed],
                    },
                ),
            ):
                with self.assertRaisesRegex(Exception, "CFBD games request"):
                    production.fetch_games(2024, "FBS", cache_decision="refresh")
            self.assertEqual(len(meter.audit_records()), 1)
            self.assertEqual(meter.audit_records()[0]["outcome"], "failed")

    def test_unsupported_production_season_fails_before_http_or_meter(self):
        with tempfile.TemporaryDirectory() as directory:
            meter = RequestMeter(Path(directory) / "requests.sqlite3")
            production = ProductionSeasonSource(meter)
            with patch("cfb.season_source.create_api_client") as create_client:
                with self.assertRaisesRegex(ValueError, "No explicit provider Week 1 boundary"):
                    production.fetch_games(2027, "FBS", cache_decision="miss")
                with self.assertRaisesRegex(ValueError, "No explicit provider Week 1 boundary"):
                    production.fetch_teams(2027, "FBS", cache_decision="miss")
            create_client.assert_not_called()
            self.assertEqual(meter.audit_records(), [])

    def test_old_game_cache_requires_explicit_refresh_in_production(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = SnapshotCache(root / "snapshots")
            fixture_service = SeasonSnapshotService(
                FixtureSeasonSource(Path(__file__).parent / "fixtures" / "cfbd"),
                cache,
            )
            # Seed a legacy payload through the normal fixture adapter so the
            # compatibility digest is genuine and contains no provider fields.
            fixture_service.get(2025, "FBS")

            meter = RequestMeter(root / "requests.sqlite3")
            production_service = SeasonSnapshotService(
                ProductionSeasonSource(meter), cache
            )
            with self.assertRaisesRegex(
                SnapshotUnavailable, "explicit games refresh required"
            ):
                production_service.get(2025, "FBS")
            with self.assertRaisesRegex(
                SnapshotUnavailable, "explicit games refresh required"
            ):
                production_service.load_cached(2025, "FBS")
            self.assertEqual(meter.audit_records(), [])

    def test_cached_provider_metadata_rederives_and_seals_canonical_week(self):
        valid = normalize_game(PROVIDER_WEEK_ONE, season=2024, from_provider=True)
        invalid = replace(valid, week=1)

        class Source:
            def fetch_teams(self, year, classification, *, cache_decision):
                return (SourceTeam("Georgia Tech", "ACC"), SourceTeam("Florida State", "ACC"))

            def fetch_games(self, year, classification, *, cache_decision):
                return (invalid,)

        with tempfile.TemporaryDirectory() as directory:
            service = SeasonSnapshotService(
                Source(), SnapshotCache(Path(directory) / "snapshots")
            )
            with self.assertRaisesRegex(ValueError, "canonical week verification"):
                service.get(2024, "FBS")


if __name__ == "__main__":
    unittest.main()
