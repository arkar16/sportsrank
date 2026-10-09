"""Independent saved-spread arithmetic and public integration regressions."""
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from cfb.performance import backforecast, write_performance, validate_performance, NAV_LINK
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
            candidate, baseline, inputs, firebase, trust = _fixture(root)
            public, receipt = root/'public', root/'receipt.json'
            result = validate_and_export(candidate, baseline, inputs, firebase, public, receipt,
                                         source_root=inputs.root, trusted_input_manifest=trust)
            self.assertEqual(result.transform['performance']['schema'], 'cors-performance/v1')
            verify_local_receipt(receipt, public, firebase, REPOSITORY, trust)
            (public/'cfb/performance/index.html').unlink()
            self.assertFalse(validate_public_output(public, expected_receipt=result).ok)
