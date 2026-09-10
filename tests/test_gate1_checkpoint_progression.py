"""Gate 1 regressions for same-season checkpoints and Week 0 ATS output."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
import tempfile
import unittest

from bs4 import BeautifulSoup

from cfb.release import build_release, grade_ats, validate_release
from cfb.ranking_engine import PreviousFinal
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam


STAMP = "2026-09-08T12:00:00+00:00"


def _season_snapshot(*, year: int, complete_through_week: int, w0_score=None, w1_score=None) -> SeasonSnapshot:
    teams = (
        SourceTeam("Alpha State", "Test"),
        SourceTeam("Beta Tech", "Test"),
    )
    w0_home, w0_away = w0_score or (None, None)
    w1_home, w1_away = w1_score or (None, None)
    games = (
        SourceGame(
            0,
            "Alpha State",
            "fbs",
            w0_home,
            "Beta Tech",
            "fbs",
            w0_away,
            False,
            provider_id=f"{year}-w0",
            completed=w0_score is not None,
            disposition="completed" if w0_score is not None else "scheduled",
        ),
        SourceGame(
            1,
            "Beta Tech",
            "fbs",
            w1_home,
            "Alpha State",
            "fbs",
            w1_away,
            False,
            provider_id=f"{year}-w1",
            completed=w1_score is not None,
            disposition="completed" if w1_score is not None else "scheduled",
        ),
    )
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": year,
            "teams_fetched_at": STAMP,
            "games_fetched_at": STAMP,
            "complete_through_week": complete_through_week,
        }
    )
    return SeasonSnapshot(
        "cfb",
        "FBS",
        year,
        teams,
        games,
        metadata,
        _checksum(metadata, teams, games),
    )


def _snapshot(*, complete_through_week: int, w0_score=None, w1_score=None) -> SeasonSnapshot:
    return _season_snapshot(
        year=2025,
        complete_through_week=complete_through_week,
        w0_score=w0_score,
        w1_score=w1_score,
    )


def _base(root: Path, *, prior_year: int = 2024) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("legacy published bytes", encoding="utf-8")
    path = base / f"cfb/years/{prior_year}/rankings/{prior_year}_FINAL_FBS_cors.html"
    path.parent.mkdir(parents=True)
    path.write_text(
        "<html><body><table><thead><tr>"
        "<th>school</th><th>cors</th><th>wins_vs_expected</th>"
        "</tr></thead><tbody>"
        "<tr><td>Alpha State</td><td>10</td><td>0</td></tr>"
        "<tr><td>Beta Tech</td><td>8</td><td>0</td></tr>"
        "</tbody></table></body></html>",
        encoding="utf-8",
    )
    return base


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _reseal_manifest(candidate) -> None:
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    for relative in manifest["artifact_checksums"]:
        manifest["artifact_checksums"][relative] = hashlib.sha256(
            (candidate.site / relative).read_bytes()
        ).hexdigest()
    manifest.pop("manifest_checksum", None)
    manifest["manifest_checksum"] = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
    candidate.manifest_path.write_text(_canonical_json(manifest), encoding="utf-8")


def _write_history(base: Path, *, year: int) -> None:
    for filename, school, kind in (
        ("nc_FBS_CFB_output.html", "Alpha State", "National champion"),
        ("wt_FBS_CFB_output.html", "Beta Tech", "Worst team"),
    ):
        for history_root in (base / "cfb" / "history", base / "cfb" / "years" / "history"):
            path = history_root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "<html><body><table><thead><tr>"
                "<th>year</th><th>school</th><th>conference</th><th>record</th><th>cors</th><th>kind</th>"
                f"</tr></thead><tbody><tr><td>{year}</td><td>{school}</td><td>Test</td>"
                f"<td>1-0</td><td>10.0</td><td>{kind}</td></tr></tbody></table></body></html>",
                encoding="utf-8",
            )


def _write_legacy_history(base: Path, *, year: int) -> None:
    """Write the published site's title/uppercase history schema."""

    for filename, school, kind, win_pct, cors in (
        ("nc_FBS_CFB_output.html", "Alpha State", "National champion", "1.0", "10.0"),
        ("wt_FBS_CFB_output.html", "Beta Tech", "Worst team", "0.0", "8.0"),
    ):
        for history_root in (base / "cfb" / "history", base / "cfb" / "years" / "history"):
            path = history_root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "<html><body><table><thead><tr>"
                "<th>Year</th><th>School</th><th>Conference</th><th>Record</th>"
                "<th>Win%</th><th>CORS</th>"
                f"</tr></thead><tbody><tr><td>{year}</td><td>{school}</td><td>Test</td>"
                f"<td>1-0</td><td>{win_pct}</td><td>{cors}</td></tr></tbody></table></body></html>",
                encoding="utf-8",
            )


