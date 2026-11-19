from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from cbb import replay_seasons


class TestCBBReplay(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "history ?#.sqlite3"
        self.db = self.enterContext(closing(sqlite3.connect(self.db_path)))
        self.db.executescript("""
            CREATE TABLE teams (
                season INTEGER, id INTEGER, raw_json TEXT NOT NULL, PRIMARY KEY (season, id)
            );
            CREATE TABLE games (
                id INTEGER PRIMARY KEY, season INTEGER, start_utc TEXT, status TEXT,
                raw_json TEXT NOT NULL
            );
        """)
        self.add_teams(2020, (1, "Alpha"), (2, "Beta"))

    def add_teams(self, season, *teams):
        self.db.executemany(
            "INSERT INTO teams VALUES (?, ?, ?)",
            [(season, team_id, json.dumps({"school": name})) for team_id, name in teams],
        )

    def add_game(
        self, game_id, tipoff, score=(80, 60), *, season=2020, teams=(1, 2),
        neutral=False, status="final", tbd=False,
    ):
        self.db.execute(
            "INSERT INTO games VALUES (?, ?, ?, ?, ?)",
            (game_id, season, tipoff, status, json.dumps({
                "homeTeamId": teams[0], "awayTeamId": teams[1], "neutralSite": neutral,
                "homePoints": score[0], "awayPoints": score[1], "startTimeTbd": tbd,
            })),
        )

    def replay(self, through_season=2020, **parameters):
        self.db.commit()
        return replay_seasons(self.db_path, through_season, **parameters)

    def test_hand_checked_football_parity_and_openers(self):
        self.add_game(2, "2019-11-11T18:00:00Z", (60, 80), neutral=True)
        self.add_game(1, "2019-11-04T17:00:00Z")
        (season,) = self.replay()
        preseason, week_one, week_two = season["rankings"]
        self.assertEqual(preseason["cutoff_utc"], "2019-11-04T11:00:00+00:00")
        self.assertEqual([row["cors"] for row in preseason["rows"]], [0.0, 0.0])
        alpha, beta = week_one["rows"]
        # Week 1: 50 win points and 4 * log10(21) margin points for Alpha.
        # Both have 0 SOS, so the multiplier is 0.8 before halving.
        self.assertEqual((alpha["cors"], beta["cors"]), (22.12, -2.12))
        self.assertEqual((alpha["mov"], beta["mov"]), (20.0, -20.0))
        self.assertEqual((alpha["expected_wins"], beta["expected_wins"]), (0.66, 0.34))
        self.assertEqual((alpha["wins_vs_expected"], beta["wins_vs_expected"]), (0.34, -0.34))
        # Week 2: each team has 25 win points, a 0 average margin, and its
        # opponent's week-1 rating as SOS. Each prior rating is divided by 2.
        alpha, beta = week_two["rows"]
        self.assertEqual((alpha["sos"], beta["sos"]), (-2.12, 22.12))
        self.assertEqual((alpha["cors"], beta["cors"]), (15.34, 11.4))
        self.assertEqual(season["final_rankings"], week_two["rows"])
        home, neutral = season["forecasts"]
        self.assertEqual((home["game_id"], neutral["game_id"]), (1, 2))
        self.assertEqual((home["home_margin"], home["actual_home_margin"]), (2.0, 20))
        self.assertEqual(neutral["cutoff_utc"], week_one["cutoff_utc"])
        self.assertEqual((neutral["home_margin"], neutral["actual_home_margin"]), (24.24, -20))

    def test_roster_turnover_and_carryover(self):
        self.add_game(1, "2019-11-04T17:00:00Z")
        self.add_teams(2021, (1, "Alpha renamed"), (3, "Gamma"))
        self.add_game(2, "2020-11-25T17:00:00Z", season=2021, teams=(1, 3), neutral=True)
        self.add_game(3, "2020-11-25T18:00:00Z", season=2021, teams=(1, 2))
        self.add_game(4, "2020-11-25T19:00:00Z", season=2021, teams=(3, 9))
        _, second = self.replay(2021)
        self.assertEqual(
            [(row["team_id"], row["school"], row["cors"]) for row in second["rankings"][0]["rows"]],
            [(1, "Alpha renamed", 21.53), (3, "Gamma", 0.0)],
        )
        (opener,) = second["forecasts"]
        self.assertEqual(opener["game_id"], 2)
        self.assertEqual(opener["home_margin"], 21.53)
        self.assertEqual(sum(row["wins"] for row in second["final_rankings"]), 1)
        _, without_carryover = self.replay(2021, carryover_strength=0.0)
        self.assertEqual(without_carryover["forecasts"][0]["home_margin"], 0.0)

    def test_future_score_perturbation_and_six_hour_boundary(self):
        # March 9 is the first Monday on EDT. Midnight local is 04:00 UTC.
        self.add_game(1, "2020-03-09T03:59:59Z")
        self.add_game(2, "2020-03-09T04:00:00Z")
        self.add_game(3, "2020-03-09T10:00:00Z", status="scheduled")
        self.add_game(4, "2020-03-09T10:00:01Z", status="scheduled")
        self.add_game(5, "2020-03-16T16:00:00Z", status="scheduled")
        (before,) = self.replay()
        rows = before["forecasts"]
        self.assertEqual(rows[2]["cutoff_utc"], "2020-03-02T11:00:00+00:00")
        self.assertEqual(rows[3]["cutoff_utc"], "2020-03-09T10:00:00+00:00")
        self.assertEqual(rows[3]["home_margin"], 26.24)
        self.assertEqual(before["rankings"][1]["rows"][0]["wins"], 1)
        self.assertEqual(before["final_cutoff_utc"], "2020-03-16T10:00:00+00:00")
        self.assertEqual(before["final_rankings"][0]["wins"], 2)

        self.db.execute(
            "UPDATE games SET raw_json = json_set(raw_json, '$.awayPoints', 120) WHERE id = 2"
        )
        (after,) = self.replay()
        self.assertEqual(before["rankings"][:2], after["rankings"][:2])
        for earlier, perturbed in zip(before["forecasts"][:4], after["forecasts"][:4], strict=True):
            for key in ("cutoff_utc", "home_cors", "away_cors", "home_margin"):
                self.assertEqual(earlier[key], perturbed[key])
        self.assertNotEqual(before["forecasts"][4]["home_margin"], after["forecasts"][4]["home_margin"])

    def test_experiments_and_unscored_games(self):
        self.add_game(1, "2019-11-04T17:00:00Z")
        self.add_game(2, "2019-11-11T17:00:00Z", status="cancelled")
        self.add_game(3, "2019-11-11T17:00:00Z", status="postponed")
        self.add_game(4, "2019-11-11T17:00:00Z", status="scheduled", tbd=True)
        self.add_game(5, "2019-11-18T17:00:00Z", status="scheduled")
        self.db.commit()
        cache_before = self.db_path.read_bytes()
        (season,) = self.replay(home_advantage=3.5, margin_weight=0.0)
        self.assertEqual(self.db_path.read_bytes(), cache_before)
        self.assertEqual([row["game_id"] for row in season["forecasts"]], [1, 5])
        self.assertEqual(season["forecasts"][0]["home_margin"], 3.5)
        self.assertIsNone(season["forecasts"][1]["actual_home_margin"])
        self.assertEqual(season["final_cutoff_utc"], "2019-11-11T11:00:00+00:00")
        alpha, beta = season["final_rankings"]
        self.assertEqual((alpha["cors"], beta["cors"]), (20.0, 0.0))

    def test_missing_cache_or_season_fails(self):
        missing = self.db_path.with_name("missing.sqlite3")
        with self.assertRaises(sqlite3.OperationalError):
            replay_seasons(missing, 2020)
        self.assertFalse(missing.exists())
        self.add_game(1, "2019-11-04T17:00:00Z")
        with self.assertRaisesRegex(ValueError, "Missing Division I roster for season 2021"):
            self.replay(2021)
