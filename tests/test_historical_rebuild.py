"""Portable chronology, membership, and cross-season reconstruction checks."""
import csv
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from cfb.historical_rebuild import (
    HistoricalInputs, historical_carryover, local_day, rebuild_history,
)
from cfb.performance import _read, evaluate_season
from cfb.ranking_engine import PreviousFinal
from cfb.ranking_progression import build_progression_outputs


class HistoricalRebuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.site = self.root / "original"
        self.source = self.root / "games.csv"

    def table(self, relative, rows):
        path = self.site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pd.DataFrame(rows).to_html(index=False))

    def season(self, year, members, results):
        base = f"cfb/years/{year}"
        ranking = [{"rank": i + 1, "school": team, "conference": "Independent",
                    "cors": 20 - i * 10, "wins_vs_expected": 1 - i}
                   for i, team in enumerate(members)]
        self.table(f"{base}/rankings/{year}_FINAL_FBS_cors.html", ranking)
        self.table(f"{base}/rankings/{year}_W0_FBS_cors.html", ranking)
        self.table(f"{base}/data/results/{year}_FBS_results.html", results)
        for week in {row['week'] for row in results}:
            self.table(f"{base}/data/results/weekly_results/{year}_W{week}_FBS_results.html",
                       [row for row in results if row['week'] == week])
        self.table(f"{base}/spread/{year}_W1_FBS_spread.html", [{
            "week": 1, "home_team": members[0], "away_team": members[1],
            "neutral_site": False, "spread": members[0] + " -99"}])

    @staticmethod
    def result(week=1, home="A", away="B", score=10, away_score=0, neutral=False):
        return {"week": week, "home_team": home, "home_division": "fbs", "home_score": score,
                "away_team": away, "away_division": "fbs", "away_score": away_score,
                "neutral_site": neutral}

    @staticmethod
    def game(year=1901, identifier="r", phase="regular", day="1901-09-17", **changes):
        return {"id": identifier, "season": year, "season_type": phase, "week": 1,
                "start_date": day, "home_team": "A", "away_team": "B",
                "home_points": 10, "away_points": 0, "neutral_site": False,
                "home_classification": "fbs", "away_classification": "fbs", **changes}

    def csv(self, rows):
        with self.source.open('w') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def sample(self, bowl=True):
        self.season(1900, ["A", "B"], [self.result()])
        self.season(1901, ["A", "B"], [self.result()])
        self.season(1902, ["A", "C"], [self.result(away="C", score=14, away_score=7)])
        rows = [self.game(), self.game(1902, "r2", day="1902-09-16", away_team="C",
                                      home_points=14, away_points=7)]
        if bowl:
            rows.append(self.game(identifier="bowl", phase="postseason", day="1902-01-01",
                                  home_points=21, neutral_site=True))
        self.csv(rows)

    def test_missing_from_both_and_rematch_is_restored_beyond_old_cutoff(self):
        self.sample()
        snapshot, evidence = HistoricalInputs(self.site, self.source).snapshot(1901)
        self.assertEqual(len(snapshot.games), 2)
        self.assertEqual(evidence['week_one_boundary'], '1901-09-16')
        self.assertEqual(evidence['added_games'], [{'id': 'bowl', 'week': 16, 'home': 'A', 'away': 'B'}])
        self.assertEqual([game.home_points for game in snapshot.games], [10, 21])
        self.assertEqual(snapshot.complete_through_week, 16)

    def test_snapshot_can_feed_existing_progression_without_checksum_bypass(self):
        self.sample()
        inputs = HistoricalInputs(self.site, self.source)
        snapshot, _ = inputs.snapshot(1901)
        from cfb.ranking_engine import preseason_ranking
        rows = preseason_ranking(snapshot, inputs.prior_final(1900))
        document, _, _ = build_progression_outputs(snapshot, preseason_rows=rows,
                                                   phase="preseason", target_week=-1)
        self.assertEqual(document.source_snapshot, snapshot.checksum)

    def test_chain_preserves_saved_predictions_and_carries_changed_final_forward(self):
        self.sample()
        year_page = self.site / 'cfb/years/1901/1901_CFB.html'
        year_page.write_text('<html><body><a href="spread/1901_W16_FBS_spread.html">Postseason</a></body></html>')
        original = {path.relative_to(self.site): path.read_bytes()
                    for path in self.site.rglob('*') if path.is_file()}
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            record = rebuild_history(self.site, self.source, self.root / 'corrected', from_season=1901)
        corrected = self.root / 'corrected'
        self.assertEqual(record['added_postseason_games'], 1)
        self.assertEqual({path.relative_to(self.site): path.read_bytes()
                          for path in self.site.rglob('*') if path.is_file()}, original)
        for relative, raw in original.items():
            self.assertTrue((corrected / relative).exists())
            if '/spread/' in str(relative):
                self.assertEqual((corrected / relative).read_bytes(), raw)
        pre, _ = _read(corrected / 'cfb/years/1901/rankings/1901_PRESEASON_FBS_cors.html')
        self.assertEqual(float(next(row['cors'] for row in pre if row['school'] == 'A')), 18.25)
        w0, _ = _read(corrected / 'cfb/years/1901/rankings/1901_W0_FBS_cors.html')
        self.assertEqual(w0, pre)  # No invented empty scored checkpoint attenuates carryover.
        w1, _ = _read(corrected / 'cfb/years/1901/rankings/1901_W1_FBS_cors.html')
        maximum = 50 + math.log10(31) * 4 * 1.2
        expected = round(((50 + math.log10(11) * 4) * (0.8 + 10 / maximum * 0.4) + 18.25) / 2, 2)
        self.assertEqual(float(next(row['cors'] for row in w1 if row['school'] == 'A')), expected)
        final, _ = _read(corrected / 'cfb/years/1901/rankings/1901_FINAL_FBS_cors.html')
        next_pre, _ = _read(corrected / 'cfb/years/1902/rankings/1902_PRESEASON_FBS_cors.html')
        a = next(row for row in final if row['school'] == 'A')
        self.assertEqual(float(next(row['cors'] for row in next_pre if row['school'] == 'A')),
                         round(float(a['cors']) - 1.75 * float(a['wins_vs_expected']), 2))
        self.assertEqual(float(next(row['cors'] for row in next_pre if row['school'] == 'C')), -10)
        report = json.loads((corrected / 'cfb/performance/1901/report.json').read_text())
        self.assertEqual(report['summary']['game_count'], 2)
        self.assertEqual(report['summary']['saved_predictions'], 0)
        self.assertEqual(report['prediction_policy'], 'reconstructed_history')
        self.assertNotEqual(report['games'][0]['home_handicap'], '-99')
        self.assertEqual(report['games'][-1]['rating_checkpoint'], 'W15')
        self.assertIn('not the original issued forecasts', (corrected / 'cfb/performance/1901/index.html').read_text())
        self.assertIn('reconstructed/1901_W16_FBS_spread.html',
                      (corrected / 'cfb/years/1901/1901_CFB.html').read_text())

    def test_missing_returning_team_never_gets_entrant_fallback(self):
        with self.assertRaisesRegex(ValueError, 'missing or mismatches'):
            historical_carryover(1902, PreviousFinal({'A': 2}, {}, 1901, 'FBS'),
                                 {'A', 'B'}, {'A', 'B', 'C'})

    def test_modern_unregistered_entrants_still_fail_engine_validation(self):
        from cfb.ranking_engine import preseason_ranking, RankingContractError
        from cfb.season_snapshot import SeasonSnapshot
        from cfb.season_source import SourceTeam
        prior, _ = historical_carryover(2024, PreviousFinal({'A': 2}, {}, 2023, 'FBS'),
                                       {'A'}, {'A', 'Unknown'})
        snapshot = SeasonSnapshot('cfb', 'FBS', 2024,
                                  (SourceTeam('A', 'Independent'), SourceTeam('Unknown', 'Independent')),
                                  (), {}, '0' * 64)
        with self.assertRaises(RankingContractError):
            preseason_ranking(snapshot, prior)

    def test_eastern_midnight_and_date_only_calendar(self):
        self.assertEqual(str(local_day('2024-01-09 00:30:00')), '2024-01-08')
        self.assertEqual(str(local_day('1902-01-01')), '1902-01-01')

    def test_repeated_archival_row_counts_once_and_same_score_rematch_stays_distinct(self):
        self.sample()
        self.table('cfb/years/1901/data/results/1901_FBS_results.html',
                   [self.result(neutral=True), self.result(neutral=True)])
        self.csv([self.game(neutral_site=True), self.game(identifier='bowl',
                   phase='postseason', day='1902-01-01', neutral_site=True)])
        snapshot, evidence = HistoricalInputs(self.site, self.source).snapshot(1901)
        self.assertEqual(len(evidence['deduplicated_results']), 1)
        self.assertEqual(len(evidence['added_games']), 1)
        self.assertEqual([game.week for game in snapshot.games], [1, 16])

    def test_scored_week_zero_moves_out_of_preseason(self):
        self.season(2023, ['A', 'B'], [self.result(), self.result(week=2, score=14),
                                     self.result(week=3, score=21)])
        self.csv([self.game(2023, day='2023-08-26', home_points=10),
                  self.game(2023, identifier='r2', day='2023-09-09', week=2, home_points=14),
                  self.game(2023, identifier='r3', day='2023-09-16', week=3, home_points=21)])
        snapshot, evidence = HistoricalInputs(self.site, self.source).snapshot(2023)
        self.assertEqual(evidence['week_one_boundary'], '2023-08-28')
        self.assertEqual([game.week for game in snapshot.games], [0, 2, 3])

    def test_distinct_dated_games_in_one_provider_week_are_not_deduplicated(self):
        self.season(1906, ['A', 'B'], [self.result(week=1, score=15),
                                     self.result(week=3, score=12), self.result(week=3, score=28)])
        self.csv([self.game(1906, day='1906-10-06', week=3, home_points=12),
                  self.game(1906, identifier='second', day='1906-10-08', week=3, home_points=28),
                  self.game(1906, identifier='first', day='1906-09-18', week=1, home_points=15)])
        snapshot, evidence = HistoricalInputs(self.site, self.source).snapshot(1906)
        self.assertEqual(len(snapshot.games), 3)
        self.assertEqual(evidence['deduplicated_results'], [])
        self.assertEqual([game.provider_id for game in snapshot.games if game.week == 3], ['r', 'second'])

    def test_duplicate_source_and_source_output_overlap_reject(self):
        self.sample()
        self.csv([self.game(), self.game()])
        with self.assertRaisesRegex(ValueError, 'duplicate historical source'):
            HistoricalInputs(self.site, self.source)
        with self.assertRaisesRegex(ValueError, 'new and separate'):
            rebuild_history(self.site, self.source, self.site / 'review', from_season=1901)


if __name__ == '__main__':
    unittest.main()