class Gate1CheckpointProgressionTests(unittest.TestCase):
    def test_preseason_w0_w1_final_progression_preserves_history_and_latest_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root)
            prior = PreviousFinal(
                {"Alpha State": 10.0, "Beta Tech": 8.0},
                {"Alpha State": 0.0, "Beta Tech": 0.0},
                year=2024,
                classification="FBS",
            )
            preseason = build_release(
                _snapshot(complete_through_week=-1),
                root / "preseason",
                release_id="preseason",
                phase="preseason",
                previous_final=prior,
                published_site=base,
                timestamp=STAMP,
            )
            w0 = build_release(
                _snapshot(complete_through_week=0, w0_score=(31, 20)),
                root / "w0",
                release_id="w0",
                phase="week",
                target_week=0,
                previous_final=prior,
                published_site=preseason.site,
                timestamp=STAMP,
            )
            w1 = build_release(
                _snapshot(complete_through_week=1, w0_score=(31, 20), w1_score=(17, 10)),
                root / "w1",
                release_id="w1",
                phase="week",
                target_week=1,
                previous_final=prior,
                published_site=w0.site,
                timestamp=STAMP,
            )
            final = build_release(
                _snapshot(complete_through_week=1, w0_score=(31, 20), w1_score=(17, 10)),
                root / "final",
                release_id="final",
                phase="final",
                target_week=1,
                previous_final=prior,
                published_site=w1.site,
                timestamp=STAMP,
            )

            report = validate_release(final, published_site=base)

            self.assertTrue(report.valid, [str(failure) for failure in report.failures])
            manifest = json.loads(final.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [(run["season"], run["phase"], run["target_week"]) for run in manifest["runs"]],
                [(2025, "preseason", -1), (2025, "week", 0), (2025, "week", 1), (2025, "final", 1)],
            )
            archives = [run["snapshot_archive_path"] for run in manifest["runs"]]
            self.assertTrue(all((final.site / archive).is_file() for archive in archives))
            self.assertEqual(
                len(set(archives)),
                len({run["source_snapshot"] for run in manifest["runs"]}),
            )
            self.assertEqual(
                [run["snapshot_archive_checksum"] for run in manifest["runs"]],
                [run["source_snapshot"] for run in manifest["runs"]],
            )
            self.assertEqual((final.site / "index.html").read_text(encoding="utf-8"), "legacy published bytes")
            results = final.site / "cfb/years/2025/data/results/weekly_results/2025_W1_FBS_results.html"
            self.assertIn("17", results.read_text(encoding="utf-8"))
            w0_results = final.site / "cfb/years/2025/spread/2025_W0_FBS_spread_results.html"
            self.assertTrue(w0_results.is_file())
            self.assertIn("ats_result", w0_results.read_text(encoding="utf-8"))

    def test_archived_checkpoint_tamper_is_rejected_after_manifest_reseal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root)
            prior = PreviousFinal(
                {"Alpha State": 10.0, "Beta Tech": 8.0},
                {"Alpha State": 0.0, "Beta Tech": 0.0},
                year=2024,
                classification="FBS",
            )
            candidate = build_release(
                _snapshot(complete_through_week=-1),
                root / "preseason",
                release_id="preseason",
                phase="preseason",
                previous_final=prior,
                published_site=base,
                timestamp=STAMP,
            )
            manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
            archive = candidate.site / manifest["runs"][0]["snapshot_archive_path"]
            payload = json.loads(archive.read_text(encoding="utf-8"))
            payload["teams"][0]["conference"] = "Forged Conference"
            archive.write_text(_canonical_json(payload), encoding="utf-8")
            _reseal_manifest(candidate)

            report = validate_release(candidate, published_site=base)

            self.assertFalse(report.valid)
            self.assertIn("runs.invalid", {failure.code for failure in report.failures})

    def test_same_checkpoint_correction_uses_new_snapshot_and_latest_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root)
            prior = PreviousFinal(
                {"Alpha State": 10.0, "Beta Tech": 8.0},
                {"Alpha State": 0.0, "Beta Tech": 0.0},
                year=2024,
                classification="FBS",
            )
            first = build_release(
                _snapshot(complete_through_week=0, w0_score=(31, 20)),
                root / "first",
                release_id="first",
                phase="week",
                target_week=0,
                previous_final=prior,
                published_site=base,
                timestamp=STAMP,
            )
            corrected = build_release(
                _snapshot(complete_through_week=0, w0_score=(30, 20)),
                root / "corrected",
                release_id="corrected",
                phase="week",
                target_week=0,
                previous_final=prior,
                published_site=first.site,
                timestamp=STAMP,
            )

            report = validate_release(corrected, published_site=base)

            self.assertTrue(report.valid, [str(failure) for failure in report.failures])
            manifest = json.loads(corrected.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [(run["phase"], run["target_week"]) for run in manifest["runs"]],
                [("week", 0), ("week", 0)],
            )
            self.assertNotEqual(
                manifest["runs"][0]["source_snapshot"],
                manifest["runs"][1]["source_snapshot"],
            )
            result_path = corrected.site / "cfb/years/2025/data/results/weekly_results/2025_W0_FBS_results.html"
            self.assertIn("30", result_path.read_text(encoding="utf-8"))

    def test_week_zero_ats_semantics_hand_check_favorite_underdog_and_push(self):
        self.assertEqual(
            grade_ats(
                home_team="Home",
                away_team="Away",
                home_points=31,
                away_points=20,
                home_margin_line=7,
            ),
            {"favorite": "Home", "underdog": "Away", "ats_result": "home", "ats_correct": True},
        )
        self.assertEqual(
            grade_ats(
                home_team="Home",
                away_team="Away",
                home_points=20,
                away_points=31,
                home_margin_line=7,
            ),
            {"favorite": "Home", "underdog": "Away", "ats_result": "away", "ats_correct": False},
        )
        self.assertEqual(
            grade_ats(
                home_team="Home",
                away_team="Away",
                home_points=24,
                away_points=17,
                home_margin_line=7,
            ),
            {"favorite": "Home", "underdog": "Away", "ats_result": "push", "ats_correct": None},
        )

    def _build_cross_season(self, root: Path):
        base = _base(root, prior_year=2023)
        prior = PreviousFinal(
            {"Alpha State": 10.0, "Beta Tech": 8.0},
            {"Alpha State": 0.0, "Beta Tech": 0.0},
            year=2023,
            classification="FBS",
        )
        final_2024 = build_release(
            _season_snapshot(
                year=2024,
                complete_through_week=1,
                w0_score=(31, 20),
                w1_score=(17, 10),
            ),
            root / "final-2024",
            release_id="final-2024",
            phase="final",
            target_week=1,
            previous_final=prior,
            published_site=base,
            timestamp="2026-09-08T12:00:00+00:00",
        )
        preseason_2025 = build_release(
            _season_snapshot(year=2025, complete_through_week=-1),
            root / "preseason-2025",
            release_id="preseason-2025",
            phase="preseason",
            published_site=final_2024.site,
            timestamp="2026-09-08T12:00:00+00:00",
        )
        return base, final_2024, preseason_2025

    def test_latest_owned_historical_final_table_is_checked_after_next_season_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            base, _, candidate = self._build_cross_season(Path(directory))
            path = candidate.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
            document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
            document.select_one("tbody tr").find_all("td")[headers.index("record")].string = "99-0"
            path.write_text(str(document), encoding="utf-8")
            _reseal_manifest(candidate)

            report = validate_release(candidate, published_site=base)

            self.assertFalse(report.valid)
            self.assertIn("ranking.value", {failure.code for failure in report.failures})

    def test_latest_final_history_outcomes_cover_both_aliases_after_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            base, _, candidate = self._build_cross_season(Path(directory))
            for relative in (
                "cfb/history/nc_FBS_CFB_output.html",
                "cfb/years/history/nc_FBS_CFB_output.html",
            ):
                path = candidate.site / relative
                document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
                for row in document.select("tbody tr"):
                    cells = row.find_all("td")
                    if cells[headers.index("year")].get_text(strip=True) == "2024":
                        cells[headers.index("cors")].string = "999"
                path.write_text(str(document), encoding="utf-8")
            _reseal_manifest(candidate)

            report = validate_release(candidate, published_site=base)

            self.assertFalse(report.valid)
            self.assertIn("history.outcome", {failure.code for failure in report.failures})

    def test_same_season_final_correction_supersedes_history_and_keeps_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root, prior_year=2023)
            prior = PreviousFinal(
                {"Alpha State": 10.0, "Beta Tech": 8.0},
                {"Alpha State": 0.0, "Beta Tech": 0.0},
                year=2023,
                classification="FBS",
            )
            first = build_release(
                _season_snapshot(year=2024, complete_through_week=1, w0_score=(31, 20), w1_score=(17, 10)),
                root / "first-final",
                release_id="first-final",
                phase="final",
                target_week=1,
                previous_final=prior,
                published_site=base,
                timestamp="2026-09-08T12:00:00+00:00",
            )
            corrected = build_release(
                _season_snapshot(year=2024, complete_through_week=1, w0_score=(10, 20), w1_score=(17, 10)),
                root / "corrected-final",
                release_id="corrected-final",
                phase="final",
                target_week=1,
                previous_final=prior,
                published_site=first.site,
                timestamp="2026-09-08T13:00:00+00:00",
            )
            candidate = build_release(
                _season_snapshot(year=2025, complete_through_week=-1),
                root / "corrected-preseason",
                release_id="corrected-preseason",
                phase="preseason",
                published_site=corrected.site,
                timestamp="2026-09-09T12:00:00+00:00",
            )

            report = validate_release(candidate, published_site=base)

            self.assertTrue(report.valid, [str(failure) for failure in report.failures])
            manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["runs"]), 3)
            self.assertNotEqual(manifest["runs"][0]["source_snapshot"], manifest["runs"][1]["source_snapshot"])
            self.assertTrue((candidate.site / manifest["runs"][0]["snapshot_archive_path"]).is_file())
            final_path = candidate.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
            final_rows = BeautifulSoup(final_path.read_text(encoding="utf-8"), "html.parser").select("tbody tr")
            final_headers = [cell.get_text(strip=True) for cell in BeautifulSoup(final_path.read_text(encoding="utf-8"), "html.parser").select("thead th")]
            expected_school = final_rows[0].find_all("td")[final_headers.index("school")].get_text(strip=True)
            history_path = candidate.site / "cfb/history/nc_FBS_CFB_output.html"
            history_document = BeautifulSoup(history_path.read_text(encoding="utf-8"), "html.parser")
            history_headers = [cell.get_text(strip=True) for cell in history_document.select("thead th")]
            current = [
                row.find_all("td")
                for row in history_document.select("tbody tr")
                if row.find_all("td")[history_headers.index("year")].get_text(strip=True) == "2024"
            ]
            self.assertEqual(current[0][history_headers.index("school")].get_text(strip=True), expected_school)

    def test_direct_final_correction_validates_against_both_bases_and_preserves_other_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root, prior_year=2023)
            _write_history(base, year=2023)
            prior = PreviousFinal(
                {"Alpha State": 10.0, "Beta Tech": 8.0},
                {"Alpha State": 0.0, "Beta Tech": 0.0},
                year=2023,
                classification="FBS",
            )
            first = build_release(
                _season_snapshot(year=2024, complete_through_week=1, w0_score=(45, 0), w1_score=(17, 10)),
                root / "direct-first-final",
                release_id="direct-first-final",
                phase="final",
                target_week=1,
                previous_final=prior,
                published_site=base,
                timestamp="2026-09-08T12:00:00+00:00",
            )
            corrected = build_release(
                _season_snapshot(year=2024, complete_through_week=1, w0_score=(0, 45), w1_score=(17, 10)),
                root / "direct-corrected-final",
                release_id="direct-corrected-final",
                phase="final",
                target_week=1,
                previous_final=prior,
                published_site=first.site,
                timestamp="2026-09-08T13:00:00+00:00",
            )

            original = validate_release(corrected, published_site=base)
            immediate = validate_release(corrected, published_site=first.site)

            self.assertTrue(original.valid, [str(failure) for failure in original.failures])
            self.assertTrue(immediate.valid, [str(failure) for failure in immediate.failures])
            for relative in (
                "cfb/history/nc_FBS_CFB_output.html",
                "cfb/history/wt_FBS_CFB_output.html",
                "cfb/years/history/nc_FBS_CFB_output.html",
                "cfb/years/history/wt_FBS_CFB_output.html",
            ):
                rows = BeautifulSoup((corrected.site / relative).read_text(encoding="utf-8"), "html.parser").select("tbody tr")
                years = [row.find_all("td")[0].get_text(strip=True) for row in rows]
                self.assertIn("2023", years)
                self.assertIn("2024", years)

    def test_direct_final_correction_rejects_tampered_other_season_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root, prior_year=2023)
            _write_history(base, year=2023)
            prior = PreviousFinal(
                {"Alpha State": 10.0, "Beta Tech": 8.0},
                {"Alpha State": 0.0, "Beta Tech": 0.0},
                year=2023,
                classification="FBS",
            )
            first = build_release(
                _season_snapshot(year=2024, complete_through_week=1, w0_score=(45, 0), w1_score=(17, 10)),
                root / "tamper-first-final",
                release_id="tamper-first-final",
                phase="final",
                target_week=1,
                previous_final=prior,
                published_site=base,
                timestamp="2026-09-08T12:00:00+00:00",
            )
            corrected = build_release(
                _season_snapshot(year=2024, complete_through_week=1, w0_score=(0, 45), w1_score=(17, 10)),
                root / "tamper-corrected-final",
                release_id="tamper-corrected-final",
                phase="final",
                target_week=1,
                previous_final=prior,
                published_site=first.site,
                timestamp="2026-09-08T13:00:00+00:00",
            )
            path = corrected.site / "cfb/history/nc_FBS_CFB_output.html"
            document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
            for row in document.select("tbody tr"):
                cells = row.find_all("td")
                if cells[headers.index("year")].get_text(strip=True) == "2023":
                    cells[headers.index("school")].string = "Forged History"
            path.write_text(str(document), encoding="utf-8")
            _reseal_manifest(corrected)

            report = validate_release(corrected, published_site=base)

            self.assertFalse(report.valid)
            self.assertIn("history.loss", {failure.code for failure in report.failures})

    def test_legacy_history_headers_preserve_win_pct_and_reject_field_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root, prior_year=2023)
            _write_legacy_history(base, year=2023)
            prior = PreviousFinal(
                {"Alpha State": 10.0, "Beta Tech": 8.0},
                {"Alpha State": 0.0, "Beta Tech": 0.0},
                year=2023,
                classification="FBS",
            )
            first = build_release(
                _season_snapshot(year=2024, complete_through_week=1, w0_score=(45, 0), w1_score=(17, 10)),
                root / "legacy-first-final",
                release_id="legacy-first-final",
                phase="final",
                target_week=1,
                previous_final=prior,
                published_site=base,
                timestamp="2026-09-08T12:00:00+00:00",
            )
            corrected = build_release(
                _season_snapshot(year=2024, complete_through_week=1, w0_score=(0, 45), w1_score=(17, 10)),
                root / "legacy-corrected-final",
                release_id="legacy-corrected-final",
                phase="final",
                target_week=1,
                previous_final=prior,
                published_site=first.site,
                timestamp="2026-09-08T13:00:00+00:00",
            )

            original = validate_release(corrected, published_site=base)
            immediate = validate_release(corrected, published_site=first.site)
            self.assertTrue(original.valid, [str(failure) for failure in original.failures])
            self.assertTrue(immediate.valid, [str(failure) for failure in immediate.failures])
            history_path = corrected.site / "cfb/history/nc_FBS_CFB_output.html"
            headers = [cell.get_text(strip=True) for cell in BeautifulSoup(history_path.read_text(encoding="utf-8"), "html.parser").select("thead th")]
            self.assertIn("win_pct", headers)
            self.assertEqual(
                BeautifulSoup(history_path.read_text(encoding="utf-8"), "html.parser").select("tbody tr")[0].find_all("td")[headers.index("win_pct")].get_text(strip=True),
                "1",
            )

            for field_name, forged_value in (("cors", "999"), ("record", "99-0"), ("win_pct", "0.25")):
                forged = build_release(
                    _season_snapshot(year=2024, complete_through_week=1, w0_score=(0, 45), w1_score=(17, 10)),
                    root / f"legacy-forged-{field_name}",
                    release_id=f"legacy-forged-{field_name}",
                    phase="final",
                    target_week=1,
                    previous_final=prior,
                    published_site=first.site,
                    timestamp="2026-09-08T14:00:00+00:00",
                )
                for relative in (
                    "cfb/history/nc_FBS_CFB_output.html",
                    "cfb/years/history/nc_FBS_CFB_output.html",
                ):
                    path = forged.site / relative
                    document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                    columns = [cell.get_text(strip=True) for cell in document.select("thead th")]
                    for row in document.select("tbody tr"):
                        cells = row.find_all("td")
                        if cells[columns.index("year")].get_text(strip=True) == "2023":
                            cells[columns.index(field_name)].string = forged_value
                    path.write_text(str(document), encoding="utf-8")
                _reseal_manifest(forged)
                report = validate_release(forged, published_site=base)
                self.assertFalse(report.valid)
                self.assertIn("history.loss", {failure.code for failure in report.failures})

    def test_per_run_timestamps_validate_against_original_and_immediate_bases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _base(root)
            prior = PreviousFinal(
                {"Alpha State": 10.0, "Beta Tech": 8.0},
                {"Alpha State": 0.0, "Beta Tech": 0.0},
                year=2024,
                classification="FBS",
            )
            preseason = build_release(
                _snapshot(complete_through_week=-1),
                root / "dated-preseason",
                release_id="dated-preseason",
                phase="preseason",
                previous_final=prior,
                published_site=base,
                timestamp="2026-09-08T12:00:00+00:00",
            )
            w0 = build_release(
                _snapshot(complete_through_week=0, w0_score=(31, 20)),
                root / "dated-w0",
                release_id="dated-w0",
                phase="week",
                target_week=0,
                previous_final=prior,
                published_site=preseason.site,
                timestamp="2026-09-09T12:00:00+00:00",
            )

            immediate = validate_release(w0, published_site=preseason.site)
            original = validate_release(w0, published_site=base)

            self.assertTrue(immediate.valid, [str(failure) for failure in immediate.failures])
            self.assertTrue(original.valid, [str(failure) for failure in original.failures])
            preseason_page = w0.site / "cfb/years/2025/rankings/2025_PRESEASON_FBS_cors.html"
            w0_page = w0.site / "cfb/years/2025/rankings/2025_W0_FBS_cors.html"
            self.assertIn("Last updated: 2026-09-08T12:00:00+00:00", preseason_page.read_text(encoding="utf-8"))
            self.assertIn("Last updated: 2026-09-09T12:00:00+00:00", w0_page.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
