"""Independent calendar and metadata-cache acceptance checks.

Expected weeks use the accepted season dates directly, rather than reading
the production boundary registry. All transport in these tests is local.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb.request_meter import RequestMeter
from cfb.season_snapshot import SeasonSnapshotService, SnapshotUnavailable, _checksum
from cfb.season_source import (
    FixtureSeasonSource,
    ProductionSeasonSource,
    SourceTeam,
    is_completed,
    normalize_game,
)
from cfb.snapshot_cache import SnapshotCache
from cfb.week_calendar import canonical_week


FIXTURES = Path(__file__).parent / "fixtures" / "cfbd"
STAMP = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


def _provider_game(**changes):
    payload = {
        "id": 401635525,
        "week": 1,
        "seasonType": "regular",
        "startDate": "2024-08-24T16:00:00+00:00",
        "homeTeam": "Georgia Tech",
        "awayTeam": "Florida State",
        "homeClassification": "fbs",
        "awayClassification": "fbs",
        "homePoints": 24,
        "awayPoints": 21,
        "completed": True,
        "neutralSite": True,
    }
    return {**payload, **changes}


class _RecordedMetadataSource:
    requires_provider_metadata = True

    def fetch_teams(self, year, classification, *, cache_decision):
        return (SourceTeam("Georgia Tech", "ACC"), SourceTeam("Florida State", "ACC"))

    def fetch_games(self, year, classification, *, cache_decision):
        return (normalize_game(_provider_game(), season=year, from_provider=True),)


class IndependentRealWeekZeroTests(unittest.TestCase):
    def test_known_openers_map_zero_and_regular_opening_week_stays_one(self):
        cases = (
            (2024, "2024-08-24T16:00:00+00:00", "Georgia Tech", "Florida State", 0),
            (2024, "2024-08-29T22:00:00+00:00", "Rutgers", "Howard", 1),
            (2025, "2025-08-23T16:00:00+00:00", "Kansas State", "Iowa State", 0),
            (2025, "2025-08-28T21:30:00+00:00", "South Florida", "Boise State", 1),
            (2026, "2026-08-30T02:00:00+00:00", "UNLV", "Memphis", 0),
            (2026, "2026-09-03T22:00:00+00:00", "Rutgers", "Massachusetts", 1),
        )
        for year, kickoff, home, away, expected in cases:
            with self.subTest(year=year, home=home, away=away):
                raw = _provider_game(startDate=kickoff, homeTeam=home, awayTeam=away)
                game = normalize_game(raw, season=year, from_provider=True)
                self.assertEqual(game.week, expected)
                self.assertEqual(game.provider_week, 1)
                self.assertEqual(game.date, kickoff)
                self.assertEqual(game.provider_id, str(raw["id"]))

    def test_eastern_midnight_boundaries_use_the_same_instant_across_offsets(self):
        for year, day in ((2024, "2024-08-26"), (2025, "2025-08-25"), (2026, "2026-08-31")):
            with self.subTest(year=year):
                self.assertEqual(canonical_week(year, 1, day + "T03:59:59Z"), 0)
                self.assertEqual(canonical_week(year, 1, day + "T04:00:00Z"), 1)
                self.assertEqual(canonical_week(year, 1, day + "T00:00:00-04:00"), 1)
                self.assertEqual(canonical_week(year, 1, day + "T05:00:00+01:00"), 1)
                self.assertEqual(canonical_week(year, 1, day), 1)
                self.assertEqual(canonical_week(year, 2, None), 2)

    def test_missing_opening_dates_and_unsupported_seasons_fail_closed(self):
        for date in (None, "", "not-a-date"):
            with self.subTest(date=date), self.assertRaises(ValueError):
                normalize_game(_provider_game(startDate=date), season=2024, from_provider=True)
        for year in (2023, 2027):
            with self.subTest(year=year), self.assertRaises(ValueError):
                normalize_game(_provider_game(), season=year, from_provider=True)

    def test_fixture_week_and_explicit_incomplete_scores_remain_authoritative(self):
        payload = _provider_game(completed=False)
        fixture = normalize_game(payload)
        production = normalize_game(payload, season=2024, from_provider=True)
        self.assertEqual((fixture.week, fixture.provider_week), (1, None))
        self.assertEqual((production.week, production.provider_week), (0, 1))
        self.assertIs(production.completed, False)
        self.assertFalse(is_completed(production))
        self.assertEqual((production.home_points, production.away_points), (24, 21))

    def test_metadata_survives_cache_roundtrip_and_each_field_is_checksum_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SnapshotCache(Path(directory) / "snapshots")
            service = SeasonSnapshotService(_RecordedMetadataSource(), cache, clock=lambda: STAMP)
            snapshot = service.get(2024, "FBS")
            path = cache.path_for(2024, "FBS")
            original = path.read_bytes()
            self.assertEqual(service.load_cached(2024, "FBS").games, snapshot.games)
            stored = json.loads(original)
            game = stored["games"][0]
            self.assertEqual(
                {field: game[field] for field in ("week", "provider_week", "provider_id", "date", "completed")},
                {"week": 0, "provider_week": 1, "provider_id": "401635525", "date": "2024-08-24T16:00:00+00:00", "completed": True},
            )
            for field, value in (("provider_id", "other-id"), ("date", "2024-08-24T17:00:00+00:00"), ("provider_week", 0), ("completed", False)):
                with self.subTest(field=field):
                    changed = json.loads(original)
                    changed["games"][0][field] = value
                    cache.save(2024, "FBS", changed)
                    with self.assertRaises(ValueError):
                        service.load_cached(2024, "FBS")
                    path.write_bytes(original)

    def test_resealed_incorrect_canonical_week_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SnapshotCache(Path(directory) / "snapshots")
            service = SeasonSnapshotService(_RecordedMetadataSource(), cache, clock=lambda: STAMP)
            snapshot = service.get(2024, "FBS")
            state = cache.load(2024, "FBS")
            state["games"][0]["week"] = 1
            state["complete_through_week"] = 1
            games = tuple(normalize_game(row) for row in state["games"])
            state["checksum"] = _checksum(state, snapshot.teams, games)
            cache.save(2024, "FBS", state)
            with self.assertRaisesRegex(ValueError, "canonical week"):
                service.load_cached(2024, "FBS")

    def test_legacy_cache_is_rejected_without_spending_a_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = SnapshotCache(root / "snapshots")
            SeasonSnapshotService(FixtureSeasonSource(FIXTURES), cache).get(2025, "FBS")
            before = cache.path_for(2025, "FBS").read_bytes()
            meter = RequestMeter(root / "requests.sqlite3")
            source = ProductionSeasonSource(meter)
            service = SeasonSnapshotService(source, cache)
            with patch.object(source, "fetch_games", side_effect=AssertionError("unexpected transport")) as fetch:
                for method in (service.get, service.load_cached):
                    with self.subTest(method=method.__name__), self.assertRaises(SnapshotUnavailable):
                        method(2025, "FBS")
                fetch.assert_not_called()
            self.assertEqual(cache.path_for(2025, "FBS").read_bytes(), before)
            self.assertEqual(meter.audit_records(), [])

    def test_failed_explicit_refresh_does_not_unlock_legacy_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = SnapshotCache(root / "snapshots")
            SeasonSnapshotService(FixtureSeasonSource(FIXTURES), cache).get(2025, "FBS")
            meter = RequestMeter(root / "requests.sqlite3")
            source = ProductionSeasonSource(meter)
            service = SeasonSnapshotService(source, cache)
            with patch.object(source, "fetch_games", side_effect=RuntimeError("offline failed refresh")):
                with self.assertRaises(RuntimeError):
                    service.get(2025, "FBS", refresh_games=True)
            for method in (service.get, service.load_cached):
                with self.subTest(method=method.__name__), self.assertRaises(SnapshotUnavailable):
                    method(2025, "FBS")
            self.assertEqual(meter.audit_records(), [])


if __name__ == "__main__":
    unittest.main()
