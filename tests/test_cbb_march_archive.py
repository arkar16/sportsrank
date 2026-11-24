from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from cbb.march_archive import _completed_games, build_tournaments


class MarchArchiveTests(unittest.TestCase):
    def setUp(self):
        self.row = {"home_id": "1", "away_id": "2", "weekly_cutoff": "2024-03-18T10:00:00+00:00",
                    "tipoff": "2024-03-22T20:30:00+00:00", "weekly_margin": "0", "tipoff_margin": "-2.5"}
        self.game = dict(game_id=10, home_team_id=1, away_team_id=2, home_team="A", away_team="B & C",
                         start_utc=self.row["tipoff"], cutoff_utc=self.row["weekly_cutoff"], status="final",
                         ncaa_round="Final Four", home_points=65, away_points=69, actual_home_margin=-4,
                         home_margin=1.5)
        self.buffer = datetime.fromisoformat(self.row["tipoff"]) + timedelta(hours=6)

    def test_visibility_picks_and_scores_in_each_rendered_matchup(self):
        season = {"season": 2024, "forecasts": [self.game, {**self.game, "game_id": 11, "ncaa_round": None}]}
        with TemporaryDirectory() as tmp, patch("cbb.march_archive._load_margins", return_value={2024: {10: self.row}}):
            root = Path(tmp)
            self.assertEqual(build_tournaments(root, [season], self.buffer, set()), set())
            self.assertFalse((root / "tournaments").exists())
            build_tournaments(root, [season], self.buffer + timedelta(seconds=1), set())
            page = BeautifulSoup((root / "tournaments/2024.html").read_text(), "html.parser")
            rows = page.select("table")[0].select("tbody tr")
            self.assertEqual([r.find_all("td")[2].text for r in rows], ["5.50", "4.00", "1.50"])
            self.assertEqual([r.find_all("td")[3].text for r in rows], ["0/1", "0/0", "1/1"])
            result = page.select("table")[1].select("tbody tr")
            self.assertEqual(len(result), 1)
            self.assertEqual(list(result[0].stripped_strings)[1:],
                             ["B & C", "69", "A", "65", "A by 1.50", "Pick’em", "B & C by 2.50"])
            self.assertTrue(page.select_one('a[href="/cbb/teams/2/2024.html"]'))
            self.assertTrue(page.select_one('a[href="/cbb/performance/2024/index.html"]'))
            self.assertTrue(page.select_one('a[href="#round-1"]'))

    def test_rejects_unpaired_or_reassigned_games_and_bad_margins(self):
        for key, value in [("game_id", 9), ("home_team_id", 2), ("start_utc", self.row["weekly_cutoff"]),
                           ("cutoff_utc", self.row["tipoff"]), ("ncaa_round", None)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                _completed_games([{**self.game, key: value}], {10: self.row}, self.buffer + timedelta(days=1))
        with self.assertRaises(ValueError):
            _completed_games([self.game], {10: {**self.row, "tipoff_margin": "nan"}}, self.buffer + timedelta(days=1))
