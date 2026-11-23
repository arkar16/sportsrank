import unittest
from cbb.archive_finals import read_archive


class HistoricalFinalsTest(unittest.TestCase):
    def test_final_rows_keep_season_membership_and_d1_records(self):
        seasons, programs = read_archive(2020)
        self.assertEqual([s.year for s in seasons], list(range(1985, 2020)))
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
        self.assertEqual(total, 11241)
        self.assertEqual(next(r.record for s in seasons if s.year == 2002 for r in s.rankings["FINAL"]
                              if r.school == "Maryland"), "32–4")
        self.assertEqual(read_archive(1985), ([], {}))
