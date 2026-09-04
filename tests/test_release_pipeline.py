"""Offline acceptance checks for the staged CFB Release seam."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cfb.release import ReleaseValidationError, build_release, promote_release, validate_release
from cfb.ranking_engine import records_for_week
from cfb.season_snapshot import SeasonSnapshot, SeasonSnapshotService
from cfb.season_source import FixtureSeasonSource, SourceGame, SourceTeam
from cfb.snapshot_cache import SnapshotCache


FIXTURES = Path(__file__).parent / "fixtures" / "cfbd"
FIXED = "2026-09-04T12:00:00+00:00"


class ReleasePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = FixtureSeasonSource(FIXTURES)
        self.service = SeasonSnapshotService(
            self.source,
            SnapshotCache(self.root / "cache"),
            clock=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
        )
        self.snapshot = self.service.get(2025, "FBS")

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, name="release", **kwargs):
        release_id = kwargs.pop("release_id", name)
        return build_release(
            self.snapshot,
            self.root / name,
            release_id=release_id,
            timestamp=FIXED,
            code_revision="test",
            **kwargs,
        )

    def test_build_is_deterministic_and_validates_owned_graph(self):
        first = self.build("first", release_id="same-inputs")
        second = self.build("second", release_id="same-inputs")
        digest = lambda path: {
            file.relative_to(path).as_posix(): file.read_bytes()
            for file in path.rglob("*")
            if file.is_file()
        }
        self.assertEqual(digest(first.site), digest(second.site))
        report = validate_release(first)
        self.assertTrue(report.valid)
        self.assertEqual(report.failures, [])
        self.assertTrue((first.site / "cfb/years/2025/rankings/2025_W1_FBS_cors.html").exists())
        self.assertFalse((first.site / "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html").exists())
        self.assertFalse("Final" in (first.site / "cfb/years/2025/2025_CFB.html").read_text(encoding="utf-8"))

    def test_complete_snapshot_owns_final_and_updates_history(self):
        complete_snapshot = self.service.get(2024, "FBS")
        candidate = build_release(
            complete_snapshot,
            self.root / "complete",
            release_id="complete",
            timestamp=FIXED,
            code_revision="test",
        )
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["season_complete"])
        self.assertTrue((candidate.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html").exists())
        history = (candidate.site / "cfb/history/nc_FBS_CFB_output.html").read_text(encoding="utf-8")
        self.assertIn("2024", history)
        self.assertTrue(validate_release(candidate).valid)

    def test_earlier_checkpoint_of_complete_snapshot_does_not_emit_final(self):
        complete_snapshot = self.service.get(2024, "FBS")
        candidate = build_release(
            complete_snapshot,
            self.root / "checkpoint",
            release_id="checkpoint",
            target_week=0,
            timestamp=FIXED,
            code_revision="test",
        )
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(manifest["season_complete"])
        self.assertFalse((candidate.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html").exists())
        self.assertTrue(validate_release(candidate).valid)

    def test_checkpoint_overlay_preserves_inherited_same_year_final(self):
        complete_snapshot = self.service.get(2024, "FBS")
        base = build_release(
            complete_snapshot,
            self.root / "complete-base",
            release_id="complete-base",
            timestamp=FIXED,
            code_revision="test",
        )
        checkpoint = build_release(
            complete_snapshot,
            self.root / "checkpoint-overlay",
            release_id="checkpoint-overlay",
            target_week=0,
            timestamp=FIXED,
            code_revision="test",
            published_site=base.site,
            clone_published=True,
        )
        final_relative = "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
        self.assertTrue((checkpoint.site / final_relative).exists())
        manifest = json.loads(checkpoint.manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(manifest["season_complete"])
        self.assertNotIn(final_relative, manifest["owned_artifacts"])
        self.assertTrue(validate_release(checkpoint, published_site=base.site).valid)

    def test_legacy_record_win_pct_includes_ties(self):
        teams = tuple(SourceTeam(school, "Legacy") for school in ("Alpha", "Beta", "Gamma"))
        games = (
            SourceGame(0, "Alpha", "FBS", 7, "FCS One", "FCS", 0, False),
            SourceGame(0, "Beta", "FBS", 7, "Alpha", "FBS", 0, False),
            SourceGame(0, "Alpha", "FBS", 3, "Gamma", "FBS", 3, False),
        )
        snapshot = SeasonSnapshot(
            "cfb",
            "FBS",
            1995,
            teams,
            games,
            MappingProxyType({"complete_through_week": 0}),
            "synthetic",
        )
        alpha = next(row for row in records_for_week(snapshot, 0) if row["school"] == "Alpha")
        self.assertEqual(alpha["record"], "1-1-1")
        self.assertEqual(alpha["win_pct"], 0.33)

    def test_legacy_runner_uses_snapshot_derived_end_week(self):
        import cfb.main as legacy_main

        service = MagicMock()
        service.get.return_value = SimpleNamespace(
            games=(SimpleNamespace(week=1), SimpleNamespace(week=4))
        )
        calculation = MagicMock()
        with patch.object(legacy_main, "single_week_calc", calculation):
            legacy_main.run_calculations(
                "single_week",
                2025,
                2,
                0,
                "FBS",
                2,
                0,
                FIXED,
                service,
            )
        self.assertEqual(calculation.call_args.args[2], 4)
        service.get.assert_called_once_with(2025, "FBS")

    def test_legacy_no_argument_entrypoint_requires_explicit_inputs(self):
        import cfb.main as legacy_main

        with patch.object(legacy_main.sys, "argv", ["cfb.main"]):
            self.assertEqual(legacy_main.main(), 2)

    def test_build_and_validate_make_no_followup_source_calls(self):
        before = self.source.call_count
        candidate = self.build()
        self.assertEqual(self.source.call_count, before)
        self.assertTrue(validate_release(candidate).valid)
        self.assertEqual(self.source.call_count, before)

    def test_tamper_blocks_promotion_and_preserves_last_known_good(self):
        candidate = self.build()
        published = self.root / "published"
        promote_release(candidate, published)
        original = {
            file.relative_to(published).as_posix(): file.read_bytes()
            for file in published.rglob("*")
            if file.is_file()
        }
        ranking = candidate.site / "cfb/years/2025/rankings/2025_W1_FBS_cors.html"
        ranking.write_text(ranking.read_text(encoding="utf-8").replace("Last updated:", "Tampered:"), encoding="utf-8")
        with self.assertRaises(ReleaseValidationError):
            promote_release(candidate, published)
        after = {
            file.relative_to(published).as_posix(): file.read_bytes()
            for file in published.rglob("*")
            if file.is_file()
        }
        self.assertEqual(original, after)

    def test_unchanged_promotion_is_explicit_noop(self):
        candidate = self.build()
        published = self.root / "published"
        self.assertTrue(promote_release(candidate, published).changed)
        result = promote_release(candidate, published)
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "unchanged")

    def test_cloned_overlay_preserves_history_and_prior_navigation(self):
        base = self.build("base")
        cloned = self.build("overlay", published_site=base.site, clone_published=True)
        report = validate_release(cloned, published_site=base.site)
        self.assertTrue(report.valid)
        home = (cloned.site / "cfb/cfb.html").read_text(encoding="utf-8")
        self.assertIn("2025_CFB.html", home)
        history = (cloned.site / "cfb/history/nc_FBS_CFB_output.html").read_text(encoding="utf-8")
        self.assertIn("2025", history)

    def test_manifest_is_machine_readable_and_records_snapshot(self):
        candidate = self.build()
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["model_version"], "v0.4.0")
        self.assertEqual(manifest["source_snapshot"], self.snapshot.checksum)
        self.assertIn("artifact_checksums", manifest)
        self.assertIn("manifest_checksum", manifest)

    def test_manifest_owned_artifact_requires_checksum_coverage(self):
        candidate = self.build()
        manifest_path = candidate.manifest_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact_checksums"].pop("cfb/cfb.html")
        unsigned = dict(manifest)
        unsigned.pop("manifest_checksum", None)
        manifest["manifest_checksum"] = hashlib.sha256(
            (json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        report = validate_release(candidate)
        self.assertFalse(report.valid)
        self.assertIn("artifact.checksum_missing", {failure.code for failure in report.failures})

    def test_snapshot_checksum_is_recomputed_from_canonical_contents(self):
        candidate = self.build()
        snapshot_path = candidate.site / "cfb/years/2025/data/snapshot.json"
        snapshot_value = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot_value["teams"][0]["conference"] = "tampered"
        snapshot_path.write_text(
            json.dumps(snapshot_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        manifest_path = candidate.manifest_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact_checksums"]["cfb/years/2025/data/snapshot.json"] = hashlib.sha256(
            snapshot_path.read_bytes()
        ).hexdigest()
        unsigned = dict(manifest)
        unsigned.pop("manifest_checksum", None)
        manifest["manifest_checksum"] = hashlib.sha256(
            (json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        report = validate_release(candidate)
        self.assertFalse(report.valid)
        self.assertIn("snapshot.checksum", {failure.code for failure in report.failures})


if __name__ == "__main__":
    unittest.main()
