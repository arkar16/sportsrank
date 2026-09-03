from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

import cfbd


CFB_ROOT = Path(__file__).resolve().parents[1] / "cfb"
sys.path.insert(0, str(CFB_ROOT))

import games
import teams


class CfbdFetcherTests(unittest.TestCase):
    @patch("games.cfbd.GamesApi")
    @patch("games.create_api_client")
    def test_games_use_the_v2_classification_filter(
        self, create_api_client, games_api_type
    ):
        api_client = MagicMock()
        create_api_client.return_value.__enter__.return_value = api_client
        games_api_type.return_value.get_games.return_value = []

        self.assertEqual(games.fetch_games(2025, "FBS", 3), [])

        games_api_type.assert_called_once_with(api_client)
        games_api_type.return_value.get_games.assert_called_once_with(
            year=2025,
            classification=cfbd.DivisionClassification.FBS,
            week=3,
        )

    @patch("teams.cfbd.TeamsApi")
    @patch("teams.create_api_client")
    def test_team_fetch_uses_the_shared_bearer_client(
        self, create_api_client, teams_api_type
    ):
        api_client = MagicMock()
        create_api_client.return_value.__enter__.return_value = api_client
        teams_api_type.return_value.get_fbs_teams.return_value = []

        self.assertEqual(teams.fetch_fbs_teams(2025), [])

        teams_api_type.assert_called_once_with(api_client)
        teams_api_type.return_value.get_fbs_teams.assert_called_once_with(year=2025)


if __name__ == "__main__":
    unittest.main()
