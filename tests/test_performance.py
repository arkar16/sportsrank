"""Independent saved-spread arithmetic and public integration regressions."""
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from bs4 import BeautifulSoup

from cfb.performance import backforecast, build_performance_files, write_performance, validate_performance, NAV_LINK
from cfb.season_source import SourceGame
from cfb.public_site import validate_and_export, validate_public_output, verify_local_receipt
from tests.test_public_site import _fixture, REPOSITORY
from scripts.backforecast import main


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = Path(self.tmp.name) / "site"
        self.root = self.site / "cfb/years/2020"
        (self.site / "cfb").mkdir(parents=True)
        (self.site / "cfb/cfb.html").write_text('<a href="years/2020/2020_CFB.html">2020</a>')

    def table(self, path, rows, encoding="utf-8"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pd.DataFrame(rows).to_html(index=False), encoding=encoding)

    def seed(self):
        results, predictions = [], []
        for home, away, line, hs, aws in (("San José State", "B", "-3.5", 20, 17),
                                         ("C", "D", "+2", 10, 13),
                                         ("E", "F", "-7", 14, 7),
                                         ("G", "H", "+0", 7, 7)):
            common = {"week": 1, "home_team": home, "away_team": away, "neutral_site": True}
            predictions.append({**common, "home_cors": "15.4025", "away_cors": "10.1275", "spread": home+" "+line})
            results.append({**common, "home_division": "fbs", "away_division": "fbs", "home_score": hs, "away_score": aws})
        self.spread = self.root / 'spread/2020_W1_FBS_spread.html'
        self.result = self.root / 'data/results/weekly_results/2020_W1_FBS_results.html'
        self.table(self.spread, predictions, 'latin-1')
        self.table(self.result, results, 'latin-1')

    def test_saved_precision_encoding_and_first_available_week_without_rankings(self):
        self.seed()
        original = {p: p.read_bytes() for p in self.site.rglob('*') if p.is_file()}
        with patch('socket.socket', side_effect=AssertionError('offline only')), \
             patch('cfb.performance.natural_matchup', side_effect=AssertionError('do not recalculate saved predictions')):
            report = backforecast(self.site, 2020)
        self.assertEqual(report['from_week'], 1)
        self.assertEqual(report['games'][0]['home'], 'San José State')
        self.assertEqual([Decimal(g['home_handicap']) for g in report['games']], list(map(Decimal, ['-3.5', '2', '-7', '0'])))
        summary = report['summary']
        self.assertEqual((summary['saved_predictions'], summary['reconstructed_predictions']), (4, 0))
        self.assertEqual((summary['straight_up_wins'], summary['straight_up_losses']), (3, 0))
        self.assertEqual((summary['covers'], summary['no_covers'], summary['pushes']), (1, 1, 1))
        self.assertEqual(summary['unknown_selections'], 1)
        self.assertEqual(Decimal(summary['mae']), Decimal('.375'))
        self.assertEqual(Decimal(summary['rmse']), (Decimal('1.25')/4).sqrt())
        self.assertEqual(original, {p: p.read_bytes() for p in original})

    def test_all_season_static_files_links_and_omission_or_tamper_rejected(self):
        self.seed()
        inputs = {self.spread: self.spread.read_bytes(), self.result: self.result.read_bytes()}
        paths = write_performance(self.site)
        self.assertEqual(validate_performance(self.site), paths)
        self.assertEqual((self.site/'cfb/cfb.html').read_bytes().count(NAV_LINK), 1)
        self.assertEqual(write_performance(self.site), paths)
        page = self.site/'cfb/performance/2020/index.html'
        self.assertIn('Game results', page.read_text())
        self.assertIn('San José State', page.read_text())
        report = self.site/'cfb/performance/2020/report.json'
        data = json.loads(report.read_bytes()); data['summary']['mae'] = '0'
        report.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate_performance(self.site)
        write_performance(self.site)
        page.unlink()
        with self.assertRaisesRegex(ValueError, 'paths differ'):
            validate_performance(self.site)
        self.assertEqual(inputs, {p: p.read_bytes() for p in inputs})

    def test_missing_spread_uses_retained_four_decimal_ratings_without_rounding(self):
        self.seed()
        self.spread.unlink()
        rating_path = self.root / 'rankings/2020_W0_FBS_cors.html'
        self.table(rating_path, [{'school': 'A', 'cors': '15.4025'}, {'school': 'B', 'cors': '10.1275'}])
        original = rating_path.read_bytes()
        for neutral, expected in ((True, '-5.2750'), (False, '-7.2750')):
            with self.subTest(neutral=neutral):
                self.table(self.result, [{'week': 1, 'home_team': 'A', 'away_team': 'B',
                    'home_division': 'fbs', 'away_division': 'fbs', 'neutral_site': neutral,
                    'home_score': 20, 'away_score': 17}])
                report = backforecast(self.site, 2020)
                self.assertEqual(report['games'][0]['home_handicap'], expected)
                self.assertEqual(report['games'][0]['prediction_source'], 'Reconstructed')
                self.assertEqual(report['summary']['reconstructed_predictions'], 1)
                self.assertEqual(rating_path.read_bytes(), original)

    def test_weekly_results_grade_missing_and_unverified_forecasts_without_changing_records(self):
        # Week 5 has no saved prediction; Week 7 has a saved spread but the
        # old result page refuses to grade it without publication evidence.
        ledger = self.root / 'forecasts/ledger.json'
        ledger.parent.mkdir(parents=True)
        ledger.write_text(json.dumps({'schema_version': 'forecast-ledger/v1', 'season': 2020,
                                     'candidates': [], 'receipts': [], 'publication_provenance': [],
                                     'timing_evidence': [], 'score_history': []}))
        for week, home_score, away_score in ((5, 24, 17), (7, 20, 17)):
            common = {'week': week, 'home_team': 'A', 'away_team': 'B', 'neutral_site': False}
            self.table(self.root / f'data/results/weekly_results/2020_W{week}_FBS_results.html',
                       [{**common, 'home_division': 'fbs', 'away_division': 'fbs',
                         'home_score': home_score, 'away_score': away_score},
                        {**common, 'home_team': 'C', 'away_team': 'D', 'home_division': 'fbs',
                         'away_division': 'fbs', 'home_score': '—', 'away_score': '—'},
                        {**common, 'home_team': 'E', 'away_team': 'F', 'home_division': 'fbs',
                         'away_division': 'fbs', 'home_score': 10, 'away_score': 7},
                        {**common, 'home_team': 'G', 'away_team': 'H', 'home_division': 'fbs',
                         'away_division': 'fcs', 'home_score': 10, 'away_score': 7}])
            self.table(self.root / f'rankings/2020_W{week-1}_FBS_cors.html',
                       [{'school': 'A', 'cors': '10.50'}, {'school': 'B', 'cors': '8.00'}])
            path = self.root / f'spread/2020_W{week}_FBS_spread_results.html'
            path.parent.mkdir(exist_ok=True)
            path.write_text('<p>Last updated: 2026-10-18T22:25:51+00:00</p>'
                            '<p>Missing forecast / Unverified publication</p>')
        spread = self.root / 'spread/2020_W7_FBS_spread.html'
        self.table(spread, [{**common, 'home_cors': '10.50', 'away_cors': '8.00', 'spread': 'A -3.25'}])
        preserved = {p: p.read_bytes() for p in self.root.rglob('*')
                     if p.is_file() and not p.name.endswith('_spread_results.html')}
        with patch('socket.socket', side_effect=AssertionError('offline only')):
            write_performance(self.site)
        for week, handicap, coverage, error in ((5, '-4.5', 'cover', '2.5'), (7, '-3.25', 'no_cover', '.25')):
            page = self.root / f'spread/2020_W{week}_FBS_spread_results.html'
            soup = BeautifulSoup(page.read_bytes(), 'html.parser')
            self.assertTrue(soup.select('table'), f'Week {week} still has no forecast results')
            headers = [c.get_text() for c in soup.select('table')[-1].select('th')]
            values = [c.get_text() for c in soup.select('table')[-1].select('tbody tr')[0].select('td')]
            row = dict(zip(headers, values))
            self.assertEqual(Decimal(row['CORS spread (home)']), Decimal(handicap))
            self.assertEqual(row['Predicted winner'], 'A')
            self.assertEqual(row['Straight up'], 'correct')
            self.assertEqual(row['CORS line coverage'], coverage)
            self.assertEqual(Decimal(row['Absolute error']), Decimal(error))
            self.assertIn('2026-10-18T22:25:51+00:00', page.read_text())
            self.assertNotIn('Unverified publication', page.read_text())
            self.assertIn('Pending score', page.read_text())
            self.assertIn('Missing prediction / preceding rating', page.read_text())
            self.assertIn('1 non-FBS games excluded', page.read_text())
            self.assertIn('1 games</strong>', page.read_text())
        self.assertEqual(preserved, {p: p.read_bytes() for p in preserved})
        validate_performance(self.site)
        spread_result = self.root / 'spread/2020_W7_FBS_spread_results.html'
        spread_result.write_text(spread_result.read_text().replace('-3.25', '-99.25'))
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate_performance(self.site)
        corrected = build_performance_files(self.site, reconstruct_all=True)
        soup = BeautifulSoup(corrected['cfb/years/2020/spread/2020_W7_FBS_spread_results.html'], 'html.parser')
        row = soup.select('tbody tr')[0]
        self.assertIn('-4.50', row.get_text())  # Explicit corrected-history policy uses preceding ratings.
        self.assertNotIn('-3.25', row.get_text())
        write_performance(self.site)
        spread_result.unlink()
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate_performance(self.site)

    def test_default_command_builds_a_navigable_full_site_preview(self):
        self.seed()
        output = Path(self.tmp.name) / 'preview'
        with patch('socket.socket', side_effect=AssertionError('offline only')):
            self.assertEqual(main(['--website', str(self.site), '--output', str(output)]), 0)
        self.assertTrue((output/'cfb/performance/index.html').is_file())
        self.assertEqual((output/'cfb/cfb.html').read_bytes().count(NAV_LINK), 1)
        self.assertEqual((output/self.spread.relative_to(self.site)).read_bytes(), self.spread.read_bytes())

    def test_real_export_binds_performance_and_requires_it_on_receipt_verification(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            candidate, baseline, inputs, firebase, trust = _fixture(root, year=2026, games=(
                SourceGame(0, 'Alpha', 'fbs', 10, 'Beta', 'fbs', 7, False,
                           provider_id='test-game', completed=True, disposition='completed'),))
            public, receipt = root/'public', root/'receipt.json'
            result = validate_and_export(candidate, baseline, inputs, firebase, public, receipt,
                                         source_root=inputs.root, trusted_input_manifest=trust)
            self.assertEqual(result.transform['performance']['schema'], 'cors-performance/v1')
            weekly = 'cfb/years/2026/spread/2026_W0_FBS_spread_results.html'
            self.assertIn(weekly, result.transform['performance']['paths'])
            self.assertIn('Evaluated', (public / weekly).read_text())
            self.assertNotIn('Missing forecast', (public / weekly).read_text())
            verify_local_receipt(receipt, public, firebase, REPOSITORY, trust)
            (public/'cfb/performance/index.html').unlink()
            self.assertFalse(validate_public_output(public, expected_receipt=result).ok)
