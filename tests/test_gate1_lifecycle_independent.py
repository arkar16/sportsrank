"""Independent Gate 1 lifecycle and provenance attacks.

These tests intentionally construct their expected public paths and checkpoint
order from the acceptance contract.  They do not use the renderer or manifest
to decide whether a required artifact or cumulative run exists.
"""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from types import MappingProxyType
import unittest

from bs4 import BeautifulSoup

from cfb.release import build_release, validate_release
from cfb.ranking_engine import PreviousFinal, is_completed
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam, normalize_game


STAMP = "2026-09-08T12:00:00+00:00"
TEAMS = (
    SourceTeam("Alpha State", "Test"),
    SourceTeam("Beta Tech", "Test"),
)
PRIOR_CORS = {"Alpha State": 10.0, "Beta Tech": 8.0}


def _snapshot(year: int, complete_through_week: int, scores: tuple[tuple[int, int] | None, ...]) -> SeasonSnapshot:
    games = []
    for week, score in enumerate(scores):
        home, away = score or (None, None)
        games.append(
            SourceGame(
                week,
                "Alpha State" if week % 2 == 0 else "Beta Tech",
                "fbs",
                home,
                "Beta Tech" if week % 2 == 0 else "Alpha State",
                "fbs",
                away,
                False,
                provider_id=f"{year}-g{week}",
                completed=score is not None,
                disposition="completed" if score is not None else "scheduled",
                disposition_source="independent-fixture",
            )
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
    games_tuple = tuple(games)
    return SeasonSnapshot(
        "cfb",
        "FBS",
        year,
        TEAMS,
        games_tuple,
        metadata,
        _checksum(metadata, TEAMS, games_tuple),
    )


def _prior() -> PreviousFinal:
    return PreviousFinal(
        dict(PRIOR_CORS),
        {school: 0.0 for school in PRIOR_CORS},
        year=2023,
        classification="FBS",
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _tree_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reseal(candidate, *, sync_release: bool = False) -> None:
    """Reseal an attack after changing content, bypassing checksum-only gates."""

    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    if sync_release:
        release_path = candidate.site / "release.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        for key in (
            "sport",
            "classification",
            "season",
            "phase",
            "target_week",
            "source_snapshot",
            "runs",
            "owned_artifacts",
            "required_artifacts",
        ):
            release[key] = manifest[key]
        _write_json(release_path, release)
    manifest["artifact_checksums"] = {
        relative: _tree_sha(candidate.site / relative)
        for relative in manifest["owned_artifacts"]
        if (candidate.site / relative).is_file()
    }
    manifest.pop("manifest_checksum", None)
    manifest["manifest_checksum"] = _tree_sha_bytes(
        (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    )
    _write_json(candidate.manifest_path, manifest)


def _tree_sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _table_rows(path: Path) -> list[dict[str, str]]:
    document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
    return [
        dict(zip(headers, (cell.get_text(strip=True) for cell in row.find_all("td"))))
        for row in document.select("tbody tr")
    ]


def _base(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("inherited homepage bytes", encoding="utf-8")
    sentinel = base / "audit" / "historical-cache-digest.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("preserve this inherited path", encoding="utf-8")
    prior_path = base / "cfb" / "years" / "2023" / "rankings" / "2023_FINAL_FBS_cors.html"
    prior_path.parent.mkdir(parents=True)
    prior_rows = "".join(
        f"<tr><td>{school}</td><td>{cors}</td><td>0.0</td></tr>"
        for school, cors in PRIOR_CORS.items()
    )
    prior_path.write_text(
        "<html><body><table><thead><tr>"
        "<th>school</th><th>cors</th><th>wins_vs_expected</th>"
        f"</tr></thead><tbody>{prior_rows}</tbody></table></body></html>",
        encoding="utf-8",
    )
    history_rows = {
        "nc_FBS_CFB_output.html": ("Alpha State", "National champion"),
        "wt_FBS_CFB_output.html": ("Beta Tech", "Worst team"),
    }
    for filename, (school, kind) in history_rows.items():
        path = base / "cfb" / "history" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "<html><body><table><thead><tr>"
            "<th>year</th><th>school</th><th>conference</th><th>record</th><th>cors</th><th>kind</th>"
            f"</tr></thead><tbody><tr><td>2023</td><td>{school}</td><td>Test</td>"
            f"<td>1-0</td><td>10.0</td><td>{kind}</td></tr></tbody></table></body></html>",
            encoding="utf-8",
        )
    return base


class IndependentSourceCompletionTests(unittest.TestCase):
    def test_explicit_completion_and_canonical_non_played_dispositions_are_authoritative(self):
        # Explicit CFBD ``completed`` is authoritative even when provisional
        # scores or a contradictory generic status are also present.
        normalized = normalize_game(
            {
                "week": 0,
                "home_team": "Alpha State",
                "home_classification": "fbs",
                "home_points": 7,
                "away_team": "Beta Tech",
                "away_classification": "fbs",
                "away_points": 3,
                "completed": False,
                "status": "completed",
            }
        )
        self.assertIs(normalized.completed, False)
        self.assertFalse(is_completed(normalized))

        direct = SourceGame(
            0, "Alpha State", "fbs", 7, "Beta Tech", "fbs", 3, False,
            completed=False, disposition="completed",
        )
        mapped = {
            **asdict(direct),
            "completed": True,
            "disposition": "canceled",
        }
        self.assertFalse(is_completed(direct))
        self.assertFalse(is_completed(mapped))

        not_played = normalize_game(
            {
                "week": 0,
                "home_team": "Alpha State",
                "home_classification": "fbs",
                "home_points": 7,
                "away_team": "Beta Tech",
                "away_classification": "fbs",
                "away_points": 3,
                "completed": True,
                "disposition": "not played",
            }
        )
        self.assertEqual(not_played.disposition, "not_played")
        self.assertFalse(is_completed(not_played))


class IndependentReleaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = _base(self.root)
        self.preseason_snapshot = _snapshot(2024, -1, (None, None, None))
        self.w0_snapshot = _snapshot(2024, 0, ((24, 17), None, None))
        self.w1_snapshot = _snapshot(2024, 1, ((24, 17), (28, 21), None))
        self.final_snapshot = _snapshot(2024, 2, ((24, 17), (28, 21), (17, 10)))

    def tearDown(self):
        self.temp.cleanup()

    def _build_chain(self, prefix: str = ""):
        prior = _prior()
        name = lambda value: f"{prefix}-{value}" if prefix else value
        preseason = build_release(
            self.preseason_snapshot,
            self.root / name("preseason"),
            release_id=name("preseason"),
            phase="preseason",
            previous_final=prior,
            published_site=self.base,
            timestamp=STAMP,
        )
        w0 = build_release(
            self.w0_snapshot,
            self.root / name("w0"),
            release_id=name("w0"),
            phase="week",
            target_week=0,
            previous_final=prior,
            published_site=preseason.site,
            timestamp=STAMP,
        )
        w1 = build_release(
            self.w1_snapshot,
            self.root / name("w1"),
            release_id=name("w1"),
            phase="week",
            target_week=1,
            previous_final=prior,
            published_site=w0.site,
            timestamp=STAMP,
        )
        final = build_release(
            self.final_snapshot,
            self.root / name("final"),
            release_id=name("final"),
            phase="final",
            target_week=2,
            previous_final=prior,
            published_site=w1.site,
            timestamp=STAMP,
        )
        return preseason, w0, w1, final

    def test_evolving_same_season_chain_validates_against_original_base(self):
        _, _, _, final = self._build_chain()
        report = validate_release(final, published_site=self.base)
        self.assertTrue(report.valid, [str(failure) for failure in report.failures])

        manifest = json.loads(final.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [(run["season"], run["phase"], run["target_week"]) for run in manifest["runs"]],
            [(2024, "preseason", -1), (2024, "week", 0), (2024, "week", 1), (2024, "final", 2)],
        )
        for run in manifest["runs"]:
            archive = final.site / run["snapshot_archive_path"]
            self.assertTrue(archive.is_file(), archive)
        self.assertEqual(
            (final.site / "audit" / "historical-cache-digest.txt").read_text(encoding="utf-8"),
            "preserve this inherited path",
        )

    def test_legacy_history_headers_preserve_values_and_reject_cors_tamper(self):
        """Published history may use the legacy title/uppercase schema.

        The current renderer adds a derived ``kind`` column to both aliases,
        while the real published site uses Year/School/.../Win%/CORS and has
        no kind field.  Validation must accept that schema while retaining
        numeric Win% and checking inherited Win%/CORS after a reseal.
        """
        legacy = self.root / "legacy-published"
        shutil.copytree(self.base, legacy)
        for filename in ("nc_FBS_CFB_output.html", "wt_FBS_CFB_output.html"):
            path = legacy / "cfb" / "history" / filename
            document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
            kind_index = headers.index("kind")
            for cell in document.select("thead tr th"):
                cell.string = {
                    "year": "Year",
                    "school": "School",
                    "conference": "Conference",
                    "record": "Record",
                    "cors": "CORS",
                    "kind": "kind",
                }.get(cell.get_text(strip=True), cell.get_text(strip=True))
            document.select("thead th")[kind_index].decompose()
            for row in document.select("tbody tr"):
                cells = row.find_all("td")
                cells[kind_index].decompose()
                win_pct = document.new_tag("td")
                win_pct.string = "1.0"
                row.find_all("td")[headers.index("cors")].insert_before(win_pct)
            win_header = document.new_tag("th")
            win_header.string = "Win%"
            document.select("thead th")[headers.index("cors")].insert_before(win_header)
            path.write_text(str(document), encoding="utf-8")

        self.base = legacy
        _, _, _, final = self._build_chain("legacy-headers")
        accepted = validate_release(final, published_site=self.base)
        self.assertTrue(accepted.valid, [str(failure) for failure in accepted.failures])

        for filename in ("nc_FBS_CFB_output.html", "wt_FBS_CFB_output.html"):
            for root in ("cfb/history", "cfb/years/history"):
                path = final.site / root / filename
                document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
                cors_index = headers.index("cors")
                year_index = headers.index("year")
                win_index = next(
                    (
                        index
                        for index, header in enumerate(headers)
                        if re.sub(r"[^a-z0-9]", "", header.lower())
                        in {"win", "winpct", "winpercentage"}
                    ),
                    None,
                )
                self.assertIsNotNone(win_index, (filename, root, headers))
                inherited = next(
                    row
                    for row in document.select("tbody tr")
                    if row.find_all("td")[year_index].get_text(strip=True) == "2023"
                )
                self.assertAlmostEqual(float(inherited.find_all("td")[win_index].get_text(strip=True)), 1.0)
                for row in document.select("tbody tr"):
                    cells = row.find_all("td")
                    if cells[year_index].get_text(strip=True) == "2023":
                        cells[cors_index].string = "999.0"
                        cells[win_index].string = "999.0"
                path.write_text(str(document), encoding="utf-8")
        _reseal(final)
        rejected = validate_release(final, published_site=self.base)
        self.assertFalse(rejected.valid)
        self.assertIn("history.loss", {failure.code for failure in rejected.failures})

    def test_scored_w0_results_use_preseason_forecast_with_hand_checked_ats(self):
        _, _, _, final = self._build_chain("ats")
        spread_path = final.site / "cfb/years/2024/spread/2024_W0_FBS_spread.html"
        result_path = final.site / "cfb/years/2024/spread/2024_W0_FBS_spread_results.html"
        spread = _table_rows(spread_path)
        results = _table_rows(result_path)
        self.assertEqual(len(spread), 1)
        self.assertEqual(len(results), 1)

        # Prior FINAL is Alpha=10, Beta=8; PRESEASON keeps those values because
        # both independent wins-vs-expected adjustments are zero.  The home
        # advantage is 2, so the hand-calculated W0 home line is +4 and the
        # completed game's home margin is 24-17=7.
        self.assertEqual(float(spread[0]["spread_value"]), 4.0)
        self.assertEqual(float(results[0]["spread_value"]), 4.0)
        self.assertEqual(float(results[0]["actual_margin"]), 7.0)
        self.assertEqual(
            (results[0]["favorite"], results[0]["underdog"], results[0]["ats_result"], results[0]["ats_correct"]),
            ("Alpha State", "Beta Tech", "home", "True"),
        )

    def test_changed_snapshot_can_correct_current_checkpoint_and_keeps_old_archive(self):
        _, _, prior_w1, _ = self._build_chain("correction")
        corrected_snapshot = _snapshot(2024, 1, ((24, 17), (30, 21), None))
        corrected = build_release(
            corrected_snapshot,
            self.root / "correction-w1-current",
            release_id="correction-w1-current",
            phase="week",
            target_week=1,
            previous_final=_prior(),
            published_site=prior_w1.site,
            timestamp=STAMP,
        )
        report = validate_release(corrected, published_site=self.base)
        self.assertTrue(report.valid, [str(failure) for failure in report.failures])
        old_archive = prior_w1.site / json.loads(prior_w1.manifest_path.read_text(encoding="utf-8"))["runs"][-1]["snapshot_archive_path"]
        self.assertTrue(old_archive.is_file())
        self.assertTrue(
            (corrected.site / old_archive.relative_to(prior_w1.site)).is_file(),
            "correction must retain the historical snapshot archive",
        )
        old_snapshot = json.loads(
            (prior_w1.site / "cfb/years/2024/data/snapshot.json").read_text(encoding="utf-8")
        )
        new_snapshot = json.loads(
            (corrected.site / "cfb/years/2024/data/snapshot.json").read_text(encoding="utf-8")
        )
        self.assertEqual(new_snapshot["games"][1]["home_points"], 30)
        self.assertNotEqual(old_snapshot["checksum"], new_snapshot["checksum"])
        self.assertNotEqual(
            json.loads(prior_w1.manifest_path.read_text(encoding="utf-8"))["source_snapshot"],
            json.loads(corrected.manifest_path.read_text(encoding="utf-8"))["source_snapshot"],
        )

    def test_direct_final_correction_with_champion_change_validates_immediate_base(self):
        prior = _prior()
        first = build_release(
            _snapshot(2024, 1, ((45, 0), (0, 45))),
            self.root / "identity-first-final",
            release_id="identity-first-final",
            phase="final",
            target_week=1,
            previous_final=prior,
            published_site=self.base,
            timestamp="2026-09-08T12:00:00+00:00",
        )
        corrected = build_release(
            _snapshot(2024, 1, ((0, 45), (45, 0))),
            self.root / "identity-corrected-final",
            release_id="identity-corrected-final",
            phase="final",
            target_week=1,
            previous_final=prior,
            published_site=first.site,
            timestamp="2026-09-08T13:00:00+00:00",
        )

        report = validate_release(corrected, published_site=first.site)
        self.assertTrue(report.valid, [str(failure) for failure in report.failures])
        history = _table_rows(
            corrected.site / "cfb/history/nc_FBS_CFB_output.html"
        )
        current = [row for row in history if row["year"] == "2024"]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["school"], "Beta Tech")

    def test_duplicate_and_reversed_run_chains_fail_after_resealing(self):
        for attack in ("duplicate", "reversed"):
            with self.subTest(attack=attack):
                _, _, _, candidate = self._build_chain(attack)
                manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
                runs = list(manifest["runs"])
                if attack == "duplicate":
                    runs[1] = dict(runs[0])
                else:
                    runs.reverse()
                manifest["runs"] = runs
                if attack == "reversed":
                    latest = runs[-1]
                    for key in ("sport", "classification", "season", "phase", "target_week", "source_snapshot"):
                        manifest[key] = latest[key]
                _write_json(candidate.manifest_path, manifest)
                _reseal(candidate, sync_release=True)
                report = validate_release(candidate, published_site=self.base)
                self.assertFalse(report.valid)
                self.assertTrue(
                    {failure.code for failure in report.failures}
                    & {"runs.duplicate", "runs.chain", "runs.order", "runs.current"},
                    [str(failure) for failure in report.failures],
                )

    def test_cross_season_build_without_prior_final_fails_before_rendering(self):
        prior = _prior()
        incomplete_prior = build_release(
            self.preseason_snapshot,
            self.root / "2024-preseason-only",
            release_id="2024-preseason-only",
            phase="preseason",
            previous_final=prior,
            published_site=self.base,
            timestamp=STAMP,
        )
        next_season = _snapshot(2025, -1, (None, None, None))
        with self.assertRaises((ValueError, RuntimeError)):
            build_release(
                next_season,
                self.root / "2025-without-2024-final",
                release_id="2025-without-2024-final",
                phase="preseason",
                previous_final=PreviousFinal(
                    dict(PRIOR_CORS),
                    {school: 0.0 for school in PRIOR_CORS},
                ),
                published_site=incomplete_prior.site,
                timestamp=STAMP,
            )
        self.assertFalse((self.root / "2025-without-2024-final").exists())

    def test_deleted_archived_snapshot_cannot_be_hidden_by_resealed_manifest(self):
        _, _, _, candidate = self._build_chain()
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        archive = manifest["runs"][0]["snapshot_archive_path"]
        (candidate.site / archive).unlink()
        manifest["owned_artifacts"].remove(archive)
        manifest["required_artifacts"].remove(archive)
        manifest["artifact_checksums"].pop(archive, None)
        _write_json(candidate.manifest_path, manifest)
        _reseal(candidate, sync_release=True)

        report = validate_release(candidate, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertTrue(
            {failure.code for failure in report.failures}
            & {"artifact.required", "artifact.missing", "runs.invalid", "snapshot.archive"},
            [str(failure) for failure in report.failures],
        )

    def test_tampered_archived_snapshot_fails_after_artifact_resealing(self):
        _, _, _, candidate = self._build_chain()
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        archive = candidate.site / manifest["runs"][0]["snapshot_archive_path"]
        payload = json.loads(archive.read_text(encoding="utf-8"))
        payload["games"][0]["home_points"] = 999
        _write_json(archive, payload)
        _reseal(candidate)

        report = validate_release(candidate, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertTrue(
            {failure.code for failure in report.failures}
            & {"snapshot.checksum", "runs.invalid", "snapshot.archive", "ranking.value"},
            [str(failure) for failure in report.failures],
        )

    def test_latest_owned_final_field_tamper_is_rejected_after_resealing(self):
        _, _, _, candidate = self._build_chain()
        final_path = candidate.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
        document = BeautifulSoup(final_path.read_text(encoding="utf-8"), "html.parser")
        headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
        document.select_one("tbody tr").find_all("td")[headers.index("cors")].string = "999"
        final_path.write_text(str(document), encoding="utf-8")
        _reseal(candidate)

        report = validate_release(candidate, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertIn("ranking.value", {failure.code for failure in report.failures})

    def test_historical_final_record_tamper_is_rejected_after_next_season_overlay(self):
        _, _, _, final_2024 = self._build_chain("historical-record")
        preseason_2025 = build_release(
            _snapshot(2025, -1, (None, None, None)),
            self.root / "historical-record-preseason-2025",
            release_id="historical-record-preseason-2025",
            phase="preseason",
            published_site=final_2024.site,
            timestamp=STAMP,
        )
        path = preseason_2025.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
        document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
        document.select_one("tbody tr").find_all("td")[headers.index("record")].string = "99-0"
        path.write_text(str(document), encoding="utf-8")
        _reseal(preseason_2025)

        report = validate_release(preseason_2025, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertTrue(
            {failure.code for failure in report.failures}
            & {"ranking.value", "records.reconcile"},
            [str(failure) for failure in report.failures],
        )

    def test_historical_history_alias_field_tamper_is_rejected_after_resealing(self):
        _, _, _, final_2024 = self._build_chain("historical-history")
        preseason_2025 = build_release(
            _snapshot(2025, -1, (None, None, None)),
            self.root / "historical-history-preseason-2025",
            release_id="historical-history-preseason-2025",
            phase="preseason",
            published_site=final_2024.site,
            timestamp=STAMP,
        )
        for relative in (
            "cfb/history/nc_FBS_CFB_output.html",
            "cfb/years/history/nc_FBS_CFB_output.html",
        ):
            path = preseason_2025.site / relative
            document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
            for row in document.select("tbody tr"):
                cells = row.find_all("td")
                if cells[headers.index("year")].get_text(strip=True) == "2024":
                    cells[headers.index("cors")].string = "999"
            path.write_text(str(document), encoding="utf-8")
        _reseal(preseason_2025)

        report = validate_release(preseason_2025, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertTrue(
            {failure.code for failure in report.failures}
            & {"history.outcome", "history.reconcile", "history.value"},
            [str(failure) for failure in report.failures],
        )

    def test_older_inherited_history_cors_tamper_is_rejected_in_both_aliases(self):
        _, _, _, final_2024 = self._build_chain("older-history")
        preseason_2025 = build_release(
            _snapshot(2025, -1, (None, None, None)),
            self.root / "older-history-preseason-2025",
            release_id="older-history-preseason-2025",
            phase="preseason",
            published_site=final_2024.site,
            timestamp=STAMP,
        )
        for relative in (
            "cfb/history/nc_FBS_CFB_output.html",
            "cfb/years/history/nc_FBS_CFB_output.html",
        ):
            path = preseason_2025.site / relative
            document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
            for row in document.select("tbody tr"):
                cells = row.find_all("td")
                if cells[headers.index("year")].get_text(strip=True) == "2023":
                    cells[headers.index("cors")].string = "999"
            path.write_text(str(document), encoding="utf-8")
        _reseal(preseason_2025)

        report = validate_release(preseason_2025, published_site=self.base)
        self.assertFalse(report.valid)
        self.assertTrue(
            {failure.code for failure in report.failures}
            & {"history.value", "history.reconcile", "history.loss"},
            [str(failure) for failure in report.failures],
        )

    def test_different_checkpoint_timestamp_still_validates_against_original_base(self):
        prior = _prior()
        preseason = build_release(
            self.preseason_snapshot,
            self.root / "timestamp-preseason",
            release_id="timestamp-preseason",
            phase="preseason",
            previous_final=prior,
            published_site=self.base,
            timestamp="2026-09-08T12:00:00+00:00",
        )
        w0 = build_release(
            self.w0_snapshot,
            self.root / "timestamp-w0",
            release_id="timestamp-w0",
            phase="week",
            target_week=0,
            previous_final=prior,
            published_site=preseason.site,
            timestamp="2026-09-08T13:00:00+00:00",
        )
        immediate = validate_release(w0, published_site=preseason.site)
        original = validate_release(w0, published_site=self.base)
        self.assertTrue(immediate.valid, [str(failure) for failure in immediate.failures])
        self.assertTrue(original.valid, [str(failure) for failure in original.failures])


if __name__ == "__main__":
    unittest.main()
