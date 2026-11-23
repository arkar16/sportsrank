import csv
from pathlib import Path
import unittest
from cbb.archive_finals import read_archive


class HistoricalFinalsTest(unittest.TestCase):
    def test_final_rows_keep_season_membership_and_d1_records(self):
        seasons, programs = read_archive(2020)
        self.assertEqual([s.year for s in seasons], list(range(1984, 2020)))
        self.assertIn("Maryland", programs.values())
        total = 0
        for season in seasons:
            rows = season.rankings["FINAL"]
            self.assertEqual([r.rank for r in rows], list(range(1, len(rows) + 1)))
            self.assertEqual(len({r.school for r in rows}), len(rows))
            self.assertEqual([r.rating for r in rows], sorted((r.rating for r in rows), reverse=True))
            wins, losses = zip(*(map(int, r.record.split("–")) for r in rows))
            self.assertEqual(sum(wins), sum(losses))
            total += len(rows)
        self.assertEqual(total, 11516)
        self.assertEqual(next(r.record for s in seasons if s.year == 2002 for r in s.rankings["FINAL"]
                              if r.school == "Maryland"), "32–4")
        self.assertEqual(read_archive(1984), ([], {}))

    def test_1984_eligible_field_and_dated_results(self):
        source = Path(__file__).resolve().parents[1] / "cbb/historical_inputs/1984"
        with (source / "teams.csv").open() as f:
            roster = {r["TeamID"] for r in csv.DictReader(f)}
        with (source / "results.csv").open() as f:
            games = list(csv.DictReader(f))
        self.assertEqual(len(roster), 275)
        self.assertEqual(len(games), 3717)
        self.assertEqual({r[k] for r in games for k in ("WTeamID", "LTeamID")}, roster)
        self.assertEqual(len({(r["DayNum"], frozenset((r["WTeamID"], r["LTeamID"]))) for r in games}), len(games))
        louisville, = [r for r in games if (r["DayNum"], r["WTeamID"], r["LTeamID"]) == ("137", "1257", "1287")]
        self.assertEqual((louisville["DayNum"], louisville["WScore"], louisville["LScore"], louisville["WLoc"]),
                         ("137", "72", "59", "N"))
