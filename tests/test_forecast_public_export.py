"""Derived forecast records cross the unchanged private/public boundary."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb.forecast_record import ForecastContractError
from cfb.forecast_publication import load_verified_forecasts
from cfb.public_safety import assert_public_bytes
from cfb.public_site import validate_and_export, validate_public_output
from cfb.release import build_release
from tests.test_forecast_record import candidate, game, provenance
from tests.test_forecast_publication import forecast_fixture, publish, recorded
from tests.test_public_site import _fixture


class ForecastPublicExportTests(unittest.TestCase):
    def test_public_export_and_immutable_package_preserve_exact_derived_candidate(self):
        item = candidate(game(), "2.24")
        relative = f"cfb/years/2026/forecasts/{item.version_id[7:]}.json"
        raw = json.dumps(item.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        assert_public_bytes(relative, raw)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Seed an inherited derived artifact before building the fixture;
            # preservation and export both cross the real Release boundary.
            def build_with_forecast(*args, **kwargs):
                path = Path(kwargs["published_site"]) / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                return build_release(*args, **kwargs)
            with patch("tests.test_public_site.build_release", side_effect=build_with_forecast):
                site, baseline, inputs, firebase, trust = _fixture(root)
            public = root / "public"
            validate_and_export(site, baseline, inputs, firebase, public, root / "receipt.json",
                                source_root=inputs.root, trusted_input_manifest=trust)
            report = validate_public_output(public)
            self.assertTrue(report.ok, report.failures)
            self.assertEqual((public / relative).read_bytes(), raw)
            fx = forecast_fixture(root / "publication", [item])
            self.assertEqual((fx.prepared.site / relative).read_bytes(), (public / relative).read_bytes())
            _, _, run = publish(fx)
            loaded = load_verified_forecasts(recorded(run), archive=fx.archive,
                                            repository="owner/repository", destination=root / "import")
            self.assertEqual(loaded.candidates, (item,))
            self.assertEqual(loaded.receipts[0].candidate_artifact_digest, item.artifact_digest)

    def test_source_scanner_still_rejects_raw_and_disguised_game_payloads(self):
        raw = {"home_team": "Home", "away_team": "Away", "home_points": 3}
        for value in (raw, {"schema_version": "forecast-candidate/v1", "game": raw},
                      {"schema_version": "forecast-ledger/v1", "payload": raw}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                assert_public_bytes("cfb/years/2026/forecasts/fixture.json", json.dumps(value).encode())

    def test_rank_and_digest_provenance_are_strict(self):
        for rank in (True, 0, -1, 1.5, "1", float("nan"), float("inf")):
            with self.subTest(rank=rank), self.assertRaises(ForecastContractError):
                replace(provenance(), home_rank=rank)
        for digest in ("not-a-digest", "a" * 64, "sha256:" + "G" * 64):
            for field in ("rating_artifact_digest", "source_snapshot_digest"):
                with self.subTest(field=field, digest=digest), self.assertRaises(ForecastContractError):
                    replace(provenance(), **{field: digest})
