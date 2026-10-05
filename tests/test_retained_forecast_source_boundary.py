"""Retained HTML must constrain even internally consistent generated releases."""

from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb.forecast_release import load_retained_spread_forecasts
from cfb.release import build_release, validate_release
from tests.test_forecast_lifecycle_independent import (
    _downgrade_to_synthetic_legacy_v2,
    _prepare_three_team_base,
    _resign_manifest,
    _synthetic_upgrade_snapshot,
)


class RetainedForecastSourceBoundaryTests(unittest.TestCase):
    def test_release_rejects_resealed_margin_with_unchanged_source_pages(self):
        snapshot = _synthetic_upgrade_snapshot(provider_ids=True, week_one_completed=False)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base"
            previous = _prepare_three_team_base(base)
            legacy = build_release(
                snapshot, root / "legacy", release_id="legacy",
                target_week=0, phase="week", previous_final=previous,
                timestamp="2026-09-01T20:00:00+00:00", published_site=base,
            )
            legacy_display = build_release(
                _synthetic_upgrade_snapshot(provider_ids=False, week_one_completed=False),
                root / "legacy-display", release_id="legacy-display",
                target_week=0, phase="week", previous_final=previous,
                timestamp="2026-09-01T20:00:00+00:00", published_site=base,
            )
            for week in (0, 1):
                relative = f"cfb/years/2026/spread/2026_W{week}_FBS_spread.html"
                (legacy.site / relative).write_bytes((legacy_display.site / relative).read_bytes())
            _downgrade_to_synthetic_legacy_v2(legacy)
            # Literal contract 2 has no issued-ledger state to inherit.
            (legacy.site / "cfb/years/2026/forecasts/ledger.json").unlink()
            legacy_manifest = json.loads(legacy.manifest_path.read_bytes())
            for field in ("forecast_contract", "forecast_ledger_path", "forecast_evaluation_path"):
                legacy_manifest.pop(field, None)
            _resign_manifest(legacy, legacy_manifest)
            page = "cfb/years/2026/spread/2026_W1_FBS_spread.html"
            retained_bytes = (legacy.site / page).read_bytes()

            def build(name):
                return build_release(
                    snapshot, root / name, release_id=name,
                    target_week=0, phase="week", previous_final=previous,
                    timestamp="2026-09-02T20:00:00+00:00", published_site=legacy.site,
                )

            valid = build("valid")
            report = validate_release(valid, published_site=legacy.site)
            self.assertTrue(report.valid, [str(item) for item in report.failures])
            ledger_path = "cfb/years/2026/forecasts/ledger.json"
            ledger = json.loads((valid.site / ledger_path).read_bytes())
            self.assertGreater(len(ledger["owner_attestations"]), 0)

            def corrupt_import(*args, **kwargs):
                candidates, attestations = load_retained_spread_forecasts(*args, **kwargs)
                original = next(item for item in candidates if item.game.week == 1)
                margin = original.home_margin + Decimal("100")
                altered = type(original).create(
                    game=original.game, home_margin=margin,
                    precision=original.precision, provenance=original.provenance,
                )
                return (
                    tuple(altered if item == original else item for item in candidates),
                    tuple(replace(
                        item, candidate_version_id=altered.version_id,
                        candidate_artifact_digest=altered.artifact_digest,
                        attestation_id="",
                    ) if item.candidate_version_id == original.version_id else item
                          for item in attestations),
                )

            # Mutate only the builder's input. It then computes all candidate IDs,
            # attestations, metrics, HTML and manifest hashes from the forgery.
            # The real validator subsequently reparses the unmodified source.
            with patch("cfb.release.load_retained_spread_forecasts", side_effect=corrupt_import):
                forged = build("forged")
            self.assertEqual((forged.site / page).read_bytes(), retained_bytes)
            forged_report = validate_release(forged, published_site=legacy.site)
            self.assertFalse(forged_report.valid)
            self.assertTrue(any(
                item.code == "forecast.contract" and "reconstructed retained source" in item.message
                for item in forged_report.failures
            ), [str(item) for item in forged_report.failures])


if __name__ == "__main__":
    unittest.main()
