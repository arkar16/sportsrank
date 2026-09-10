"""Gate 1 regression contracts for the metered source and ranking seams."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb.ranking_engine import (
    PreviousFinal,
    final_ranking,
    is_completed,
    records_for_week,
    season_is_complete,
)
from cfb.request_meter import MeteredRequestFailed, RequestMeter
from cfb.season_snapshot import SeasonSnapshotService, SnapshotUnavailable
from cfb.season_source import ProductionSeasonSource, SourceGame, SourceTeam
from cfb.snapshot_cache import SnapshotCache


NOW = lambda: datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


def _teams_payload():
    return [
        {"school": "Alpha", "conference": "Test"},
        {"school": "Beta", "conference": "Test"},
    ]


def _games_payload():
    return [
        {
            "id": "fixture-2025-1",
            "week": 1,
            "seasonType": "regular",
            "start_date": "2025-08-28T21:30:00Z",
            "home_team": "Alpha",
            "home_classification": "fbs",
            "home_points": 21,
            "away_team": "Beta",
            "away_classification": "fbs",
            "away_points": 14,
            "neutral_site": False,
        }
    ]


class MeteredSourceContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.meter = RequestMeter(self.root / "meter.sqlite3", clock=NOW)

    def tearDown(self):
        self.temporary.cleanup()

    @patch("cfb.season_source.cfbd.GamesApi")
    @patch("cfb.season_source.cfbd.TeamsApi")
    @patch("cfb.season_source.create_api_client")
    def test_malformed_provider_payload_is_failed_inside_meter_and_requires_refresh(
        self, create_client, teams_api, games_api
    ):
        create_client.return_value.__enter__.return_value = object()
        teams_api.return_value.get_fbs_teams.return_value = _teams_payload()
        malformed = dict(_games_payload()[0])
        malformed["week"] = "not-a-week"
        games_api.return_value.get_games.return_value = [malformed]
        service = SeasonSnapshotService(
            ProductionSeasonSource(self.meter),
            SnapshotCache(self.root / "snapshots"),
            clock=NOW,
        )

        with self.assertRaises(MeteredRequestFailed):
            service.get(2025, "FBS")
        records = self.meter.audit_records()
        self.assertEqual([row["endpoint"] for row in records], ["teams", "games"])
        self.assertEqual([row["outcome"] for row in records], ["succeeded", "failed"])
        self.assertEqual(games_api.return_value.get_games.call_count, 1)

        # A normal cache access must fail closed after the failed normal fetch.
        with self.assertRaises(SnapshotUnavailable):
            service.get(2025, "FBS")
        self.assertEqual(games_api.return_value.get_games.call_count, 1)
        self.assertEqual(len(self.meter.audit_records()), 2)

        # Only an explicit refresh may spend another request.  The provider is
        # corrected before the retry, so the refreshed snapshot succeeds.
        games_api.return_value.get_games.return_value = _games_payload()
        snapshot = service.get(2025, "FBS", refresh_games=True)
        self.assertEqual(snapshot.games[0].home_points, 21)
        self.assertEqual(games_api.return_value.get_games.call_count, 2)
        self.assertEqual(self.meter.audit_records()[-1]["cache_decision"], "refresh")
        service.get(2025, "FBS")
        self.assertEqual(games_api.return_value.get_games.call_count, 2)
        self.assertEqual(len(self.meter.audit_records()), 3)

    @patch("cfb.season_source.cfbd.TeamsApi")
    @patch("cfb.season_source.create_api_client")
    def test_auth_or_transport_failure_is_metered_and_normal_retry_is_blocked(
        self, create_client, teams_api
    ):
        create_client.side_effect = RuntimeError("Authorization: Bearer <REDACTED>")
        service = SeasonSnapshotService(
            ProductionSeasonSource(self.meter),
            SnapshotCache(self.root / "snapshots"),
            clock=NOW,
        )

        with self.assertRaises(MeteredRequestFailed):
            service.get(2025, "FBS")
        self.assertEqual(self.meter.audit_records()[-1]["outcome"], "failed")
        with self.assertRaises(SnapshotUnavailable):
            service.get(2025, "FBS")
        self.assertEqual(len(self.meter.audit_records()), 1)

    def test_budget_preflight_still_precedes_any_provider_call(self):
        # The RequestMeter owns this gate; the source seam must not create a
        # client when the budget is already exhausted.
        from cfb.request_meter import RequestBudgets, RequestBudgetExhausted

        meter = RequestMeter(
            self.root / "blocked.sqlite3",
            RequestBudgets(scheduled=0, historical=500, absolute=2500),
            clock=NOW,
        )
        source = ProductionSeasonSource(meter)
        with patch("cfb.season_source.create_api_client") as create_client:
            with self.assertRaises(RequestBudgetExhausted):
                source.fetch_teams(2025, "FBS", cache_decision="miss")
        create_client.assert_not_called()
        self.assertEqual(meter.audit_records()[0]["outcome"], "blocked")

    @patch("cfb.season_source.cfbd.GamesApi")
    @patch("cfb.season_source.cfbd.TeamsApi")
    @patch("cfb.season_source.create_api_client")
    def test_failed_refresh_preserves_a_valid_cache_for_zero_call_normal_reads(
        self, create_client, teams_api, games_api
    ):
        create_client.return_value.__enter__.return_value = object()
        teams_api.return_value.get_fbs_teams.return_value = _teams_payload()
        games_api.return_value.get_games.return_value = _games_payload()
        service = SeasonSnapshotService(
            ProductionSeasonSource(self.meter),
            SnapshotCache(self.root / "snapshots"),
            clock=NOW,
        )
        first = service.get(2025, "FBS")
        malformed = dict(_games_payload()[0])
        malformed["week"] = "broken"
        games_api.return_value.get_games.return_value = [malformed]

        with self.assertRaises(MeteredRequestFailed):
            service.get(2025, "FBS", refresh_games=True)
        self.assertEqual(games_api.return_value.get_games.call_count, 2)

        cached = service.get(2025, "FBS")
        self.assertEqual(cached.checksum, first.checksum)
        self.assertEqual(games_api.return_value.get_games.call_count, 2)


class RankingDispositionContracts(unittest.TestCase):
    def test_explicit_non_played_with_finite_scores_is_excluded_from_final_inputs(self):
        teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
        played = SourceGame(1, "Alpha", "FBS", 21, "Beta", "FBS", 14, False)
        canceled = SourceGame(
            2,
            "Alpha",
            "FBS",
            99,
            "Beta",
            "FBS",
            0,
            False,
            disposition="canceled",
            disposition_source="https://example.test/cancellation",
        )
        from types import MappingProxyType

        from cfb.season_snapshot import SeasonSnapshot

        snapshot = SeasonSnapshot(
            "cfb",
            "FBS",
            2024,
            teams,
            (played, canceled),
            MappingProxyType({"complete_through_week": 1}),
            "offline",
        )

        self.assertTrue(is_completed(played))
        self.assertFalse(is_completed(canceled))
        self.assertFalse(
            is_completed(
                {
                    "home_points": 99,
                    "away_points": 0,
                    "disposition": "not_played",
                }
            )
        )
        self.assertTrue(season_is_complete(snapshot))
        rows = records_for_week(snapshot, 2)
        alpha = next(row for row in rows if row["school"] == "Alpha")
        self.assertEqual(alpha["wins"], 1)
        self.assertEqual(alpha["losses"], 0)
        final = final_ranking(
            snapshot,
            PreviousFinal(
                {"Alpha": 10.0, "Beta": 9.0},
                {"Alpha": 0.0, "Beta": 0.0},
                year=2023,
                classification="FBS",
            ),
        )
        alpha_final = next(row for row in final if row["school"] == "Alpha")
        self.assertEqual(alpha_final["wins"], 1)
        self.assertEqual(alpha_final["losses"], 0)


if __name__ == "__main__":
    unittest.main()
