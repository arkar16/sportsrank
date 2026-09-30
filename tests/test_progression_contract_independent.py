"""Independent SR-30 contract4 checks for the Release progression graph.

The fixtures in this file are deliberately small and literal.  They exercise
the Release boundary and reseal the outer manifest after each mutation, so a
checksum-only implementation cannot make a forged progression look valid.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest

from bs4 import BeautifulSoup
from decimal import Decimal

from cfb.forecast_record import (
    ForecastCandidate,
    ForecastProvenance,
    GameIdentity,
    GameTimingEvidence,
)
from cfb.forecast_release import (
    CURRENT_ARTIFACT_CONTRACT,
    FORECAST_ARTIFACT_CONTRACT,
    canonical_json,
)
from cfb.public_site import PUBLIC_RELEASE_CONTRACTS
from cfb.ranking_engine import HFA, MODEL_VERSION, PreviousFinal, season_rankings, spreads_for_week
from cfb.release import (
    build_release,
    validate_release,
    validated_progression_used_coverage,
)
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam
from tests.test_forecast_lifecycle_independent import (
    _authenticated_capability,
    _evidence,
    _issued_candidate,
    _kickoff,
    _prepare_base,
    _snapshot as _forecast_snapshot,
)


CONTRACT4 = 4
MODEL = MODEL_VERSION
STAMP = "2025-12-08T12:00:00+00:00"


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _source_snapshot(complete_through: int) -> SeasonSnapshot:
    """Return the same immutable source as each successive checkpoint."""

    teams = (
        SourceTeam("Alpha State", "Test Conference"),
        SourceTeam("Beta Tech", "Test Conference"),
        SourceTeam("Zulu Tech", "FBS Independents"),
    )
    games = []
    fixtures = (
        (0, "Alpha State", 21, "Beta Tech", 14, "g-w0"),
        (1, "Beta Tech", 17, "Zulu Tech", 10, "g-w1"),
        (2, "Zulu Tech", 28, "Alpha State", 7, "g-w2"),
    )
    for week, home, home_points, away, away_points, provider_id in fixtures:
        complete = week <= complete_through
        games.append(
            SourceGame(
                week,
                home,
                "fbs",
                home_points if complete else None,
                away,
                "fbs",
                away_points if complete else None,
                False,
                provider_id=provider_id,
                date=f"2025-09-{6 + week:02d}",
                provider_week=week,
                completed=complete,
                disposition="completed" if complete else "scheduled",
            )
        )
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2025,
            "teams_fetched_at": STAMP,
            "games_fetched_at": STAMP,
            "complete_through_week": complete_through,
            "calendar_provenance": None,
            "correction_registry_provenance": None,
            "migration_provenance": None,
        }
    )
    return SeasonSnapshot(
        "cfb",
        "FBS",
        2025,
        teams,
        tuple(games),
        metadata,
        _checksum(metadata, teams, tuple(games)),
    )


def _next_season_snapshot(complete_through: int) -> SeasonSnapshot:
    teams = (
        SourceTeam("Alpha State", "Test Conference"),
        SourceTeam("Beta Tech", "Test Conference"),
        SourceTeam("Zulu Tech", "FBS Independents"),
    )
    games = (
        SourceGame(
            0, "Alpha State", "fbs", 24 if complete_through >= 0 else None,
            "Beta Tech", "fbs", 17 if complete_through >= 0 else None, False,
            provider_id="2026-g-w0", date="2026-09-05", provider_week=0,
            completed=complete_through >= 0,
            disposition="completed" if complete_through >= 0 else "scheduled",
        ),
        SourceGame(
            1, "Beta Tech", "fbs", 20 if complete_through >= 1 else None,
            "Zulu Tech", "fbs", 13 if complete_through >= 1 else None, False,
            provider_id="2026-g-w1", date="2026-09-12", provider_week=1,
            completed=complete_through >= 1,
            disposition="completed" if complete_through >= 1 else "scheduled",
        ),
    )
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2026,
            "teams_fetched_at": "2026-09-01T12:00:00+00:00",
            "games_fetched_at": "2026-09-01T12:00:00+00:00",
            "complete_through_week": complete_through,
            "calendar_provenance": None,
            "correction_registry_provenance": None,
            "migration_provenance": None,
        }
    )
    return SeasonSnapshot(
        "cfb", "FBS", 2026, teams, games, metadata, _checksum(metadata, teams, games)
    )


def _contract_migration_snapshot() -> SeasonSnapshot:
    """A literal legacy-compatible 2026 checkpoint with no provider IDs."""

    teams = tuple(SourceTeam(school, "X") for school in ("Home", "Away", "Rival"))
    games = (
        SourceGame(
            0,
            "Home",
            "fbs",
            21,
            "Away",
            "fbs",
            14,
            False,
            provider_id=None,
            completed=True,
            disposition="completed",
        ),
        SourceGame(
            1,
            "Away",
            "fbs",
            17,
            "Rival",
            "fbs",
            14,
            False,
            provider_id=None,
            completed=True,
            disposition="completed",
        ),
        SourceGame(
            2,
            "Rival",
            "fbs",
            None,
            "Home",
            "fbs",
            None,
            False,
            provider_id=None,
            completed=False,
            disposition="scheduled",
        ),
    )
    metadata = MappingProxyType(
        {
            "schema_version": 2,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2026,
            "teams_fetched_at": None,
            "games_fetched_at": None,
            "complete_through_week": 1,
            "calendar_provenance": None,
            "correction_registry_provenance": None,
            "migration_provenance": None,
        }
    )
    return SeasonSnapshot(
        "cfb",
        "FBS",
        2026,
        teams,
        games,
        metadata,
        _checksum(metadata, teams, games),
    )


def _write_base(path: Path) -> tuple[PreviousFinal, bytes]:
    """Create a literal published base with the exact preceding FINAL page."""

    path.mkdir(parents=True)
    (path / "index.html").write_text("retained published bytes", encoding="utf-8")
    final = path / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
    final.parent.mkdir(parents=True)
    contents = (
        "<html><body><table><thead><tr>"
        "<th>school</th><th>cors</th><th>wins_vs_expected</th>"
        "</tr></thead><tbody>"
        "<tr><td>Alpha State</td><td>10</td><td>0</td></tr>"
        "<tr><td>Beta Tech</td><td>20</td><td>0</td></tr>"
        "<tr><td>Zulu Tech</td><td>15</td><td>0</td></tr>"
        "</tbody></table></body></html>"
    ).encode()
    final.write_bytes(contents)
    return (
        PreviousFinal(
            {"Alpha State": 10.0, "Beta Tech": 20.0, "Zulu Tech": 15.0},
            {"Alpha State": 0.0, "Beta Tech": 0.0, "Zulu Tech": 0.0},
        ),
        contents,
    )


def _home_away_2025_snapshot(*, next_completed: bool) -> SeasonSnapshot:
    source = _forecast_snapshot(next_completed=next_completed)
    metadata = dict(source.metadata)
    metadata["year"] = 2025
    return SeasonSnapshot(
        "cfb",
        "FBS",
        2025,
        source.teams,
        source.games,
        MappingProxyType(metadata),
        _checksum(metadata, source.teams, source.games),
    )


def _previous_final_from_page(path: Path) -> PreviousFinal:
    document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    header = [cell.get_text(strip=True) for cell in document.select_one("thead tr").find_all("th")]
    school_index = header.index("school")
    cors_index = header.index("cors")
    wins_index = header.index("wins_vs_expected")
    rows = {}
    wins = {}
    for row in document.select("tbody tr"):
        cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
        if len(cells) > max(school_index, cors_index, wins_index):
            school = cells[school_index]
            rows[school] = float(cells[cors_index])
            wins[school] = float(cells[wins_index])
    return PreviousFinal(rows, wins)


def _forecast_candidate_for_snapshot(
    snapshot: SeasonSnapshot, previous: PreviousFinal
) -> ForecastCandidate:
    ranking_rows = season_rankings(snapshot, 0, previous)[0]
    ranking_by_school = {str(row["school"]): row for row in ranking_rows}
    rating_digest = "sha256:" + hashlib.sha256(canonical_json(ranking_rows)).hexdigest()
    source_digest = "sha256:" + snapshot.checksum
    game = next(game for game in snapshot.games if int(game.week) == 1)
    spread = next(
        row
        for row in spreads_for_week(
            snapshot, 1, ranking_rows, hfa=HFA, legacy_half_point=False
        )
        if row["home_team"] == game.home_team and row["away_team"] == game.away_team
    )
    home = ranking_by_school[game.home_team]
    away = ranking_by_school[game.away_team]
    return ForecastCandidate.create(
        game=GameIdentity(
            str(game.provider_id),
            snapshot.year,
            int(game.week),
            game.home_team,
            game.away_team,
            game.home_classification,
            game.away_classification,
            bool(game.neutral_site),
        ),
        home_margin=spread["home_margin"],
        precision=2,
        provenance=ForecastProvenance(
            "W0",
            "through-week-0",
            rating_digest,
            source_digest,
            MODEL_VERSION,
            "season-snapshot",
            "working-tree",
            home["cors"],
            away["cors"],
            Decimal("0") if game.neutral_site else Decimal(str(HFA)),
            int(home["rank"]),
            int(away["rank"]),
        ),
    )


def _reseal(candidate) -> None:
    """Reseal only the outer manifest, as an attacker can legitimately do."""

    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    checksums = manifest["artifact_checksums"]
    for relative in list(checksums):
        path = candidate.site / relative
        if path.is_file():
            checksums[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.pop("manifest_checksum", None)
    manifest["manifest_checksum"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    candidate.manifest_path.write_bytes(_canonical(manifest))


def _progression_paths(candidate) -> tuple[str, str]:
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    paths = sorted(
        path
        for path in manifest.get("owned_artifacts", [])
        if "_FBS_progression." in path
    )
    if len(paths) != 2:
        raise AssertionError(f"contract4 progression graph is incomplete: {paths!r}")
    return next(path for path in paths if path.endswith(".json")), next(
        path for path in paths if path.endswith(".html")
    )


def _remove_progression_from_manifest(candidate, *, remove_current_marker: bool = True) -> None:
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    progression = {
        path for path in manifest.get("owned_artifacts", []) if "_FBS_progression." in path
    }
    for field in ("owned_artifacts", "required_artifacts"):
        manifest[field] = [path for path in manifest.get(field, []) if path not in progression]
    for path in progression:
        manifest.get("artifact_checksums", {}).pop(path, None)
    # The feature binding itself is part of the current4 requirement.  A
    # resealed candidate cannot remove it while retaining the current marker.
    manifest.pop("progression_feature", None)
    if manifest.get("runs"):
        manifest["runs"][-1].pop("progression_feature", None)
    if remove_current_marker:
        manifest.pop("artifact_contract", None)
        manifest.pop("manifest_version", None)
    if manifest.get("runs") and remove_current_marker:
        manifest["runs"][-1].pop("artifact_contract", None)
    candidate.manifest_path.write_bytes(_canonical(manifest))

    metadata = json.loads(candidate.metadata_path.read_text(encoding="utf-8"))
    metadata.pop("progression_feature", None)
    for run in metadata.get("runs", []):
        run.pop("progression_feature", None)
        if remove_current_marker:
            run.pop("artifact_contract", None)
    if remove_current_marker:
        metadata.pop("artifact_contract", None)
    candidate.metadata_path.write_bytes(_canonical(metadata))
    release_metadata = candidate.site / "release.json"
    if release_metadata != candidate.metadata_path and release_metadata.exists():
        value = json.loads(release_metadata.read_text(encoding="utf-8"))
        for field in ("owned_artifacts", "required_artifacts"):
            value[field] = [path for path in value.get(field, []) if path not in progression]
        value.pop("progression_feature", None)
        for run in value.get("runs", []):
            run.pop("progression_feature", None)
            if remove_current_marker:
                run.pop("artifact_contract", None)
        if remove_current_marker:
            value.pop("artifact_contract", None)
            value.pop("manifest_version", None)
        release_metadata.write_bytes(_canonical(value))

    # A frozen predecessor did not own the additive progression page, so its
    # season navigation must retain the literal pre-feature bytes as well.
    season_page = candidate.site / "cfb/years/2025/2025_CFB.html"
    if season_page.is_file():
        text = season_page.read_text(encoding="utf-8")
        text = text.replace(
            '<a href="history/2025_FBS_progression.html">Ranking progression</a> | ',
            "",
        )
        season_page.write_text(text, encoding="utf-8")


def _rewrite_contract(
    candidate,
    contract: int,
    *,
    remove_forecast_fields: bool = False,
    all_runs: bool = True,
) -> None:
    """Create a literal read-only predecessor envelope for migration tests."""

    paths = [candidate.manifest_path, candidate.metadata_path, candidate.site / "release.json"]
    for path in paths:
        if not path.is_file():
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        value["manifest_version"] = contract
        value["artifact_contract"] = contract
        runs = value.get("runs", [])
        runs_to_rewrite = runs if all_runs else runs[-1:]
        for run in runs_to_rewrite:
            run["artifact_contract"] = contract
            if remove_forecast_fields:
                for field in (
                    "forecast_contract",
                    "forecast_ledger_path",
                    "forecast_evaluation_path",
                ):
                    run.pop(field, None)
        if remove_forecast_fields:
            for field in (
                "forecast_contract",
                "forecast_ledger_path",
                "forecast_evaluation_path",
            ):
                value.pop(field, None)
        path.write_bytes(_canonical(value))
    _reseal(candidate)


def _strip_forecast_artifacts(candidate) -> None:
    """Turn a generated fixture into a contract2 site with legacy spreads only."""

    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    forecast_paths = {
        path
        for path in manifest.get("owned_artifacts", [])
        if "/forecasts/" in path or path.endswith("_forecast_results.html")
        or path.endswith("_spread_results.html")
    }
    for path in forecast_paths:
        artifact = candidate.site / path
        if artifact.is_file():
            artifact.unlink()
        manifest.get("artifact_checksums", {}).pop(path, None)
    for field in ("owned_artifacts", "required_artifacts"):
        manifest[field] = [path for path in manifest.get(field, []) if path not in forecast_paths]
    for field in (
        "forecast_contract",
        "forecast_ledger_path",
        "forecast_evaluation_path",
        "forecast_display_mode",
    ):
        manifest.pop(field, None)
    for run in manifest.get("runs", []):
        for field in (
            "forecast_contract",
            "forecast_ledger_path",
            "forecast_evaluation_path",
            "forecast_display_mode",
        ):
            run.pop(field, None)
    candidate.manifest_path.write_bytes(_canonical(manifest))
    for path in (candidate.metadata_path, candidate.site / "release.json"):
        if not path.is_file():
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        for field in ("owned_artifacts", "required_artifacts"):
            value[field] = [item for item in value.get(field, []) if item not in forecast_paths]
        for field in (
            "forecast_contract",
            "forecast_ledger_path",
            "forecast_evaluation_path",
            "forecast_display_mode",
        ):
            value.pop(field, None)
        for run in value.get("runs", []):
            for field in (
                "forecast_contract",
                "forecast_ledger_path",
                "forecast_evaluation_path",
                "forecast_display_mode",
            ):
                run.pop(field, None)
        path.write_bytes(_canonical(value))
    _reseal(candidate)


class ProgressionContractIndependentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / "published"
        self.previous, self.previous_bytes = _write_base(self.base)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_history_supports_frozen3_while_current_preparation_is4(self):
        self.assertEqual(FORECAST_ARTIFACT_CONTRACT, 3)
        self.assertEqual(CURRENT_ARTIFACT_CONTRACT, CONTRACT4)
        self.assertEqual(PUBLIC_RELEASE_CONTRACTS, frozenset({3, CONTRACT4}))

    def _build_chain(self):
        previous_site = self.base
        releases = []
        for label, snapshot, phase, target in (
            ("preseason", _source_snapshot(-1), "preseason", None),
            ("w0", _source_snapshot(0), "week", 0),
            ("w1", _source_snapshot(1), "week", 1),
            ("final", _source_snapshot(2), "final", 2),
        ):
            release = build_release(
                snapshot,
                self.root / label,
                release_id=label,
                phase=phase,
                target_week=target,
                previous_final=self.previous,
                model_version=MODEL,
                code_revision="contract4-independent",
                timestamp=STAMP,
                published_site=previous_site,
            )
            releases.append(release)
            previous_site = release.site
        return tuple(releases)

    def test_used_coverage_requires_the_same_unchanged_validated_candidate_tree(self):
        """A successful report cannot authorize a different or later-mutated tree."""

        _preseason, _w0, _w1, candidate = self._build_chain()
        report = validate_release(candidate, published_site=self.base)
        self.assertTrue(report.ok, [str(item) for item in report.failures])
        coverage = validated_progression_used_coverage(candidate, report)
        self.assertEqual(coverage["status"], "required")
        self.assertEqual(coverage["season"], 2025)

        other = build_release(
            _source_snapshot(2),
            self.root / "other-final",
            release_id="other-final",
            phase="final",
            target_week=2,
            previous_final=self.previous,
            model_version=MODEL,
            code_revision="different-candidate-tree",
            timestamp="2025-12-09T12:00:00+00:00",
            published_site=self.base,
        )
        other_report = validate_release(other, published_site=self.base)
        self.assertTrue(other_report.ok, [str(item) for item in other_report.failures])
        with self.assertRaises(ValueError):
            validated_progression_used_coverage(other, report)

        manifest_path = candidate.manifest_path
        manifest = json.loads(manifest_path.read_bytes())
        manifest["progression_feature"]["source_snapshot"] = "0" * 64
        manifest_path.write_bytes(_canonical(manifest))
        with self.assertRaises(ValueError):
            validated_progression_used_coverage(candidate, report)

    def test_current4_chain_has_literal_checkpoint_graph_and_authenticated_prior_final(self):
        preseason, w0, w1, final = self._build_chain()
        manifest = json.loads(final.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], CONTRACT4)
        self.assertEqual(manifest["artifact_contract"], CONTRACT4)
        self.assertEqual(
            [(run["phase"], run["target_week"]) for run in manifest["runs"][-4:]],
            [("preseason", -1), ("week", 0), ("week", 1), ("final", 2)],
        )
        json_path, html_path = _progression_paths(final)
        payload = json.loads((final.site / json_path).read_text(encoding="utf-8"))
        self.assertEqual(
            [checkpoint["key"] for checkpoint in payload["checkpoints"]],
            ["PRESEASON", "W0", "W1", "W2", "FINAL"],
        )
        self.assertEqual(payload["provenance"]["season"], 2025)
        self.assertEqual(payload["provenance"]["source_snapshot"], _source_snapshot(2).checksum)
        self.assertEqual(
            [row["team"] for row in payload["rows"]],
            ["Alpha State", "Beta Tech", "Zulu Tech"],
        )
        self.assertTrue(payload["checkpoints"][-1]["available"])
        self.assertTrue((final.site / html_path).is_file())

        prior_path = final.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
        self.assertEqual(prior_path.read_bytes(), self.previous_bytes)
        prior_digest = hashlib.sha256(self.previous_bytes).hexdigest()
        feature = manifest["progression_feature"]
        self.assertEqual(feature["carryover_identity"], f"prior-final:{prior_digest}")
        self.assertEqual(feature["prior_final"]["sha256"], prior_digest)
        self.assertEqual(
            feature["prior_final"]["path"],
            "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html",
        )

        for published in (final.site, self.base):
            report = validate_release(final, published_site=published)
            self.assertTrue(report.valid, [str(item) for item in report.failures])

    def test_incomplete_source_marks_future_checkpoints_unavailable_without_backfill(self):
        candidate = build_release(
            _source_snapshot(0),
            self.root / "w0-only",
            release_id="w0-only",
            phase="week",
            target_week=0,
            previous_final=self.previous,
            model_version=MODEL,
            code_revision="contract4-independent",
            timestamp=STAMP,
            published_site=self.base,
        )
        report = validate_release(candidate, published_site=self.base)
        self.assertTrue(report.valid, [str(item) for item in report.failures])
        json_path, _ = _progression_paths(candidate)
        payload = json.loads((candidate.site / json_path).read_text(encoding="utf-8"))
        by_key = {checkpoint["key"]: checkpoint for checkpoint in payload["checkpoints"]}
        self.assertTrue(by_key["PRESEASON"]["available"])
        self.assertTrue(by_key["W0"]["available"])
        for key in ("W1", "W2", "FINAL"):
            self.assertFalse(by_key[key]["available"])
            self.assertIn(by_key[key]["reason"], {"future_checkpoint", "ranking_missing"})
        for row in payload["rows"]:
            for key in ("W1", "W2", "FINAL"):
                self.assertFalse(row["checkpoints"][key]["available"])
                self.assertIsNone(row["checkpoints"][key].get("points"))
                self.assertIsNone(row["checkpoints"][key].get("rank"))

    def test_resealed_progression_graph_and_static_content_tampering_fails(self):
        _preseason, _w0, _w1, candidate = self._build_chain()
        json_path, html_path = _progression_paths(candidate)
        original_json = (candidate.site / json_path).read_bytes()
        original_html = (candidate.site / html_path).read_bytes()
        original_manifest = candidate.manifest_path.read_bytes()
        original_metadata = candidate.metadata_path.read_bytes()

        mutations = {
            "semantic cell": lambda: self._mutate_json_cell(candidate, json_path),
            "extra JSON field": lambda: self._mutate_json_extra(candidate, json_path),
            "provenance": lambda: self._mutate_json_provenance(candidate, json_path),
            "hidden core table": lambda: self._mutate_html(candidate, html_path, "<table", '<table hidden'),
            "inert core table": lambda: self._mutate_html(candidate, html_path, "<table", '<table inert'),
            "hidden core rows": lambda: self._mutate_html(candidate, html_path, "<tbody", '<tbody hidden'),
            "hidden core cell": lambda: self._mutate_html(candidate, html_path, "<td", '<td style="display:none"'),
            "collapsed core cell": lambda: self._mutate_html(candidate, html_path, "<td", '<td style="height:0;overflow:hidden"'),
            "extra stylesheet": lambda: self._mutate_html(candidate, html_path, "</head>", '<style>.hide{display:none}</style></head>'),
            "duplicate navigation": lambda: self._mutate_html(
                candidate,
                html_path,
                "</nav>",
                '<a href="2025_FBS_progression.json">Progression data</a></nav>',
            ),
            "extra navigation": lambda: self._mutate_html(
                candidate,
                html_path,
                "</nav>",
                '<a href="../2025_CFB.html">Unexpected duplicate season</a></nav>',
            ),
            "altered navigation label": lambda: self._mutate_html(
                candidate, html_path, ">W0 ranking<", ">Forged W0 ranking<"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                mutate()
                _reseal(candidate)
                report = validate_release(candidate, published_site=self.base)
                self.assertFalse(report.valid, [str(item) for item in report.failures])
                (candidate.site / json_path).write_bytes(original_json)
                (candidate.site / html_path).write_bytes(original_html)
                candidate.manifest_path.write_bytes(original_manifest)
                candidate.metadata_path.write_bytes(original_metadata)

        manifest = json.loads(original_manifest)
        feature = manifest["progression_feature"]
        archive_relative = feature["snapshot_archive_path"]
        archive_path = candidate.site / archive_relative
        archive_bytes = archive_path.read_bytes()
        prior_relative = feature["prior_final"]["path"]
        prior_path = candidate.site / prior_relative
        prior_bytes = prior_path.read_bytes()

        def restore_graph() -> None:
            if archive_path.exists():
                archive_path.write_bytes(archive_bytes)
            else:
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                archive_path.write_bytes(archive_bytes)
            if prior_path.exists():
                prior_path.write_bytes(prior_bytes)
            else:
                prior_path.parent.mkdir(parents=True, exist_ok=True)
                prior_path.write_bytes(prior_bytes)
            candidate.manifest_path.write_bytes(original_manifest)
            candidate.metadata_path.write_bytes(original_metadata)

        graph_mutations = {
            "origin archive deleted": lambda: archive_path.unlink(),
            "origin rebound": lambda: manifest["progression_feature"]["source"].update(
                source_snapshot="0" * 64
            ),
            "prior FINAL deleted": lambda: prior_path.unlink(),
            "prior FINAL rebound": lambda: manifest["progression_feature"].update(
                carryover_identity="prior-final:" + "0" * 64,
                prior_final={"path": prior_relative, "sha256": "0" * 64, "values_sha256": "0" * 64},
            ),
            "progression feature stripped": lambda: manifest.pop("progression_feature"),
        }
        for label, mutate in graph_mutations.items():
            with self.subTest(case=label):
                manifest = json.loads(original_manifest)
                mutate()
                candidate.manifest_path.write_bytes(_canonical(manifest))
                _reseal(candidate)
                report = validate_release(candidate, published_site=self.base)
                self.assertFalse(report.valid, [str(item) for item in report.failures])
                restore_graph()

        _remove_progression_from_manifest(candidate, remove_current_marker=False)
        for path in (json_path, html_path):
            (candidate.site / path).unlink()
        _reseal(candidate)
        report = validate_release(candidate, published_site=self.base)
        self.assertFalse(report.valid)

    def test_cross_season_current4_uses_latest_retained_2025_final_without_reverse_run(self):
        _preseason, _w0, _w1, final_2025 = self._build_chain()
        prior_final_bytes = (final_2025.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html").read_bytes()
        next_release = build_release(
            _next_season_snapshot(0),
            self.root / "2026-w0",
            release_id="2026-w0",
            phase="week",
            target_week=0,
            model_version=MODEL,
            code_revision="contract4-cross-season",
            timestamp="2026-09-15T12:00:00+00:00",
            published_site=final_2025.site,
        )
        manifest = json.loads(next_release.manifest_path.read_text(encoding="utf-8"))
        feature = manifest.get("progression_feature")
        self.assertIsInstance(feature, dict)
        self.assertEqual(feature["source"]["kind"], "retained-run")
        self.assertEqual(feature["source"]["phase"], "final")
        self.assertEqual(feature["source"]["target_week"], 2)
        self.assertEqual(feature["source"]["source_snapshot"], _source_snapshot(2).checksum)
        self.assertEqual(
            (next_release.site / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html").read_bytes(),
            prior_final_bytes,
        )
        self.assertEqual(
            [(run["season"], run["phase"], run["target_week"]) for run in manifest["runs"][-5:]],
            [(2025, "preseason", -1), (2025, "week", 0), (2025, "week", 1), (2025, "final", 2), (2026, "week", 0)],
        )
        season_page = (next_release.site / "cfb/years/2025/2025_CFB.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Last updated: 2026-09-15T12:00:00+00:00", season_page)
        self.assertNotIn(f"Last updated: {STAMP}", season_page)
        report = validate_release(next_release, published_site=final_2025.site)
        self.assertTrue(report.valid, [str(item) for item in report.failures])

    def test_same_checkpoint_2026_refresh_and_forecast_correction_preserve_retained_2025_owner(self):
        base = self.root / "home-away-published"
        _prepare_base(base)
        previous = PreviousFinal({"Home": 10.0, "Away": 20.0}, {})
        published = base
        for label, snapshot, phase, target in (
            ("preseason", _home_away_2025_snapshot(next_completed=False), "preseason", None),
            ("w0", _home_away_2025_snapshot(next_completed=False), "week", 0),
            ("final", _home_away_2025_snapshot(next_completed=True), "final", 1),
        ):
            retained = build_release(
                snapshot,
                self.root / label,
                release_id=label,
                phase=phase,
                target_week=target,
                previous_final=previous,
                code_revision="working-tree",
                timestamp="2025-12-01T00:00:00+00:00",
                published_site=published,
            )
            published = retained.site

        current_snapshot = _forecast_snapshot(next_completed=False)
        prior_final = _previous_final_from_page(
            published / "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html"
        )
        original = _forecast_candidate_for_snapshot(current_snapshot, prior_final)
        original_capability = _authenticated_capability(
            self.root / "original-forecast", original
        )
        timing = GameTimingEvidence(
            original.game,
            _evidence("actual-start"),
            actual_started_at=_kickoff(),
        )

        first = build_release(
            current_snapshot,
            self.root / "2026-first",
            release_id="2026-w0",
            phase="week",
            target_week=0,
            code_revision="working-tree",
            timestamp="2026-01-01T00:00:00+00:00",
            published_site=published,
            forecast_publications=(original_capability,),
            timing_evidence=(timing,),
        )
        first_report = validate_release(
            first,
            published_site=published,
            forecast_publications=(original_capability,),
            timing_evidence=(timing,),
        )
        self.assertTrue(first_report.valid, [str(item) for item in first_report.failures])

        refreshed = build_release(
            current_snapshot,
            self.root / "2026-refresh",
            release_id="2026-w0-refresh",
            phase="week",
            target_week=0,
            code_revision="working-tree",
            timestamp="2026-01-01T01:00:00+00:00",
            published_site=first.site,
            forecast_publications=(original_capability,),
            timing_evidence=(timing,),
        )
        refresh_report = validate_release(
            refreshed,
            published_site=first.site,
            forecast_publications=(original_capability,),
            timing_evidence=(timing,),
        )
        self.assertTrue(refresh_report.valid, [str(item) for item in refresh_report.failures])

        corrected = build_release(
            current_snapshot,
            self.root / "2026-correction",
            release_id="2026-w0-correction",
            phase="week",
            target_week=0,
            code_revision="working-tree",
            timestamp="2026-01-01T02:00:00+00:00",
            published_site=refreshed.site,
            forecast_publications=(original_capability,),
            timing_evidence=(timing,),
            forecast_replacements={
                original.version_id: "pregame model correction"
            },
        )
        correction_report = validate_release(
            corrected,
            published_site=refreshed.site,
            forecast_publications=(original_capability,),
            timing_evidence=(timing,),
        )
        self.assertTrue(
            correction_report.valid,
            [str(item) for item in correction_report.failures],
        )

        first_manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
        refresh_manifest = json.loads(refreshed.manifest_path.read_text(encoding="utf-8"))
        correction_manifest = json.loads(corrected.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            refresh_manifest["runs"][: len(first_manifest["runs"])],
            first_manifest["runs"],
        )
        self.assertEqual(
            correction_manifest["runs"][: len(refresh_manifest["runs"])],
            refresh_manifest["runs"],
        )
        self.assertEqual(refresh_manifest["runs"][-1]["run_kind"], "forecast-evidence-refresh")
        self.assertEqual(correction_manifest["runs"][-1]["run_kind"], "forecast-correction")

        def feature_owner(manifest):
            feature = manifest["progression_feature"]
            return {
                "source": feature["source"],
                "carryover_identity": feature["carryover_identity"],
                "prior_final": feature["prior_final"],
                "artifacts": feature["artifacts"],
                "season_navigation": feature["season_navigation"],
                "source_snapshot": feature["source_snapshot"],
            }

        owner = feature_owner(first_manifest)
        self.assertEqual(owner["source"]["kind"], "retained-run")
        self.assertEqual(owner["source"]["season"], 2025)
        self.assertEqual(feature_owner(refresh_manifest), owner)
        self.assertEqual(feature_owner(correction_manifest), owner)

    def test_forecast_upgrade_sequence_2_to_3_to_4_is_single_upgrade(self):
        """A forecast-bearing v3 predecessor must not trigger a second upgrade."""

        # The old2 archive has no provider identities, so its literal spread
        # pages are valid legacy artifacts.  The migration itself still
        # exercises two same-checkpoint artifact upgrades and independently
        # counts only one crossing into the forecast-bearing contract.
        prior_2025 = self.base / "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html"
        prior_2025.parent.mkdir(parents=True, exist_ok=True)
        prior_2025.write_text(
            "<html><body><table><thead><tr>"
            "<th>school</th><th>cors</th><th>wins_vs_expected</th>"
            "</tr></thead><tbody>"
            "<tr><td>Home</td><td>10</td><td>0</td></tr>"
            "<tr><td>Away</td><td>20</td><td>0</td></tr>"
            "<tr><td>Rival</td><td>15</td><td>0</td></tr>"
            "</tbody></table></body></html>",
            encoding="utf-8",
        )
        previous = PreviousFinal(
            {"Home": 10.0, "Away": 20.0, "Rival": 15.0},
            {"Home": 0.0, "Away": 0.0, "Rival": 0.0},
        )
        snapshot = _contract_migration_snapshot()

        legacy2 = build_release(
            snapshot,
            self.root / "legacy2",
            release_id="legacy2",
            phase="week",
            target_week=0,
            previous_final=previous,
            model_version=MODEL,
            code_revision="contract2-independent",
            timestamp="2026-09-01T12:00:00+00:00",
            published_site=self.base,
        )
        legacy_bytes = {
            relative: (legacy2.site / relative).read_bytes()
            for relative in (
                "cfb/years/2026/spread/2026_W0_FBS_spread.html",
                "cfb/years/2026/spread/2026_W1_FBS_spread.html",
            )
        }
        _rewrite_contract(legacy2, 2, remove_forecast_fields=True)

        frozen3 = build_release(
            snapshot,
            self.root / "frozen3",
            release_id="frozen3",
            phase="week",
            target_week=0,
            previous_final=previous,
            model_version=MODEL,
            code_revision="contract3-independent",
            timestamp="2026-09-02T12:00:00+00:00",
            published_site=legacy2.site,
        )
        _rewrite_contract(frozen3, 3, all_runs=False)
        frozen3_manifest = json.loads(frozen3.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(frozen3_manifest["manifest_version"], FORECAST_ARTIFACT_CONTRACT)
        self.assertEqual(
            sum(run.get("run_kind") == "artifact-contract-upgrade" for run in frozen3_manifest["runs"]),
            1,
        )
        frozen3_report = validate_release(
            frozen3,
            published_site=legacy2.site,
            expected_manifest_version=FORECAST_ARTIFACT_CONTRACT,
        )
        self.assertTrue(frozen3_report.valid, [str(item) for item in frozen3_report.failures])

        current4 = build_release(
            snapshot,
            self.root / "current4-after-3",
            release_id="current4-after-3",
            phase="week",
            target_week=0,
            previous_final=previous,
            model_version=MODEL,
            code_revision="contract4-independent",
            timestamp="2026-09-03T12:00:00+00:00",
            published_site=frozen3.site,
        )
        current4_manifest = json.loads(current4.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(current4_manifest["manifest_version"], CONTRACT4)
        self.assertEqual(
            sum(run.get("run_kind") == "artifact-contract-upgrade" for run in current4_manifest["runs"]),
            2,
        )
        current4_report = validate_release(current4, published_site=frozen3.site)
        self.assertTrue(current4_report.valid, [str(item) for item in current4_report.failures])
        contracts = [int(run["artifact_contract"]) for run in current4_manifest["runs"]]
        self.assertEqual(
            sum(previous < FORECAST_ARTIFACT_CONTRACT <= current
                for previous, current in zip(contracts, contracts[1:])),
            1,
        )
        for relative, contents in legacy_bytes.items():
            self.assertEqual((current4.site / relative).read_bytes(), contents, relative)

    def _mutate_json_cell(self, candidate, relative: str) -> None:
        value = json.loads((candidate.site / relative).read_text(encoding="utf-8"))
        value["rows"][0]["checkpoints"]["W0"]["points"] = 999.0
        (candidate.site / relative).write_bytes(_canonical(value))

    def _mutate_json_extra(self, candidate, relative: str) -> None:
        value = json.loads((candidate.site / relative).read_text(encoding="utf-8"))
        value["forged_extra"] = "accepted only if the validator trusts its own serializer"
        (candidate.site / relative).write_bytes(_canonical(value))

    def _mutate_json_provenance(self, candidate, relative: str) -> None:
        value = json.loads((candidate.site / relative).read_text(encoding="utf-8"))
        value["provenance"]["source_snapshot"] = "0" * 64
        (candidate.site / relative).write_bytes(_canonical(value))

    def _mutate_html(self, candidate, relative: str, old: str, new: str) -> None:
        path = candidate.site / relative
        raw = path.read_text(encoding="utf-8")
        self.assertIn(old, raw)
        path.write_text(raw.replace(old, new, 1), encoding="utf-8")

    def test_current4_cannot_be_resealed_as_frozen3_or_legacy2(self):
        _preseason, _w0, _w1, candidate = self._build_chain()
        original_manifest = candidate.manifest_path.read_bytes()
        original_metadata = candidate.metadata_path.read_bytes()
        manifest = json.loads(original_manifest)
        manifest["manifest_version"] = 3
        manifest["artifact_contract"] = 3
        for run in manifest["runs"]:
            run["artifact_contract"] = 3
        candidate.manifest_path.write_bytes(_canonical(manifest))
        metadata = json.loads(original_metadata)
        metadata["artifact_contract"] = 3
        for run in metadata.get("runs", []):
            run["artifact_contract"] = 3
        candidate.metadata_path.write_bytes(_canonical(metadata))
        _reseal(candidate)
        rejected = validate_release(candidate, published_site=self.base)
        self.assertFalse(rejected.valid)

        # A read-only explicit legacy inspection may accept a genuine frozen
        # archive, but current validation must never silently accept it.  Keep
        # every public artifact byte unchanged while changing only the legacy
        # metadata envelope.
        legacy_site = self.root / "frozen3-site"
        shutil.copytree(candidate.site, legacy_site)
        legacy = type(
            "Legacy",
            (),
            {
                "manifest_path": legacy_site / "manifest.json",
                "metadata_path": legacy_site / "release.json",
                "site": legacy_site,
            },
        )()
        frozen = json.loads(legacy.manifest_path.read_text(encoding="utf-8"))
        progression = {
            path for path in frozen.get("owned_artifacts", []) if "_FBS_progression." in path
        }
        _remove_progression_from_manifest(legacy, remove_current_marker=False)
        for path in progression:
            artifact = legacy_site / path
            if artifact.exists():
                artifact.unlink()
        frozen = json.loads(legacy.manifest_path.read_text(encoding="utf-8"))
        frozen["manifest_version"] = 3
        frozen["artifact_contract"] = 3
        for run in frozen["runs"]:
            run["artifact_contract"] = 3
        legacy.manifest_path.write_bytes(_canonical(frozen))
        release_metadata = json.loads(legacy.metadata_path.read_text(encoding="utf-8"))
        release_metadata["manifest_version"] = 3
        release_metadata["artifact_contract"] = 3
        for run in release_metadata.get("runs", []):
            run["artifact_contract"] = 3
        release_metadata["runs"] = frozen["runs"]
        release_metadata["owned_artifacts"] = frozen["owned_artifacts"]
        release_metadata["required_artifacts"] = frozen["required_artifacts"]
        legacy.metadata_path.write_bytes(_canonical(release_metadata))
        _reseal(legacy)
        frozen_report = validate_release(legacy_site, published_site=self.base, expected_manifest_version=3)
        self.assertTrue(frozen_report.valid, [str(item) for item in frozen_report.failures])
        self.assertFalse(validate_release(legacy_site, published_site=self.base).valid)

        frozen_bytes = {
            path.relative_to(legacy_site).as_posix(): path.read_bytes()
            for path in legacy_site.rglob("*")
            if path.is_file() and path.name not in {"manifest.json", "release.json"}
        }
        upgraded = build_release(
            _next_season_snapshot(0),
            self.root / "upgrade-from-frozen3",
            release_id="upgrade-from-frozen3",
            phase="week",
            target_week=0,
            model_version=MODEL,
            code_revision="contract4-upgrade",
            timestamp="2026-09-15T12:00:00+00:00",
            published_site=legacy_site,
        )
        upgraded_manifest = json.loads(upgraded.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(upgraded_manifest["manifest_version"], CONTRACT4)
        self.assertEqual(upgraded_manifest["artifact_contract"], CONTRACT4)
        _progression_paths(upgraded)
        for relative, contents in frozen_bytes.items():
            if (
                relative.startswith("cfb/years/2025/history/")
                or relative.startswith("cfb/history/")
                or relative.startswith("cfb/years/history/")
                or relative in {
                    "cfb/cfb.html",
                    "cfb/years/2025/2025_CFB.html",
                    "cfb/years/2025/metadata.json",
                }
            ):
                continue
            self.assertEqual((upgraded.site / relative).read_bytes(), contents, relative)
        upgraded_report = validate_release(upgraded, published_site=legacy_site)
        self.assertTrue(upgraded_report.valid, [str(item) for item in upgraded_report.failures])
        self.assertTrue(validate_release(upgraded, published_site=self.base).valid)

        legacy2_site = self.root / "legacy2-site"
        shutil.copytree(legacy_site, legacy2_site)
        legacy2_manifest_path = legacy2_site / "manifest.json"
        legacy2 = json.loads(legacy2_manifest_path.read_text(encoding="utf-8"))
        legacy2["manifest_version"] = 2
        legacy2["artifact_contract"] = 2
        for run in legacy2["runs"]:
            run["artifact_contract"] = 2
        legacy2_manifest_path.write_bytes(_canonical(legacy2))
        legacy2_metadata_path = legacy2_site / "release.json"
        legacy2_metadata = json.loads(legacy2_metadata_path.read_text(encoding="utf-8"))
        legacy2_metadata["manifest_version"] = 2
        legacy2_metadata["artifact_contract"] = 2
        for run in legacy2_metadata.get("runs", []):
            run["artifact_contract"] = 2
        legacy2_metadata_path.write_bytes(_canonical(legacy2_metadata))
        _reseal(type("Legacy", (), {"manifest_path": legacy2_manifest_path, "site": legacy2_site})())
        old2_bytes = {
            path.relative_to(legacy2_site).as_posix(): path.read_bytes()
            for path in legacy2_site.rglob("*")
            if path.is_file() and path.name not in {"manifest.json", "release.json"}
        }
        old2_report = validate_release(legacy2_site, published_site=self.base, expected_manifest_version=2)
        self.assertTrue(old2_report.valid, [str(item) for item in old2_report.failures])
        self.assertFalse(validate_release(legacy2_site, published_site=self.base).valid)
        upgraded2 = build_release(
            _next_season_snapshot(0),
            self.root / "upgrade-from-legacy2",
            release_id="upgrade-from-legacy2",
            phase="week",
            target_week=0,
            model_version=MODEL,
            code_revision="contract4-upgrade-v2",
            timestamp="2026-09-15T12:00:00+00:00",
            published_site=legacy2_site,
        )
        upgraded2_manifest = json.loads(upgraded2.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(upgraded2_manifest["manifest_version"], CONTRACT4)
        self.assertEqual(upgraded2_manifest["artifact_contract"], CONTRACT4)
        _progression_paths(upgraded2)
        for relative, contents in old2_bytes.items():
            if (
                relative.startswith("cfb/years/2025/history/")
                or relative.startswith("cfb/history/")
                or relative.startswith("cfb/years/history/")
                or relative in {
                    "cfb/cfb.html",
                    "cfb/years/2025/2025_CFB.html",
                    "cfb/years/2025/metadata.json",
                }
            ):
                continue
            self.assertEqual((upgraded2.site / relative).read_bytes(), contents, relative)
        self.assertTrue(validate_release(upgraded2, published_site=legacy2_site).valid)
        self.assertTrue(validate_release(upgraded2, published_site=self.base).valid)


if __name__ == "__main__":
    unittest.main()
