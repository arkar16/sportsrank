from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from cbb.cors_v1 import cors_v1_seasons, iter_cors_v1_seasons, rounded
from cbb.replay import replay_seasons


class CorsV1Test(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "history.sqlite3"
        self.seed = Path(tmp.name) / "seed.json"
        self.save_seed({})
        self.keys = {(2020, t): t for t in range(1, 4)}
        with sqlite3.connect(self.db) as db:
            db.executescript("""
                CREATE TABLE teams(season,id,raw_json);
                CREATE TABLE games(season,id PRIMARY KEY,start_utc,status,raw_json);
            """)
            for _, t in self.keys:
                db.execute("INSERT INTO teams VALUES(2020,?,?)",
                           (t, json.dumps(dict(school=str(t), sourceId=t))))
        self.game(1, "05", 1, 2)
        self.game(2, "06", 2, 3)
        self.game(3, "12", 3, 1)
        self.game(4, "19", 1, 2)

    def save_seed(self, teams):
        self.seed.write_text(json.dumps(dict(season=2019, teams=teams, hfa_mean=3.6,
                                             hfa_var=.25, nond1_mean=-26, nond1_var=25)))

    def game(self, gid, day, h, a, *, clock="19:00", score=75, status="final", **fields):
        raw = dict(homeTeamId=h, awayTeamId=a, startTimeTbd=False, neutralSite=False,
                   homePoints=score, awayPoints=60)
        raw.update(fields)
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT OR REPLACE INTO games VALUES(2020,?,?,?,?)",
                       (gid, f"2019-11-{day}T{clock}:00Z", status, json.dumps(raw)))

    def run_model(self):
        return cors_v1_seasons(self.db, replay_seasons(self.db, 2020), seed_path=self.seed)[0]

    @staticmethod
    def margins(season):
        return [g["home_margin"] for g in season["forecasts"]]

    def test_future_d1_result(self):
        before = self.margins(self.run_model())
        self.game(3, "12", 3, 1, score=45)
        after = self.margins(self.run_model())
        self.assertEqual(before[:3], after[:3])
        self.assertNotEqual(before[3], after[3])

    def test_tier_game_at_exact_six_hour_boundary(self):
        before = self.run_model()
        # Monday 05:00 UTC is precisely six hours before this checkpoint.
        self.game(5, "11", 1, 99, clock="05:00", score=103)
        after = self.run_model()
        self.assertEqual(self.margins(before)[:3], self.margins(after)[:3])
        self.assertNotEqual(self.margins(before)[3], self.margins(after)[3])
        self.assertEqual([g["game_id"] for g in after["forecasts"]], [1, 2, 3, 4])
        def records(replay):
            return sorted((r["team_id"], r["wins"], r["losses"]) for r in replay["final_rankings"])
        self.assertEqual(records(before), records(after))
        for invalid in [dict(status="scheduled"), dict(startTimeTbd=True), dict(score=True),
                        dict(score=-1), dict(score=60)]:
            with self.subTest(invalid=invalid):
                self.game(5, "11", 1, 99, clock="05:00", **invalid)
                self.assertEqual(before, self.run_model())

    def test_order_invariance(self):
        baseline, = replay_seasons(self.db, 2020)
        tiers = [(datetime.fromisoformat("2019-11-06T23:00Z"), t, 35, 1) for t in (2, 1)]
        def replay(games, tier):
            season = {**baseline, "forecasts": games}
            return next(iter_cors_v1_seasons([season], self.keys, nond1_by_season={2020: tier}))
        self.assertEqual(replay(baseline["forecasts"], tiers),
                         replay(baseline["forecasts"][::-1], tiers[::-1]))

    def test_hiatus_entrant_centering_and_venue(self):
        self.save_seed({"1": [2018, -20, 10, 5, 80, -80, 10, 10],
                        "2": [2019, 20, 0, 5, -80, 80, 10, 10]})
        season = self.run_model()
        week = season["rankings"][0]
        rows = {r["team_id"]: r["cors"] for r in week["rows"]}
        self.assertAlmostEqual(sum(rows.values()), 0.)
        self.assertAlmostEqual(rows[1] - rows[3], .96**2 * 10 - (-12))
        self.assertAlmostEqual(rows[2] - rows[3], .96 * .55 * 20 - (-12))
        self.assertAlmostEqual(sum(r["cors"] for r in season["final_rankings"]), 0.)
        self.assertAlmostEqual(week["home_advantages"][1], 3.6 + 1.6)
        self.assertAlmostEqual(week["home_advantages"][2], 3.6 - 1.6)
        self.assertEqual(season["home_advantages"], season["rankings"][-1]["home_advantages"])
        self.assertEqual(rounded(-1.125), -1.13)
        self.assertEqual(rounded(1.125), 1.13)

    def test_seed_schema_and_boundary(self):
        self.save_seed({"1": [2019, 0, 0, 5, 0, 0, 0, 0]})
        baseline = replay_seasons(self.db, 2020)
        seed = json.loads(self.seed.read_text())
        for field, value in (("season", 2018), ("hfa_var", -1), ("nond1_mean", None),
                             ("teams", {"1": [1]}), ("teams", {"1": [2021,0,0,5,0,0,0,0]})):
            with self.subTest(field=field):
                bad = deepcopy(seed)
                bad[field] = value
                self.seed.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    cors_v1_seasons(self.db, baseline, seed_path=self.seed)
