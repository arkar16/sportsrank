"""Independent correction acceptance and rejection at the export boundary."""
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from cfb.historical_correction import (
    CorrectionInputs, PUBLIC_PATH, apply_correction, digest, validate_reconstruction,
    validate_public_correction,
)
from cfb.historical_rebuild import rebuild_history
from tests import test_historical_rebuild as fixtures


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HistoricalRebuildTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.temp.cleanup)
        self.fixture.sample()
        self.root, self.original = self.fixture.root, self.fixture.site
        self.request = CorrectionInputs(self.fixture.source, digest(self.fixture.source.read_bytes()))
        self.output = self.root / 'corrected'
        progression = self.original / 'cfb/years/1901/history/1901_FBS_progression.html'
        progression.parent.mkdir(parents=True)
        progression.write_text('old progression')

    def rebuild(self):
        rebuild_history(self.original, self.request.games_csv, self.output)

    def test_validated_transform_binds_csv_and_corrected_paths_preserving_sources(self):
        before = {p.relative_to(self.original): p.read_bytes() for p in self.original.rglob('*') if p.is_file()}
        with patch('socket.socket', side_effect=AssertionError('offline required')):
            binding = apply_correction(self.original, self.output, self.request)
        document = validate_public_correction(self.output, binding)
        self.assertEqual(document['added_postseason_games'], 1)
        self.assertEqual((document['from_season'], document['through_season']), (1901, 1902))
        self.assertEqual(document['source_csv_sha256'], self.request.sha256)
        self.assertFalse((self.output / 'historical-review.json').exists())
        self.assertIn('cfb/years/1901/rankings/1901_W16_FBS_cors.html', binding['paths'])
        self.assertNotIn('cfb/years/1901/spread/1901_W1_FBS_spread.html', binding['paths'])
        self.assertEqual({p.relative_to(self.original): p.read_bytes() for p in self.original.rglob('*') if p.is_file()}, before)
        changed = self.output / 'cfb/years/1901/rankings/1901_FINAL_FBS_cors.html'
        changed.write_bytes(changed.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'artifact differs'):
            validate_public_correction(self.output, binding)

    def test_score_rank_record_and_reconstructed_prediction_tampering_rejected(self):
        self.rebuild()
        for relative, old, new in [
            ('data/results/weekly_results/1901_W16_FBS_results.html', '<td>21</td>', '<td>20</td>'),
            ('rankings/1901_PRESEASON_FBS_cors.html', '<td>18.25</td>', '<td>18.26</td>'),
            ('reconstructed/1901_W1_FBS_spread.html', '<td>A</td>', '<td>Wrong</td>'),
            ('data/records/1901_W1_FBS_records.html', '<td>A</td>', '<td>Wrong</td>'),
        ]:
            with self.subTest(relative=relative):
                path = self.output / 'cfb/years/1901' / relative
                raw = path.read_text()
                self.assertIn(old, raw)
                path.write_text(raw.replace(old, new, 1))
                with self.assertRaisesRegex(ValueError, 'differs'):
                    validate_reconstruction(self.original, self.output, self.request)
                path.write_text(raw)

    def test_omitted_postseason_page_and_invented_artifacts_rejected(self):
        self.rebuild()
        path = self.output / 'cfb/years/1901/data/results/weekly_results/1901_W16_FBS_results.html'
        raw = path.read_bytes()
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            validate_reconstruction(self.original, self.output, self.request)
        path.write_bytes(raw)
        (self.output / 'invented.html').write_text('not in the derived graph')
        with self.assertRaisesRegex(ValueError, 'invented artifact'):
            validate_reconstruction(self.original, self.output, self.request)

    def test_original_forecast_and_unrelated_navigation_are_protected(self):
        self.rebuild()
        path = self.output / 'cfb/years/1901/spread/1901_W1_FBS_spread.html'
        path.write_text(path.read_text().replace('-99', '-98'))
        with self.assertRaisesRegex(ValueError, 'preserved bytes'):
            validate_reconstruction(self.original, self.output, self.request)

    def test_progression_html_and_json_are_both_numerically_checked(self):
        self.rebuild()
        base = self.output / 'cfb/years/1901/history/1901_FBS_progression'
        html = base.with_suffix('.html')
        raw = html.read_text()
        self.assertIn('18.25', raw)
        html.write_text(raw.replace('18.25', '18.26'))
        with self.assertRaisesRegex(ValueError, 'HTML values differ'):
            validate_reconstruction(self.original, self.output, self.request)
        html.write_text(raw)
        path = base.with_suffix('.json')
        doc = json.loads(path.read_bytes())
        doc['rows'][0]['checkpoints']['PRESEASON']['points'] += 1
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, 'progression differs'):
            validate_reconstruction(self.original, self.output, self.request)

    def test_csv_pin_and_preview_evidence_cannot_be_replaced(self):
        self.rebuild()
        with self.assertRaisesRegex(ValueError, 'pinned identity'):
            validate_reconstruction(self.original, self.output, CorrectionInputs(self.fixture.source, '0' * 64))
        path = self.output / 'historical-review.json'
        doc = json.loads(path.read_bytes())
        doc['added_postseason_games'] = 0
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, 'evidence differs'):
            validate_reconstruction(self.original, self.output, self.request)

    def test_normal_export_issues_and_rechecks_correction_receipt(self):
        from cfb.public_site import validate_and_export, verify_local_receipt, validate_public_output
        from cfb.release import build_release
        from cfb.season_snapshot import SeasonSnapshot
        from cfb.season_source import SourceTeam
        from tests.test_public_site import _fixture, REPOSITORY
        fixture_root = self.root / 'export'
        fixture_root.mkdir()
        _, baseline, inputs, firebase, trust = _fixture(fixture_root)
        shutil.copytree(self.original, baseline, dirs_exist_ok=True)
        raw = json.loads(inputs.resolve(1897).source_path.read_bytes())
        snapshot = SeasonSnapshot('cfb', 'FBS', 1897,
            tuple(SourceTeam(row['school'], row['conference']) for row in raw['teams']), (),
            {key: value for key, value in raw.items() if key not in {'sport', 'classification', 'year', 'teams', 'games', 'checksum'}}, raw['checksum'])
        candidate = build_release(snapshot, fixture_root / 'with-history', release_id='with-history', phase='preseason',
                                  published_site=baseline, source_inputs=inputs, timestamp='2026-09-01T00:00:00+00:00')
        receipt_path = fixture_root / 'receipt.json'
        with patch('socket.socket', side_effect=AssertionError('offline required')):
            receipt = validate_and_export(candidate, baseline, inputs, firebase, self.output, receipt_path,
                trusted_input_manifest=trust, code_root=REPOSITORY, historical_correction=self.request)
            verified = verify_local_receipt(receipt_path, self.output, firebase, REPOSITORY, trust)
        self.assertEqual(receipt.digest, verified.digest)
        self.assertEqual(receipt.used_coverage['status'], 'none')
        self.assertIn('historical_correction', receipt.transform)
        self.assertTrue(validate_public_output(self.output, expected_receipt=receipt).ok)
        # An ordinary receipt cannot be repurposed by dropping the correction.
        from dataclasses import replace
        ordinary = replace(receipt, transform={k: v for k, v in receipt.transform.items() if k != 'historical_correction'})
        self.assertFalse(validate_public_output(self.output, expected_receipt=ordinary).ok)

    def test_corrected_2025_coverage_retains_original_and_binds_new_carryover(self):
        from dataclasses import asdict, replace
        from cfb.historical_rebuild import HistoricalInputs
        from cfb.public_site import validate_and_export, verify_local_receipt, validate_public_output, LocalValidationReceipt
        from cfb.recovery_inputs import RecoveryInputBundle
        from cfb.release import build_release
        from tests.test_progression_contract_independent import _source_snapshot
        from tests.test_public_site import REPOSITORY
        shutil.rmtree(self.original)
        snapshot = _source_snapshot(2)
        names = [team.school for team in snapshot.teams]
        for year in (2022, 2023, 2024):
            self.fixture.season(year, names, [self.fixture.result(home=names[0], away=names[1])])
        self.fixture.csv([
            self.fixture.game(year=2023, day='2023-09-02', home_team=names[0], away_team=names[1]),
            self.fixture.game(year=2023, identifier='bowl', phase='postseason', day='2024-01-01',
                              home_team=names[0], away_team=names[1], home_points=21),
        ])
        request = CorrectionInputs(self.fixture.source, digest(self.fixture.source.read_bytes()))
        source = self.root / 'source'
        (source / 'snapshots').mkdir(parents=True)
        raw = {**snapshot.metadata, 'teams': [asdict(team) for team in snapshot.teams],
               'games': [asdict(game) for game in snapshot.games], 'checksum': snapshot.checksum}
        source_bytes = json.dumps(raw).encode()
        (source / 'snapshots/cfb-fbs-2025.json').write_bytes(source_bytes)
        trust = source / 'inputs-manifest.json'
        trust.write_text(json.dumps({'files': [{'path': 'snapshots/cfb-fbs-2025.json',
                                               'sha256': digest(source_bytes), 'bytes': len(source_bytes)}]}))
        inputs = RecoveryInputBundle.from_directory(source, trusted_file_sha256={'snapshots/cfb-fbs-2025.json': digest(source_bytes)})
        previous = HistoricalInputs(self.original, request.games_csv).prior_final(2024)
        candidate = build_release(snapshot, self.root / 'modern', release_id='modern', phase='final', target_week=2,
            previous_final=previous, source_inputs=inputs, published_site=self.original,
            timestamp='2025-12-08T12:00:00+00:00')
        firebase = self.root / 'firebase.json'
        firebase.write_text('{"hosting":{"public":"website"}}')
        receipt_path = self.root / 'receipt.json'
        receipt = validate_and_export(candidate, self.original, inputs, firebase, self.output, receipt_path,
            trusted_input_manifest=trust, code_root=REPOSITORY, historical_correction=request)
        self.assertEqual(receipt.used_coverage['schema'], 'ranking-progression/used-coverage/v2')
        self.assertEqual(receipt.used_coverage['original']['status'], 'required')
        self.assertEqual(verify_local_receipt(receipt_path, self.output, firebase, REPOSITORY, trust).digest, receipt.digest)
        ordinary = replace(receipt, used_coverage=receipt.used_coverage['original'])
        self.assertFalse(validate_public_output(self.output, expected_receipt=ordinary).ok)
        raw = receipt.to_dict()
        raw['used_coverage']['carryover_identity'] = 'f' * 64
        bad = LocalValidationReceipt.from_bytes((json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode())
        self.assertFalse(validate_public_output(self.output, expected_receipt=bad).ok)


class PreparationTests(unittest.TestCase):
    def test_one_config_selects_pinned_inputs_and_new_output(self):
        import tempfile
        from scripts.prepare_site import export_arguments, SCHEMA, REQUIRED, PATH_FIELDS
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'schema': SCHEMA, **{key: 'input' if key in PATH_FIELDS else 'a' * 64 for key in REQUIRED}}
            path = root / 'prepare.json'
            path.write_text(json.dumps(config))
            args = export_arguments(path, root / 'prepared')
            self.assertIn('--historical-games-sha256', args)
            self.assertIn('--market-inputs', args)
            self.assertEqual(args[args.index('--historical-games-csv') + 1], str((root / 'input').resolve()))
            config['CFBD_API'] = 'not-a-real-token'
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, 'unknown fields'):
                export_arguments(path, root / 'prepared')

    def test_preparation_reuses_github_login_without_retaining_credential(self):
        import io
        import os
        import tempfile
        from contextlib import redirect_stdout
        from scripts.prepare_site import main, SCHEMA, REQUIRED, PATH_FIELDS
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'schema': SCHEMA, **{key: 'input' if key in PATH_FIELDS else 'a' * 64 for key in REQUIRED},
                      'successor_baseline': 'successor.json'}
            path = root / 'prepare.json'
            path.write_text(json.dumps(config))
            captured = io.StringIO()
            def exporter(args):
                self.assertEqual(os.environ['GITHUB_TOKEN'], 'synthetic-test-secret')
                self.assertNotIn('synthetic-test-secret', args)
                return 0
            with patch.dict(os.environ, {}, clear=True), patch('scripts.prepare_site.subprocess.check_output', return_value='synthetic-test-secret\n') as auth, patch('scripts.prepare_site.export_main', side_effect=exporter), redirect_stdout(captured):
                self.assertEqual(main(['--config', str(path), '--output', str(root / 'prepared')]), 0)
                self.assertNotIn('GITHUB_TOKEN', os.environ)
                auth.assert_called_once()
            self.assertNotIn('synthetic-test-secret', captured.getvalue())


if __name__ == '__main__':
    unittest.main()
