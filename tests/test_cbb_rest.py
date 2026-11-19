from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from cbb.rest import RestHistory


class RestCutoffTest(unittest.TestCase):
    def test_games_after_cutoff_do_not_reset_rest_for_later_matchup(self):
        def game(day, home, away):
            return SimpleNamespace(start_utc=datetime(2023, 1, day, 20, tzinfo=timezone.utc),
                                   home_team_id=home, away_team_id=away, score=(70, 65))
        monday = datetime(2023, 1, 9, 11, tzinfo=timezone.utc)
        history = RestHistory([game(5, 1, 3), game(8, 4, 2), game(10, 1, 2)])
        self.assertEqual(history.differential(1, 2, monday), 3)
        self.assertIsNone(history.differential(1, 5, monday))
