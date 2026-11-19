from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest

from cbb.tournament import external_forecasts, read_picks, record_picks, report, tournament_games


class TournamentTrackerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.ledger = Path(directory.name) / "ledger.sqlite3"
        now = datetime.now(timezone.utc)
        self.g = {7: dict(id=7, homeTeamId=1, awayTeamId=2, homeTeam="A", awayTeam="B",
                          homeSeed=6, awaySeed=11, gameNotes="Championship - Sweet 16",
                          startDate=(now+timedelta(days=1)).isoformat(), status="scheduled")}
        self.f = {7: dict(game_id=7, cutoff_utc=(now-timedelta(days=1)).isoformat(),
                          home_margin=4, home_team_id=1, away_team_id=2)}

    def test_first_saved_pick_survives_reruns_and_is_graded_on_final(self):
        self.assertEqual(record_picks(self.ledger, 2027, self.g, self.f, "a"), 1)
        self.f[7]["home_margin"] = -1
        self.assertEqual(record_picks(self.ledger, 2027, self.g, self.f, "a"), 0)
        self.assertEqual(record_picks(self.ledger, 2027, self.g, self.f, "b"), 1)
        picks = read_picks(self.ledger, 2027, "a")
        self.assertIsNone(report(self.g, picks, True)["grade"])
        self.g[7].update(status="final", homePoints=80, awayPoints=75)
        result = report(self.g, picks, True)
        self.assertEqual(result["grade"]["rmse"], 1)
        self.assertEqual(result["seed_comparison"]["model_correct"], 1)
        self.assertEqual(result["games"][0]["pick"]["round"], "Sweet 16")
        self.g[7]["awayTeamId"] = 55
        self.assertIsNone(report(self.g, picks, True)["grade"])

    def test_cannot_backfill_a_live_prediction_or_use_a_future_cutoff(self):
        self.g[7]["startDate"] = self.f[7]["cutoff_utc"]
        self.assertEqual(record_picks(self.ledger, 2027, self.g, self.f, "a"), 0)
        late = (datetime.now(timezone.utc)+timedelta(days=6)).isoformat()
        self.g[7]["startDate"] = self.f[7]["cutoff_utc"] = late
        self.assertEqual(record_picks(self.ledger, 2027, self.g, self.f, "a"), 0)

    def test_external_forecasts_must_match_the_actual_matchup_and_cutoff(self):
        path = self.ledger.with_suffix(".json")
        path.write_text(json.dumps([dict(self.f[7], home_team_id=200)]))
        with self.assertRaisesRegex(ValueError, "home_team_id"):
            external_forecasts(path, self.g, self.f)

    def test_only_explicit_ncaa_tag_for_selected_season_enters_tracking(self):
        with sqlite3.connect(self.ledger) as db:
            db.execute("CREATE TABLE games(season INTEGER, start_utc TEXT, raw_json TEXT)")
            db.executemany("INSERT INTO games VALUES(?,?,?)", [
                (year, "2026-03-20", json.dumps({"id": key, "tournament": tag}))
                for year, key, tag in [(2026, 1, "NCAA"), (2026, 2, "NIT"),
                                      (2026, 3, "ACC"), (2027, 4, "NCAA")]
            ])
        self.assertEqual(set(tournament_games(self.ledger, 2026)), {1})

    def test_corrected_tipoff_must_still_follow_saved_timestamp(self):
        record_picks(self.ledger, 2027, self.g, self.f, "a")
        self.g[7].update(startDate=self.f[7]["cutoff_utc"], status="final", homePoints=80, awayPoints=70)
        result = report(self.g, read_picks(self.ledger, 2027, "a"), True)
        self.assertIsNone(result["grade"])
        self.assertEqual(result["games"][0]["grading"], "tipoff before freeze")
