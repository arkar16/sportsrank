"""Gate 1 regressions for authoritative game-completion metadata."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

from cfb.ranking_engine import (
    PreviousFinal,
    RankingContractError,
    final_ranking,
    is_completed,
    records_for_week,
)
from cfb.season_snapshot import SeasonSnapshot, SeasonSnapshotService
from cfb.season_source import (
    FixtureSeasonSource,
    SourceGame,
    SourceTeam,
    normalize_game,
)
from cfb.snapshot_cache import SnapshotCache


FIXTURES = Path(__file__).parent / "fixtures" / "cfbd"
NOW = lambda: datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


def _raw_game(**overrides):
    value = {
        "week": 1,
        "home_team": "Alpha",
        "home_classification": "fbs",
        "home_points": 7,
        "away_team": "Beta",
        "away_classification": "fbs",
        "away_points": 3,
        "neutral_site": False,
    }
    value.update(overrides)
    return value


class CompletionNormalizationTests(unittest.TestCase):
    def test_completion_metadata_controls_finite_score_compatibility(self):
        absent = normalize_game(_raw_game())
        unfinished = normalize_game(_raw_game(completed=False))
        finished = normalize_game(_raw_game(completed=True))

        self.assertIsNone(_raw_game().get("completed"))
        self.assertTrue(absent.completed)
        self.assertEqual(absent.disposition, "completed")
        self.assertTrue(is_completed(absent))

        self.assertIs(unfinished.completed, False)
        self.assertEqual(unfinished.disposition, "scheduled")
        self.assertFalse(is_completed(unfinished))

        self.assertIs(finished.completed, True)
        self.assertEqual(finished.disposition, "completed")
        self.assertTrue(is_completed(finished))

    def test_conflicting_completion_and_cancellation_metadata_stays_unrankable(self):
        conflicting_status = normalize_game(
            _raw_game(completed=False, status="completed")
        )
        canceled = normalize_game(
            _raw_game(completed=True, disposition="canceled")
        )
        not_played = normalize_game(
            _raw_game(completed=True, disposition="not_played")
        )

        self.assertIs(conflicting_status.completed, False)
        self.assertFalse(is_completed(conflicting_status))
        self.assertEqual(canceled.disposition, "canceled")
        self.assertFalse(is_completed(canceled))
        self.assertEqual(not_played.disposition, "not_played")
        self.assertFalse(is_completed(not_played))

    def test_explicit_complete_without_finite_scores_is_not_rankable(self):
        game = normalize_game(
            _raw_game(completed=True, home_points=None, away_points=None)
        )

        self.assertIs(game.completed, True)
        self.assertFalse(is_completed(game))


class CompletionSnapshotAndRankingTests(unittest.TestCase):
    def test_explicit_unfinished_game_does_not_advance_snapshot_boundary(self):
        class Source:
            def fetch_teams(self, year, classification, *, cache_decision):
                return (
                    SourceTeam("Alpha", "Test"),
                    SourceTeam("Beta", "Test"),
                )

            def fetch_games(self, year, classification, *, cache_decision):
                return (
                    normalize_game(_raw_game(week=0, completed=True)),
                    normalize_game(_raw_game(week=1, completed=False)),
                )

        with tempfile.TemporaryDirectory() as directory:
            service = SeasonSnapshotService(
                Source(),
                SnapshotCache(Path(directory) / "snapshots"),
                clock=NOW,
            )
            snapshot = service.get(2025, "FBS")

        self.assertEqual(snapshot.complete_through_week, 0)
        self.assertEqual([game.week for game in snapshot.games if is_completed(game)], [0])
        rows = records_for_week(snapshot, 1)
        alpha = next(row for row in rows if row["school"] == "Alpha")
        beta = next(row for row in rows if row["school"] == "Beta")
        self.assertEqual((alpha["wins"], alpha["losses"]), (1, 0))
        self.assertEqual((beta["wins"], beta["losses"]), (0, 1))

        prior = PreviousFinal(
            {"Alpha": 10.0, "Beta": 9.0},
            {"Alpha": 0.0, "Beta": 0.0},
            year=2024,
            classification="FBS",
        )
        with self.assertRaises(RankingContractError):
            final_ranking(snapshot, prior)

    def test_existing_fixture_without_completion_metadata_keeps_score_compatibility(self):
        source = FixtureSeasonSource(FIXTURES)
        games = source.fetch_games(2025, "FBS", cache_decision="miss")

        self.assertTrue(games[0].completed)
        self.assertTrue(is_completed(games[0]))
        self.assertIsNone(games[1].completed)
        self.assertFalse(is_completed(games[1]))

        teams = source.fetch_teams(2025, "FBS", cache_decision="miss")
        snapshot = SeasonSnapshot(
            "cfb",
            "FBS",
            2025,
            teams,
            games,
            MappingProxyType({"complete_through_week": 1}),
            "fixture",
        )
        rows = records_for_week(snapshot, 1)
        alpha = next(row for row in rows if row["school"] == "Alpha State")
        beta = next(row for row in rows if row["school"] == "Beta Tech")
        self.assertEqual((alpha["wins"], alpha["losses"]), (1, 0))
        self.assertEqual((beta["wins"], beta["losses"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
