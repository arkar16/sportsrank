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

from bs4 import BeautifulSoup

from cfb.release import ReleaseValidationError, build_release, grade_ats, promote_release, validate_release
from cfb.ranking_engine import PreviousFinal, records_for_week
from cfb.season_snapshot import SeasonSnapshot, SeasonSnapshotService, _checksum as snapshot_checksum
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
        self.base = self.root / "published"
        self.base.mkdir()
        (self.base / "index.html").write_text("legacy published bytes", encoding="utf-8")
        for snapshot in (self.snapshot, self.service.get(2024, "FBS")):
            prior_year = snapshot.year - 1
            path = self.base / f"cfb/years/{prior_year}/rankings/{prior_year}_FINAL_FBS_cors.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = "".join(
                f"<tr><td>{team.school}</td><td>{float(index)}</td><td>0.0</td></tr>"
                for index, team in enumerate(snapshot.teams)
            )
            path.write_text(
                f"<html><body><table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead><tbody>{rows}</tbody></table></body></html>",
                encoding="utf-8",
            )

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, name="release", **kwargs):
        release_id = kwargs.pop("release_id", name)
        snapshot = kwargs.pop("snapshot", self.snapshot)
        prior = PreviousFinal(
            {team.school: float(index) for index, team in enumerate(snapshot.teams)},
            {team.school: 0.0 for team in snapshot.teams},
        )
        return build_release(
            snapshot,
            self.root / name,
            release_id=release_id,
            timestamp=FIXED,
            code_revision="test",
            published_site=kwargs.pop("published_site", self.base),
            previous_final=kwargs.pop("previous_final", prior),
            phase=kwargs.pop("phase", "week"),
            **kwargs,
        )

    def reseal(self, candidate):
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        for relative in manifest["artifact_checksums"]:
            manifest["artifact_checksums"][relative] = hashlib.sha256((candidate.site / relative).read_bytes()).hexdigest()
        manifest.pop("manifest_checksum", None)
        manifest["manifest_checksum"] = hashlib.sha256(
            (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        ).hexdigest()
        candidate.manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")

    def tamper_cell(self, candidate, relative, column, value):
        path = candidate.site / relative
        document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
        document.select_one("tbody tr").find_all("td")[headers.index(column)].string = str(value)
        path.write_text(str(document), encoding="utf-8")
        self.reseal(candidate)

    def test_build_is_deterministic_and_validates_owned_graph(self):
        first = self.build("first", release_id="same-inputs")
        second = self.build("second", release_id="same-inputs")
        digest = lambda path: {
            file.relative_to(path).as_posix(): file.read_bytes()
            for file in path.rglob("*")
            if file.is_file()
        }
        self.assertEqual(digest(first.site), digest(second.site))
        report = validate_release(first, published_site=self.base)
        self.assertTrue(report.valid)
        self.assertEqual(report.failures, [])
        self.assertTrue((first.site / "cfb/years/2025/rankings/2025_W1_FBS_cors.html").exists())
        self.assertFalse((first.site / "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html").exists())
        self.assertFalse("Final" in (first.site / "cfb/years/2025/2025_CFB.html").read_text(encoding="utf-8"))

    def test_release_requires_base_and_refuses_existing_directory(self):
        with self.assertRaisesRegex(ValueError, "published_site is required"):
            build_release(self.snapshot, self.root / "missing-base", release_id="missing-base")
        occupied = self.root / "occupied"
        occupied.mkdir()
        with self.assertRaises(FileExistsError):
            self.build("occupied")

    def test_release_id_is_required_canonical_and_bound_to_candidate_root(self):
        attacks = (("missing", None), ("escaping", "../escape"), ("forged", "forged-id"))
        for name, value in attacks:
            with self.subTest(name=name):
                candidate = self.build(f"release-id-{name}")
                manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
                release_metadata = json.loads(candidate.metadata_path.read_text(encoding="utf-8"))
                if value is None:
                    manifest.pop("release_id")
                    release_metadata.pop("release_id")
                else:
                    manifest["release_id"] = value
                    release_metadata["release_id"] = value
                candidate.metadata_path.write_text(json.dumps(release_metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
                candidate.manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
                self.reseal(candidate)
                report = validate_release(candidate, published_site=self.base)
                self.assertFalse(report.valid)
                self.assertTrue({failure.code for failure in report.failures} & {"metadata.field", "release.id"})

    def test_preseason_owns_distinct_ranking_and_week_zero_forecast_only(self):
        candidate = self.build("preseason", phase="preseason")
        root = candidate.site / "cfb/years/2025"
        self.assertTrue((root / "rankings/2025_PRESEASON_FBS_cors.html").exists())
        self.assertTrue((root / "data/slate/weekly_slate/2025_W0_FBS_slate.html").exists())
        self.assertTrue((root / "spread/2025_W0_FBS_spread.html").exists())
        self.assertFalse((root / "rankings/2025_W0_FBS_cors.html").exists())
        self.assertFalse((root / "data/results/weekly_results/2025_W0_FBS_results.html").exists())
        self.assertTrue(validate_release(candidate, published_site=self.base).valid)

    def test_resealed_manifest_cannot_hide_deleted_derived_artifact(self):
        candidate = self.build("deleted")
        victim = "cfb/years/2025/rankings/2025_W1_FBS_cors.html"
        (candidate.site / victim).unlink()
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        manifest["owned_artifacts"].remove(victim)
        manifest["required_artifacts"].remove(victim)
        manifest["artifact_checksums"].pop(victim)
        manifest.pop("manifest_checksum")
        manifest["manifest_checksum"] = hashlib.sha256(
            (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        ).hexdigest()
        candidate.manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        report = validate_release(candidate, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertIn("artifact.required", {failure.code for failure in report.failures})

    def test_overlay_rejects_inherited_mutation_and_unowned_new_path(self):
        candidate = self.build("unowned")
        (candidate.site / "index.html").write_text("mutated", encoding="utf-8")
        (candidate.site / "surprise.txt").write_text("new", encoding="utf-8")
        report = validate_release(candidate, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertEqual(
            {failure.path for failure in report.failures if failure.code == "overlay.unowned"},
            {str(candidate.site / "index.html"), str(candidate.site / "surprise.txt")},
        )

    def test_ats_grading_preserves_side_orientation_pushes_and_missing_lines(self):
        cases = (
            (dict(home_team="H", away_team="A", home_points=31, away_points=20, home_margin_line=7), ("H", "home", True)),
            (dict(home_team="H", away_team="A", home_points=20, away_points=17, home_margin_line=7), ("H", "away", False)),
            (dict(home_team="H", away_team="A", home_points=20, away_points=30, home_margin_line=-7), ("A", "away", True)),
            (dict(home_team="H", away_team="A", home_points=24, away_points=17, home_margin_line=7), ("H", "push", None)),
            (dict(home_team="H", away_team="A", home_points=17, away_points=17, home_margin_line=0), (None, "push", None)),
            (dict(home_team="H", away_team="A", home_points=17, away_points=14, home_margin_line=None), (None, "ungraded", None)),
        )
        for inputs, expected in cases:
            with self.subTest(inputs=inputs):
                result = grade_ats(**inputs)
                self.assertEqual((result["favorite"], result["ats_result"], result["ats_correct"]), expected)

    def test_resealed_field_tampering_is_rejected_independently(self):
        attacks = (
            ("records", "cfb/years/2025/data/records/2025_W1_FBS_records.html", "win_pct", "NaN", "records.reconcile"),
            ("results", "cfb/years/2025/data/results/weekly_results/2025_W1_FBS_results.html", "home_score", 999, "results.reconcile"),
            ("spread", "cfb/years/2025/spread/2025_W1_FBS_spread.html", "spread_value", "NaN", "spread.reconcile"),
            ("spread-result", "cfb/years/2025/spread/2025_W1_FBS_spread_results.html", "actual_margin", 999, "spread_result.reconcile"),
        )
        for name, relative, column, value, expected_code in attacks:
            with self.subTest(name=name):
                candidate = self.build(f"attack-{name}")
                self.tamper_cell(candidate, relative, column, value)
                report = validate_release(candidate, published_site=self.base)
                self.assertFalse(report.valid)
                self.assertIn(expected_code, {failure.code for failure in report.failures})

    def test_resealed_wrong_final_history_outcome_is_rejected(self):
        snapshot = self.service.get(2024, "FBS")
        candidate = build_release(snapshot, self.root / "history-attack", release_id="history-attack", phase="final", timestamp=FIXED, code_revision="test", published_site=self.base)
        self.tamper_cell(candidate, "cfb/history/nc_FBS_CFB_output.html", "school", "Wrong Champion")
        report = validate_release(candidate, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertIn("history.outcome", {failure.code for failure in report.failures})

    def test_complete_snapshot_owns_final_and_updates_history(self):
        complete_snapshot = self.service.get(2024, "FBS")
        candidate = build_release(
            complete_snapshot,
            self.root / "complete",
            release_id="complete",
            timestamp=FIXED,
            code_revision="test",
            published_site=self.base,
            phase="final",
        )
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["season_complete"])
        self.assertTrue((candidate.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html").exists())
        history = (candidate.site / "cfb/history/nc_FBS_CFB_output.html").read_text(encoding="utf-8")
        self.assertIn("2024", history)
        self.assertTrue(validate_release(candidate, published_site=self.base).valid)

    def test_earlier_checkpoint_of_complete_snapshot_does_not_emit_final(self):
        complete_snapshot = self.service.get(2024, "FBS")
        candidate = build_release(
            complete_snapshot,
            self.root / "checkpoint",
            release_id="checkpoint",
            target_week=0,
            timestamp=FIXED,
            code_revision="test",
            published_site=self.base,
            phase="week",
        )
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(manifest["season_complete"])
        self.assertTrue((candidate.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html").exists())
        self.assertNotIn(
            "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html",
            json.loads(candidate.manifest_path.read_text(encoding="utf-8"))["owned_artifacts"],
        )
        self.assertTrue(validate_release(candidate, published_site=self.base).valid)

    def test_checkpoint_overlay_preserves_inherited_same_year_final(self):
        complete_snapshot = self.service.get(2024, "FBS")
        base = build_release(
            complete_snapshot,
            self.root / "complete-base",
            release_id="complete-base",
            timestamp=FIXED,
            code_revision="test",
            published_site=self.base,
            phase="final",
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
            phase="week",
        )
        final_relative = "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
        self.assertTrue((checkpoint.site / final_relative).exists())
        manifest = json.loads(checkpoint.manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(manifest["season_complete"])
        self.assertIn(final_relative, manifest["owned_artifacts"])
        report = validate_release(checkpoint, published_site=base.site)
        self.assertFalse(report.valid)
        self.assertIn("runs.duplicate", {failure.code for failure in report.failures})

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
        self.assertTrue(validate_release(candidate, published_site=self.base).valid)
        self.assertEqual(self.source.call_count, before)

    def test_tamper_blocks_promotion_and_preserves_last_known_good(self):
        candidate = self.build()
        published = self.base
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
        published = self.base
        self.assertTrue(promote_release(candidate, published).changed)
        result = promote_release(candidate, published)
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "unchanged")

    def test_cumulative_chain_rejects_duplicate_season_run(self):
        base = self.build("base")
        cloned = self.build("overlay", published_site=base.site, clone_published=True)
        report = validate_release(cloned, published_site=base.site)
        self.assertFalse(report.valid)
        self.assertIn("runs.duplicate", {failure.code for failure in report.failures})

    def test_cumulative_chain_validates_directly_against_original_base(self):
        snapshot_2024 = self.service.get(2024, "FBS")
        first = build_release(
            snapshot_2024,
            self.root / "chain-2024",
            release_id="chain-2024",
            phase="final",
            timestamp=FIXED,
            code_revision="test",
            published_site=self.base,
        )
        metadata = MappingProxyType({
            "schema_version": 2,
            "teams_fetched_at": FIXED,
            "games_fetched_at": FIXED,
            "complete_through_week": -1,
        })
        state = {
            "schema_version": 2,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2025,
            "teams_fetched_at": FIXED,
            "games_fetched_at": FIXED,
            "complete_through_week": -1,
        }
        snapshot_2025 = SeasonSnapshot("cfb", "FBS", 2025, snapshot_2024.teams, (), metadata, snapshot_checksum(state, snapshot_2024.teams, ()))
        second = build_release(
            snapshot_2025,
            self.root / "chain-2025",
            release_id="chain-2025",
            phase="preseason",
            timestamp=FIXED,
            code_revision="test",
            published_site=first.site,
        )
        manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual([run["season"] for run in manifest["runs"]], [2024, 2025])
        report = validate_release(second, published_site=self.base)
        self.assertTrue(report.valid, [str(failure) for failure in report.failures])

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
        report = validate_release(candidate, published_site=self.base)
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
        report = validate_release(candidate, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertIn("snapshot.checksum", {failure.code for failure in report.failures})


if __name__ == "__main__":
    unittest.main()
