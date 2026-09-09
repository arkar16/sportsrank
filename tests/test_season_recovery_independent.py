"""Independent behavioral checks for the CFBD recovery seam."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from cfb.request_meter import (
    MeteredRequestFailed,
    RequestBudgetExhausted,
    RequestBudgets,
    RequestMeter,
)
from cfb.season_snapshot import SeasonSnapshotService
from cfb.season_source import FixtureSeasonSource, ProductionSeasonSource
from cfb.snapshot_cache import SnapshotCache


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cfbd"


class RequestMeterIndependentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_absolute_exhaustion_blocks_transport_before_http(self):
        meter = RequestMeter(
            self.root / "meter.sqlite3",
            RequestBudgets(scheduled=2, historical=2, absolute=1),
        )
        first_transport = MagicMock(return_value="first")
        blocked_transport = MagicMock(return_value="must not run")

        meter.execute(
            purpose="historical",
            endpoint="teams",
            season=2024,
            cache_decision="miss",
            transport=first_transport,
        )
        with self.assertRaises(RequestBudgetExhausted):
            meter.execute(
                purpose="scheduled",
                endpoint="games",
                season=2025,
                cache_decision="refresh",
                transport=blocked_transport,
            )

        first_transport.assert_called_once_with()
        blocked_transport.assert_not_called()
        records = meter.audit_records()
        self.assertEqual(
            [record["outcome"] for record in records], ["succeeded", "blocked"]
        )
        self.assertEqual(
            [record["budget_impact"] for record in records], [1, 0]
        )

    def test_concurrent_attempts_cannot_overspend_absolute_budget(self):
        meter = RequestMeter(
            self.root / "meter.sqlite3",
            RequestBudgets(scheduled=2, historical=2, absolute=1),
        )
        calls = []
        calls_lock = threading.Lock()

        def transport():
            with calls_lock:
                calls.append("http")
            return "ok"

        def attempt():
            try:
                meter.execute(
                    purpose="scheduled",
                    endpoint="games",
                    season=2025,
                    cache_decision="refresh",
                    transport=transport,
                )
            except RequestBudgetExhausted:
                return "blocked"
            return "succeeded"

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _index: attempt(), (1, 2)))

        self.assertEqual(sorted(outcomes), ["blocked", "succeeded"])
        self.assertEqual(calls, ["http"])
        self.assertEqual(
            [record["budget_impact"] for record in meter.audit_records()], [1, 0]
        )


class SeasonSourceIndependentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_concurrent_initial_get_does_not_duplicate_source_calls(self):
        source = FixtureSeasonSource(FIXTURE_ROOT)
        service = SeasonSnapshotService(source, SnapshotCache(self.root / "cache"))

        with ThreadPoolExecutor(max_workers=2) as workers:
            snapshots = list(
                workers.map(lambda _index: service.get(2025, "FBS"), (1, 2))
            )

        self.assertEqual(source.call_count, 2)
        self.assertEqual(snapshots[0].checksum, snapshots[1].checksum)

    def test_cache_tampering_of_completion_metadata_is_rejected(self):
        cache = SnapshotCache(self.root / "cache")
        source = FixtureSeasonSource(FIXTURE_ROOT)
        service = SeasonSnapshotService(source, cache)
        service.get(2025, "FBS")

        path = cache.path_for(2025, "FBS")
        persisted = json.loads(path.read_text(encoding="utf-8"))
        persisted["complete_through_week"] = 99
        path.write_text(json.dumps(persisted), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "verification failed"):
            service.load_cached(2025, "FBS")

    @patch("cfb.season_source.cfbd.GamesApi")
    @patch("cfb.season_source.cfbd.TeamsApi")
    @patch("cfb.season_source.create_api_client")
    def test_fixture_and_production_return_identical_normalized_shapes(
        self, create_client, teams_api, games_api
    ):
        fixture_source = FixtureSeasonSource(FIXTURE_ROOT)
        expected_teams = fixture_source.fetch_teams(
            2025, "FBS", cache_decision="miss"
        )
        expected_games = fixture_source.fetch_games(
            2025, "FBS", cache_decision="miss"
        )

        fixture_dir = FIXTURE_ROOT / "2025" / "fbs"
        teams_api.return_value.get_fbs_teams.return_value = json.loads(
            (fixture_dir / "teams.json").read_text(encoding="utf-8")
        )
        provider_games = json.loads(
            (fixture_dir / "games.json").read_text(encoding="utf-8")
        )
        for index, game in enumerate(provider_games, 1):
            game.update(
                {
                    "id": f"fixture-provider-{index}",
                    "startDate": f"2025-08-{27 + index:02d}T12:00:00Z",
                    "seasonType": "regular",
                }
            )
        games_api.return_value.get_games.return_value = provider_games
        create_client.return_value.__enter__.return_value = object()
        source = ProductionSeasonSource(RequestMeter(self.root / "meter.sqlite3"))

        self.assertEqual(
            source.fetch_teams(2025, "FBS", cache_decision="miss"), expected_teams
        )
        actual_games = source.fetch_games(2025, "FBS", cache_decision="miss")
        self.assertEqual(
            [
                (game.week, game.home_team, game.away_team, game.home_points, game.away_points)
                for game in actual_games
            ],
            [
                (game.week, game.home_team, game.away_team, game.home_points, game.away_points)
                for game in expected_games
            ],
        )
        self.assertTrue(all(game.phase == "regular" for game in actual_games))
        self.assertTrue(all(game.provider_week is not None for game in actual_games))

    @patch("cfb.season_source.create_api_client")
    def test_failed_production_transport_does_not_leak_secret_to_exception_db_or_cache(
        self, create_client
    ):
        secret = "recovery-secret-token"
        create_client.side_effect = RuntimeError(f"Authorization: Bearer {secret}")
        meter_path = self.root / "meter.sqlite3"
        cache_root = self.root / "cache"
        source = ProductionSeasonSource(RequestMeter(meter_path))
        service = SeasonSnapshotService(source, SnapshotCache(cache_root))

        with patch.dict(os.environ, {"CFBD_API_KEY": secret}, clear=True):
            with self.assertRaises(MeteredRequestFailed) as raised:
                service.get(2025, "FBS")

        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret.encode(), meter_path.read_bytes())
        if cache_root.exists():
            for artifact in cache_root.rglob("*"):
                if artifact.is_file():
                    self.assertNotIn(secret.encode(), artifact.read_bytes())
        for fixture in FIXTURE_ROOT.rglob("*"):
            if fixture.is_file():
                contents = fixture.read_bytes().lower()
                self.assertNotIn(b"authorization", contents)
                self.assertNotIn(b"bearer ", contents)


if __name__ == "__main__":
    unittest.main()
