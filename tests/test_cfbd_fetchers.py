from pathlib import Path
import sys
import unittest


CFB_ROOT = Path(__file__).resolve().parents[1] / "cfb"
sys.path.insert(0, str(CFB_ROOT))

import games
import teams
from season_source import SourceGame, SourceTeam


class _Snapshot:
    teams = (SourceTeam("Alpha", "Conference A"),)
    games = (
        SourceGame(1, "Alpha", "fbs", 21, "Beta", "fbs", 14, False),
        SourceGame(2, "Beta", "fbs", None, "Alpha", "fbs", None, True),
    )


class _Service:
    def __init__(self):
        self.calls = []

    def get(self, year, division, **options):
        self.calls.append((year, division, options))
        return _Snapshot()


class CfbdFetcherCompatibilityTests(unittest.TestCase):
    def test_team_fetch_delegates_to_snapshot_seam(self):
        service = _Service()
        result = teams.fetch_fbs_teams(2025, snapshot_service=service)
        self.assertEqual(result[0].school, "Alpha")
        self.assertEqual(service.calls, [(2025, "FBS", {})])

    def test_week_games_are_derived_from_full_snapshot(self):
        service = _Service()
        result = games.fetch_games(2025, "FBS", week=2, snapshot_service=service)
        self.assertEqual([game.week for game in result], [2])
        self.assertEqual(service.calls, [(2025, "FBS", {
            "refresh_games": False, "required_week": None
        })])

    def test_legacy_dataframes_preserve_columns_and_values(self):
        results = games._results_dataframe(_Snapshot.games)
        slate = games._slate_dataframe(_Snapshot.games)
        self.assertEqual(
            list(results.columns),
            ["week", "home_team", "home_division", "home_score", "away_team",
             "away_division", "away_score", "neutral_site"],
        )
        self.assertEqual(results.loc[0, "home_score"], 21)
        self.assertEqual(
            list(slate.columns),
            ["week", "home_team", "home_division", "away_team",
             "away_division", "neutral_site"],
        )


if __name__ == "__main__":
    unittest.main()
