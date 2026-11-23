import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from cbb.blend import blend_seasons
from cbb.replay import replay_seasons


class BlendTests(unittest.TestCase):
    def test_later_scores_only_enter_later_checkpoints(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE teams(season, id, raw_json)")
                db.execute("CREATE TABLE games(season, id, start_utc, status, raw_json)")
                for i in (1, 2):
                    team = {"school": f"College {i}", "sourceId": str(90001+i)}
                    db.execute("INSERT INTO teams VALUES (2020,?,?)", (i, json.dumps(team)))

                def score(game_id, date, home):
                    game = dict(homeTeamId=1, awayTeamId=2, neutralSite=True,
                                startTimeTbd=False, homePoints=home, awayPoints=60)
                    db.execute("DELETE FROM games WHERE id=?", (game_id,))
                    db.execute("INSERT INTO games VALUES (2020,?,?,'final',?)",
                               (game_id, date, json.dumps(game)))
                    db.commit()

                def margins():
                    forecasts = blend_seasons(path, replay_seasons(path, 2020))[0]["forecasts"]
                    return [g["home_margin"] for g in forecasts]

                score(1, "2019-11-05T22:00:00Z", 75)
                score(2, "2019-11-12T22:00:00Z", 81)
                score(4, "2019-11-13T22:00:00Z", 66)
                score(3, "2019-11-19T22:00:00Z", 77)
                original = margins()
                score(2, "2019-11-12T22:00:00Z", 34)
                changed = margins()
                self.assertEqual(original[:3], changed[:3])
                self.assertNotEqual(original[3], changed[3])
