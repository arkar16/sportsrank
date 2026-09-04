from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import cfbd

from cfb.request_meter import MeteredRequestFailed, RequestBudgetExhausted, RequestBudgets, RequestMeter
from cfb.season_snapshot import SeasonSnapshotService
from cfb.season_source import FixtureSeasonSource, ProductionSeasonSource, SeasonSource, SourceTeam
from cfb.snapshot_cache import SnapshotCache


FIXTURES = Path(__file__).parent / "fixtures" / "cfbd"
NOW = lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


class SeasonSnapshotBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = FixtureSeasonSource(FIXTURES)
        self.cache = SnapshotCache(self.root / "snapshots")
        self.service = SeasonSnapshotService(self.source, self.cache, clock=NOW)

    def tearDown(self):
        self.temporary.cleanup()

    def test_fresh_season_is_two_calls_and_snapshot_is_normalized_immutable(self):
        snapshot = self.service.get(2025, "fbs")
        self.assertEqual(self.source.call_count, 2)
        self.assertEqual(snapshot.classification, "FBS")
        self.assertEqual(snapshot.teams[0], SourceTeam("Alpha State", "Test"))
        self.assertEqual(snapshot.games[0].home_classification, "fbs")
        self.assertEqual(len(snapshot.checksum), 64)
        persisted = json.loads(self.cache.path_for(2025, "FBS").read_text())
        self.assertEqual(persisted["checksum"], snapshot.checksum)
        with self.assertRaises(TypeError):
            snapshot.metadata["anything"] = "changed"

    def test_concurrent_same_season_initialization_fetches_only_once(self):
        original_fetch_teams = self.source.fetch_teams

        def slow_fetch_teams(year, classification, *, cache_decision):
            time.sleep(0.05)
            return original_fetch_teams(
                year, classification, cache_decision=cache_decision
            )

        self.source.fetch_teams = slow_fetch_teams
        start = threading.Barrier(2)

        def initialize():
            start.wait()
            return self.service.get(2025, "FBS").checksum

        with ThreadPoolExecutor(max_workers=2) as executor:
            checksums = list(executor.map(lambda _: initialize(), range(2)))

        self.assertEqual(checksums[0], checksums[1])
        self.assertEqual(
            [call[0] for call in self.source.calls], ["teams", "games"]
        )

    def test_cached_weekly_calculation_and_post_fetch_retry_are_zero_calls(self):
        first = self.service.get(2025, "FBS")
        calls_after_fetch = self.source.call_count
        try:
            raise RuntimeError("downstream calculation failed")
        except RuntimeError:
            pass
        retry_service = SeasonSnapshotService(self.source, self.cache, clock=NOW)
        retry = retry_service.get(2025, "FBS")
        self.assertEqual(self.source.call_count, calls_after_fetch)
        self.assertEqual(retry.checksum, first.checksum)

    def test_monday_refresh_fetches_games_only_when_teams_are_cached(self):
        self.service.get(2025, "FBS")
        refreshed = self.service.get(2025, "FBS", refresh_games=True)
        self.assertEqual(self.source.call_count, 3)
        self.assertEqual([call[0] for call in self.source.calls], ["teams", "games", "games"])
        self.assertEqual([call[3] for call in self.source.calls], ["miss", "miss", "refresh"])
        self.assertEqual(refreshed.teams[0].school, "Alpha State")

    def test_incomplete_required_week_refetches_games_only(self):
        snapshot = self.service.get(2025, "FBS")
        self.assertEqual(snapshot.complete_through_week, 1)
        self.service.get(2025, "FBS", required_week=2)
        self.assertEqual([call[0] for call in self.source.calls], ["teams", "games", "games"])
        self.assertEqual(self.source.calls[-1][3], "incomplete")

    def test_render_validation_and_deployment_cache_interface_make_zero_calls(self):
        expected = self.service.get(2025, "FBS").checksum
        before = self.source.call_count
        for _consumer in ("render", "validate", "deploy"):
            self.assertEqual(self.service.load_cached(2025, "FBS").checksum, expected)
        self.assertEqual(self.source.call_count, before)

    def test_historical_backfill_is_at_most_two_calls_per_season(self):
        for year in (2024, 2025):
            self.service.get(year, "FBS")
        self.assertEqual(self.source.call_count, 4)
        self.assertEqual(
            [(kind, year) for kind, year, _, _ in self.source.calls],
            [("teams", 2024), ("games", 2024), ("teams", 2025), ("games", 2025)],
        )

    def test_cache_checksum_detects_tampering_without_a_source_call(self):
        self.service.get(2025, "FBS")
        path = self.cache.path_for(2025, "FBS")
        persisted = json.loads(path.read_text())
        persisted["teams"][0]["school"] = "Tampered"
        path.write_text(json.dumps(persisted))
        before = self.source.call_count
        with self.assertRaisesRegex(ValueError, "checksum verification"):
            self.service.load_cached(2025, "FBS")
        self.assertEqual(self.source.call_count, before)

    def test_cache_rejects_tampered_completion_metadata_before_consumption(self):
        self.service.get(2025, "FBS")
        path = self.cache.path_for(2025, "FBS")
        persisted = json.loads(path.read_text())
        persisted["complete_through_week"] = 99
        path.write_text(json.dumps(persisted))
        before = self.source.call_count
        with self.assertRaisesRegex(ValueError, "completion metadata verification"):
            self.service.load_cached(2025, "FBS")
        self.assertEqual(self.source.call_count, before)

    def test_valid_v1_cache_migrates_without_refetching(self):
        snapshot = self.service.get(2025, "FBS")
        path = self.cache.path_for(2025, "FBS")
        persisted = json.loads(path.read_text())
        persisted["schema_version"] = 1
        legacy_content = {
            "sport": "cfb",
            "classification": "FBS",
            "year": 2025,
            "teams": persisted["teams"],
            "games": persisted["games"],
        }
        import hashlib

        persisted["checksum"] = hashlib.sha256(
            json.dumps(
                legacy_content, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        path.write_text(json.dumps(persisted))
        before = self.source.call_count
        migrated = self.service.load_cached(2025, "FBS")
        self.assertEqual(self.source.call_count, before)
        self.assertEqual(migrated.checksum, snapshot.checksum)
        self.assertEqual(json.loads(path.read_text())["schema_version"], 2)


class RequestMeterBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "meter.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def test_default_budgets_preserve_reserve(self):
        budgets = RequestBudgets()
        self.assertEqual((budgets.scheduled, budgets.historical, budgets.absolute), (100, 500, 2500))

    def test_budget_exhaustion_is_recorded_before_transport(self):
        meter = RequestMeter(self.path, RequestBudgets(scheduled=0, historical=500, absolute=2500), NOW)
        transport = MagicMock()
        with self.assertRaisesRegex(RequestBudgetExhausted, "before teams transport"):
            meter.execute(
                purpose="scheduled", endpoint="teams", season=2025,
                cache_decision="miss", transport=transport,
            )
        transport.assert_not_called()
        record = meter.audit_records()[0]
        self.assertEqual(record["outcome"], "blocked")
        self.assertEqual(record["purpose"], "scheduled")
        self.assertEqual(record["cache_decision"], "miss")
        self.assertEqual(record["budget_impact"], 0)

    def test_attempt_is_durable_before_transport_and_outcome_is_audited(self):
        meter = RequestMeter(self.path, clock=NOW)

        def transport():
            self.assertEqual(RequestMeter(self.path, clock=NOW).audit_records()[0]["outcome"], "started")
            return "ok"

        self.assertEqual(
            meter.execute(
                purpose="historical", endpoint="games", season=2024,
                cache_decision="refresh", transport=transport,
            ),
            "ok",
        )
        record = meter.audit_records()[0]
        self.assertEqual(record["outcome"], "succeeded")
        self.assertEqual(record["purpose"], "historical")
        self.assertEqual(record["cache_decision"], "refresh")
        self.assertEqual(record["budget_impact"], 1)

    def test_category_and_absolute_budgets_are_independently_enforced(self):
        meter = RequestMeter(
            self.path, RequestBudgets(scheduled=1, historical=2, absolute=2), NOW
        )
        meter.execute(
            purpose="scheduled", endpoint="teams", season=2025,
            cache_decision="miss", transport=lambda: None,
        )
        with self.assertRaises(RequestBudgetExhausted):
            meter.execute(
                purpose="scheduled", endpoint="games", season=2025,
                cache_decision="refresh", transport=lambda: None,
            )
        meter.execute(
            purpose="historical", endpoint="teams", season=2024,
            cache_decision="miss", transport=lambda: None,
        )
        with self.assertRaises(RequestBudgetExhausted):
            meter.execute(
                purpose="historical", endpoint="games", season=2024,
                cache_decision="incomplete", transport=lambda: None,
            )

    def test_failure_artifacts_and_exception_do_not_contain_credentials(self):
        secret = "super-secret-api-key"
        meter = RequestMeter(self.path, clock=NOW)

        def transport():
            raise RuntimeError(f"Authorization: Bearer {secret}")

        with self.assertRaises(MeteredRequestFailed) as raised:
            meter.execute(
                purpose="scheduled", endpoint="games", season=2025,
                cache_decision="incomplete", transport=transport,
            )
        records = meter.audit_records()
        self.assertEqual(records[0]["outcome"], "failed")
        self.assertEqual(records[0]["error_type"], "RuntimeError")
        self.assertEqual(records[0]["cache_decision"], "incomplete")
        self.assertEqual(records[0]["budget_impact"], 1)
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret.encode(), self.path.read_bytes())

    def test_existing_meter_database_is_migrated_and_backfilled(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE request_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requested_at TEXT NOT NULL,
                    month TEXT NOT NULL,
                    category TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    error_type TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO request_audit "
                "(requested_at, month, category, endpoint, season, outcome) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (NOW().isoformat(), "2026-09", "historical", "games", 2024, "succeeded"),
            )
        record = RequestMeter(self.path, clock=NOW).audit_records()[0]
        self.assertEqual(record["purpose"], "historical")
        self.assertEqual(record["cache_decision"], "miss")
        self.assertEqual(record["budget_impact"], 1)


class ProductionAdapterBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.meter = RequestMeter(self.root / "meter.sqlite3", clock=NOW)

    def tearDown(self):
        self.temporary.cleanup()

    @patch("cfb.season_source.cfbd.GamesApi")
    @patch("cfb.season_source.cfbd.TeamsApi")
    @patch("cfb.season_source.create_api_client")
    def test_every_production_transport_is_metered_and_full_season(self, create_client, teams_api, games_api):
        client = MagicMock()
        create_client.return_value.__enter__.return_value = client
        teams_api.return_value.get_fbs_teams.return_value = [{"school": "Alpha State", "conference": "Test"}]
        games_api.return_value.get_games.return_value = [{
            "week": 1, "home_team": "Alpha State", "home_classification": "fbs",
            "home_points": 7, "away_team": "Beta Tech", "away_classification": "fbs",
            "away_points": 3, "neutral_site": False,
        }]
        source = ProductionSeasonSource(self.meter)
        service = SeasonSnapshotService(source, SnapshotCache(self.root / "cache"), clock=NOW)
        service.get(2025, "FBS")
        records = self.meter.audit_records()
        self.assertEqual([r["endpoint"] for r in records], ["teams", "games"])
        self.assertEqual([r["purpose"] for r in records], ["scheduled", "scheduled"])
        self.assertEqual([r["cache_decision"] for r in records], ["miss", "miss"])
        self.assertEqual([r["budget_impact"] for r in records], [1, 1])
        games_api.return_value.get_games.assert_called_once_with(year=2025, classification=cfbd.DivisionClassification.FBS)
        self.assertTrue(isinstance(source, SeasonSource))

    def test_fixture_and_production_adapters_share_consumer_contract(self):
        fixture = FixtureSeasonSource(FIXTURES)
        self.assertTrue(isinstance(fixture, SeasonSource))
        self.assertEqual(
            {name for name in ("fetch_teams", "fetch_games") if hasattr(fixture, name)},
            {name for name in ("fetch_teams", "fetch_games") if hasattr(ProductionSeasonSource, name)},
        )

    def test_credentials_are_not_read_during_adapter_construction(self):
        with patch.dict(os.environ, {}, clear=True):
            source = ProductionSeasonSource(self.meter)
        self.assertIsInstance(source, ProductionSeasonSource)


if __name__ == "__main__":
    unittest.main()
