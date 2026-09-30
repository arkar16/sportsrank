"""Independent acceptance tests for the pure ranking progression boundary.

The expected checkpoint values in this file are literal fixture values.  They
are intentionally not produced by ``ranking_engine`` or by the progression
serializer under test.
"""

import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

from bs4 import BeautifulSoup

from cfb.public_safety import assert_public_bytes
from cfb.ranking_engine import MODEL_VERSION
from cfb.ranking_progression import (
    ProgressionCell,
    ProgressionContractError,
    build_progression,
    progression_json,
    render_progression_html,
)
from cfb.ranking_progression_validation import validate_progression_artifacts
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam


STAMP = "2026-09-29T12:00:00+00:00"
DATASET = "synthetic-2025-reference"
CARRYOVER = "fixture-prior-final"


def _snapshot(*, complete: bool, forged_completion: bool = False) -> SeasonSnapshot:
    """Build a small roster with a real W1 bye and a validated W2 boundary."""

    teams = (
        SourceTeam("Alpha State", "Test Conference"),
        SourceTeam("Independent College", "Independent"),
        SourceTeam("Zulu Tech", "Test Conference"),
    )
    if complete:
        games = (
            SourceGame(
                0,
                "Alpha State",
                "fbs",
                31,
                "Zulu Tech",
                "fbs",
                10,
                False,
                provider_id="fixture-w0",
                completed=True,
                disposition="completed",
            ),
            # Zulu Tech is deliberately on a bye in W1.  Its later rank still
            # changes when Independent College's W1 rating overtakes it.
            SourceGame(
                1,
                "Independent College",
                "fbs",
                20,
                "Alpha State",
                "fbs",
                17,
                False,
                provider_id="fixture-w1",
                completed=True,
                disposition="completed",
            ),
            SourceGame(
                2,
                "Zulu Tech",
                "fbs",
                24,
                "Independent College",
                "fbs",
                21,
                False,
                provider_id="fixture-w2",
                completed=True,
                disposition="completed",
            ),
        )
    else:
        games = (
            SourceGame(
                0,
                "Alpha State",
                "fbs",
                31,
                "Zulu Tech",
                "fbs",
                10,
                False,
                provider_id="fixture-w0",
                completed=True,
                disposition="completed",
            ),
            SourceGame(
                1,
                "Independent College",
                "fbs",
                20,
                "Alpha State",
                "fbs",
                17,
                False,
                provider_id="fixture-w1",
                completed=True,
                disposition="completed",
            ),
            SourceGame(
                2,
                "Zulu Tech",
                "fbs",
                None,
                "Independent College",
                "fbs",
                None,
                False,
                provider_id="fixture-w2",
                completed=False,
                disposition="scheduled",
            ),
        )
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2025,
            "teams_fetched_at": STAMP,
            "games_fetched_at": STAMP,
            # The property under test derives completion from game disposition;
            # this field is deliberately forgeable input metadata in one
            # adversarial case below.
            "complete_through_week": 2 if complete or forged_completion else 1,
        }
    )
    return SeasonSnapshot(
        "cfb",
        "FBS",
        2025,
        teams,
        games,
        metadata,
        _checksum(metadata, teams, games),
    )


def _rows(values: dict[str, tuple[float, int]]) -> list[dict[str, object]]:
    """Turn literal expected values into ranking-shaped input rows."""

    return [
        {"school": school, "points": points, "rank": rank}
        for school, (points, rank) in values.items()
    ]


def _validation_rows(key: str) -> list[dict[str, object]]:
    """Use literal fixture values in the independent validator input shape."""

    return [
        {
            "school": row["school"],
            "conference": (
                "Independent"
                if row["school"] == "Independent College"
                else "Test Conference"
            ),
            "cors": row["points"],
            "rank": row["rank"],
        }
        for row in FULL_RANKINGS[key]
    ]


def _write_validation_artifacts(root: Path) -> tuple[SeasonSnapshot, dict[str, object]]:
    """Write a literal, source-derived artifact pair for validator tests."""

    snapshot = _snapshot(complete=True)
    classification = "FBS"
    year = 2025
    json_path = root / f"cfb/years/{year}/history/{year}_{classification}_progression.json"
    html_path = root / f"cfb/years/{year}/history/{year}_{classification}_progression.html"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    provenance = {
        "sport": "cfb",
        "classification": classification,
        "season": year,
        "dataset_id": DATASET,
        "model_version": MODEL_VERSION,
        "source_snapshot": snapshot.checksum,
    }
    keys = ["PRESEASON", "W0", "W1", "W2", "FINAL"]
    checkpoints = [
        {
            "key": key,
            "kind": "preseason" if key == "PRESEASON" else "final" if key == "FINAL" else "week",
            "week": None if key in {"PRESEASON", "FINAL"} else int(key[1:]),
            "available": True,
            "source_snapshot": snapshot.checksum,
            "dataset_id": DATASET,
            "model_version": MODEL_VERSION,
        }
        for key in keys
    ]
    rows = []
    for school in ("Alpha State", "Independent College", "Zulu Tech"):
        conference = "Independent" if school == "Independent College" else "Test Conference"
        cells = {}
        for key in keys:
            # The independent Release validator treats FINAL as the engine's
            # terminal numbered ranking; use W2 as that literal source value.
            source_key = "W2" if key == "FINAL" else key
            source = next(row for row in FULL_RANKINGS[source_key] if row["school"] == school)
            cells[key] = {
                "available": True,
                "points": source["points"],
                "rank": source["rank"],
            }
        rows.append({"team": school, "conference": conference, "checkpoints": cells})
    payload = {
        "schema_version": 1,
        **provenance,
        "carryover_identity": CARRYOVER,
        "phase": "final",
        "target_week": 2,
        "provenance": provenance,
        "checkpoints": checkpoints,
        "rows": rows,
    }
    json_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    headers = ["Team", "Conference"]
    for key in keys:
        headers.extend((f"{key} Points", f"{key} National rank"))
    header_html = "".join(f"<th>{header}</th>" for header in headers)
    body_html = []
    for row in rows:
        cells = [f"<th>{row['team']}</th>", f"<td>{row['conference']}</td>"]
        for key in keys:
            cell = row["checkpoints"][key]
            cells.extend((f"<td>{cell['points']}</td>", f"<td>{cell['rank']}</td>"))
        body_html.append("<tr>" + "".join(cells) + "</tr>")
    hrefs = [
        f"../{year}_CFB.html",
        f"{year}_{classification}_progression.json",
        *(f"../rankings/{year}_{key}_{classification}_cors.html" for key in keys),
    ]
    nav = "".join(f'<a href="{href}">{href}</a>' for href in hrefs)
    html_path.write_text(
        "<!doctype html><html><head><title>2025 CORS ranking progression — FBS</title></head><body>"
        "<h1>2025 CORS ranking progression — FBS</h1>"
        f"<nav>{nav}</nav><p class=\"provenance\">Season {year} · Dataset {DATASET} · "
        f"Model {MODEL_VERSION} · Source snapshot {snapshot.checksum}</p>"
        f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body_html)}</tbody></table>"
        "</body></html>",
        encoding="utf-8",
    )
    return snapshot, payload


FULL_RANKINGS = {
    "PRESEASON": _rows(
        {
            "Alpha State": (10.0, 3),
            "Independent College": (20.0, 2),
            "Zulu Tech": (30.0, 1),
        }
    ),
    "W0": _rows(
        {
            "Alpha State": (12.5, 3),
            "Independent College": (22.25, 2),
            "Zulu Tech": (31.75, 1),
        }
    ),
    "W1": _rows(
        {
            "Alpha State": (32.0, 2),
            "Independent College": (33.0, 1),
            "Zulu Tech": (31.75, 3),
        }
    ),
    "W2": _rows(
        {
            "Alpha State": (18.0, 2),
            "Independent College": (35.0, 1),
            "Zulu Tech": (17.0, 3),
        }
    ),
    "FINAL": _rows(
        {
            "Alpha State": (40.0, 1),
            "Independent College": (30.0, 2),
            "Zulu Tech": (20.0, 3),
        }
    ),
}


class IndependentRankingProgressionTests(unittest.TestCase):
    def test_literal_values_roster_order_and_provenance_are_preserved(self):
        snapshot = _snapshot(complete=True)
        document = build_progression(
            snapshot,
            FULL_RANKINGS,
            phase="final",
            dataset_id=DATASET,
            carryover_identity=CARRYOVER,
        )

        self.assertEqual(
            [checkpoint.key for checkpoint in document.checkpoints],
            ["PRESEASON", "W0", "W1", "W2", "FINAL"],
        )
        self.assertEqual(
            [team.school for team in document.teams],
            ["Alpha State", "Independent College", "Zulu Tech"],
        )
        self.assertEqual(
            document.provenance,
            {
                "sport": "cfb",
                "classification": "FBS",
                "season": 2025,
                "dataset_id": DATASET,
                "model_version": MODEL_VERSION,
                "source_snapshot": snapshot.checksum,
            },
        )
        self.assertEqual(document.to_dict()["carryover_identity"], CARRYOVER)
        self.assertEqual(
            (document.team("Independent College").cells["W1"].points,
             document.team("Independent College").cells["W1"].rank),
            (33.0, 1),
        )
        self.assertEqual(
            (document.team("Zulu Tech").cells["W1"].points,
             document.team("Zulu Tech").cells["W1"].rank),
            (31.75, 3),
        )
        # The bye leaves Zulu's points unchanged from W0 while its national
        # rank moves after Independent's W1 result.
        self.assertEqual(document.team("Zulu Tech").cells["W0"].points, 31.75)
        self.assertNotEqual(
            document.team("Zulu Tech").cells["W0"].rank,
            document.team("Zulu Tech").cells["W1"].rank,
        )

    def test_incomplete_boundary_exposes_missing_and_future_without_backfill(self):
        snapshot = _snapshot(complete=False)
        rows = {
            "PRESEASON": FULL_RANKINGS["PRESEASON"],
            "W0": FULL_RANKINGS["W0"],
            "W1": {"available": False, "reason": "ranking_not_retained"},
        }
        document = build_progression(
            snapshot,
            rows,
            phase="week",
            target_week=1,
            dataset_id=DATASET,
        )

        self.assertFalse(document.checkpoint("W1").available)
        self.assertEqual(document.checkpoint("W1").reason, "ranking_not_retained")
        self.assertFalse(document.checkpoint("W2").available)
        self.assertEqual(document.checkpoint("W2").reason, "future_checkpoint")
        self.assertFalse(document.checkpoint("FINAL").available)
        self.assertEqual(document.checkpoint("FINAL").reason, "future_checkpoint")
        for team in document.teams:
            self.assertEqual(team.cells["W1"].reason, "ranking_not_retained")
            self.assertEqual(team.cells["W2"].reason, "future_checkpoint")
            self.assertIsNone(team.cells["W2"].points)
            self.assertIsNone(team.cells["W2"].rank)

    def test_no_active_games_infers_preseason_instead_of_week_minus_one(self):
        teams = (
            SourceTeam("Alpha State", "Test Conference"),
            SourceTeam("Independent College", "Independent"),
            SourceTeam("Zulu Tech", "Test Conference"),
        )
        games = ()
        metadata = MappingProxyType(
            {
                "schema_version": 3,
                "sport": "cfb",
                "classification": "FBS",
                "year": 2025,
                "teams_fetched_at": STAMP,
                "games_fetched_at": STAMP,
                "complete_through_week": -1,
            }
        )
        snapshot = SeasonSnapshot(
            "cfb",
            "FBS",
            2025,
            teams,
            games,
            metadata,
            _checksum(metadata, teams, games),
        )

        document = build_progression(snapshot)

        self.assertEqual(document.phase, "preseason")
        self.assertEqual(document.target_week, -1)
        self.assertEqual(
            [checkpoint.key for checkpoint in document.checkpoints],
            ["PRESEASON", "FINAL"],
        )
        self.assertEqual(document.checkpoint("PRESEASON").reason, "ranking_missing")
        self.assertEqual(document.checkpoint("FINAL").reason, "future_checkpoint")

    def test_invalid_checkpoint_and_row_contracts_fail_closed(self):
        snapshot = _snapshot(complete=True)

        duplicate_rows = list(FULL_RANKINGS["W0"])
        duplicate_rows[-1] = dict(duplicate_rows[0])
        with self.assertRaisesRegex(ProgressionContractError, "duplicate school"):
            build_progression(snapshot, {"W0": duplicate_rows}, phase="week", target_week=0)

        outsider_rows = list(FULL_RANKINGS["W0"])
        outsider_rows[0] = {**outsider_rows[0], "school": "Outside University"}
        with self.assertRaisesRegex(ProgressionContractError, "outside the snapshot roster"):
            build_progression(snapshot, {"W0": outsider_rows}, phase="week", target_week=0)

        nonfinite_rows = list(FULL_RANKINGS["W0"])
        nonfinite_rows[0] = {**nonfinite_rows[0], "points": float("nan")}
        with self.assertRaisesRegex(ProgressionContractError, "finite number"):
            build_progression(snapshot, {"W0": nonfinite_rows}, phase="week", target_week=0)

        bad_rank_rows = list(FULL_RANKINGS["W0"])
        bad_rank_rows[0] = {**bad_rank_rows[0], "rank": 1.5}
        with self.assertRaisesRegex(ProgressionContractError, "positive integer"):
            build_progression(snapshot, {"W0": bad_rank_rows}, phase="week", target_week=0)

        mismatched_identity = list(FULL_RANKINGS["W0"])
        mismatched_identity[0] = {**mismatched_identity[0], "season": 2024}
        with self.assertRaisesRegex(ProgressionContractError, "does not match snapshot identity"):
            build_progression(snapshot, {"W0": mismatched_identity}, phase="week", target_week=0)

        fractional_identity = list(FULL_RANKINGS["W0"])
        fractional_identity[0] = {**fractional_identity[0], "season": 2025.5}
        with self.assertRaisesRegex(ProgressionContractError, "does not match snapshot identity"):
            build_progression(snapshot, {"W0": fractional_identity}, phase="week", target_week=0)

        with self.assertRaisesRegex(ProgressionContractError, "source_snapshot"):
            build_progression(
                snapshot,
                {"W0": FULL_RANKINGS["W0"]},
                phase="week",
                target_week=0,
                source_snapshot="wrong-snapshot",
            )

        tampered_snapshot = SeasonSnapshot(
            snapshot.sport,
            snapshot.classification,
            snapshot.year,
            snapshot.teams,
            snapshot.games,
            snapshot.metadata,
            "0" * 64,
        )
        with self.assertRaisesRegex(ProgressionContractError, "checksum verification"):
            build_progression(
                tampered_snapshot,
                {"W0": FULL_RANKINGS["W0"]},
                phase="week",
                target_week=0,
            )

        with self.assertRaisesRegex(ProgressionContractError, "unsupported CORS model"):
            build_progression(
                snapshot,
                {"W0": FULL_RANKINGS["W0"]},
                phase="week",
                target_week=0,
                model_version="v0.0-test",
            )

        bad_metadata = {
            "rows": FULL_RANKINGS["W0"],
            "dataset_id": "different-dataset",
        }
        with self.assertRaisesRegex(ProgressionContractError, "snapshot identity"):
            build_progression(
                snapshot,
                {"W0": bad_metadata},
                phase="week",
                target_week=0,
                dataset_id=DATASET,
            )

        with self.assertRaisesRegex(ProgressionContractError, "duplicate ranking checkpoint"):
            build_progression(
                snapshot,
                {0: FULL_RANKINGS["W0"], "W0": FULL_RANKINGS["W0"]},
                phase="week",
                target_week=0,
            )

        with self.assertRaisesRegex(ProgressionContractError, "beyond"):
            build_progression(
                _snapshot(complete=False),
                {"W2": FULL_RANKINGS["W2"]},
                phase="week",
                target_week=1,
            )

        with self.assertRaisesRegex(ProgressionContractError, "FINAL phase"):
            build_progression(
                _snapshot(complete=False),
                {"W0": FULL_RANKINGS["W0"]},
                phase="final",
                target_week=1,
            )

    def test_final_requires_the_actual_season_edge_and_integral_identity(self):
        complete_snapshot = _snapshot(complete=True)
        with self.assertRaisesRegex(ProgressionContractError, "target_week"):
            build_progression(
                complete_snapshot,
                {"FINAL": FULL_RANKINGS["FINAL"]},
                phase="final",
                target_week=0,
            )

        with self.assertRaisesRegex(ProgressionContractError, "integer"):
            build_progression(
                complete_snapshot,
                {"W0": FULL_RANKINGS["W0"]},
                phase="week",
                target_week=0.5,
            )

        # A forged completion marker cannot turn a scheduled game into FINAL.
        with self.assertRaisesRegex(ProgressionContractError, "FINAL phase"):
            build_progression(
                _snapshot(complete=False, forged_completion=True),
                {"FINAL": FULL_RANKINGS["FINAL"]},
                phase="final",
            )

        # Identity fields must be integral and must not be accepted by
        # truncating an otherwise invalid season value.
        malformed_year = SeasonSnapshot(
            complete_snapshot.sport,
            complete_snapshot.classification,
            2025.5,
            complete_snapshot.teams,
            complete_snapshot.games,
            complete_snapshot.metadata,
            complete_snapshot.checksum,
        )
        with self.assertRaisesRegex(ProgressionContractError, "snapshot identity|year"):
            build_progression(
                malformed_year,
                {"W0": FULL_RANKINGS["W0"]},
                phase="week",
                target_week=0,
            )

    def test_serialized_values_are_static_readable_and_public_safe(self):
        document = build_progression(
            _snapshot(complete=True),
            FULL_RANKINGS,
            phase="final",
            dataset_id=DATASET,
            carryover_identity=CARRYOVER,
        )
        payload = json.loads(progression_json(document))
        self.assertEqual(payload, document.to_dict())
        self.assertEqual(
            payload["rows"][1]["checkpoints"]["W1"],
            {"available": True, "points": 33.0, "rank": 1},
        )
        self.assertEqual(
            set(payload["provenance"]),
            {
                "sport",
                "classification",
                "season",
                "dataset_id",
                "model_version",
                "source_snapshot",
            },
        )
        assert_public_bytes(
            "cfb/years/2025/history/2025_FBS_progression.json",
            progression_json(document).encode("utf-8"),
        )

        html = render_progression_html(document)
        self.assertNotIn("<script", html.lower())
        self.assertIn("CORS points and national rank are shown at each checkpoint", html)
        self.assertLess(html.index("W1 Points"), html.index("W1 National rank"))
        self.assertIn("2025_FBS_progression.json", html)
        self.assertIn("2025_W0_FBS_cors.html", html)
        table = BeautifulSoup(html, "html.parser").find("table")
        self.assertIsNotNone(table)
        rows = table.select("tbody tr")
        self.assertEqual(
            [row.find("th").get_text(strip=True) for row in rows],
            ["Alpha State", "Independent College", "Zulu Tech"],
        )

        page = BeautifulSoup(html, "html.parser")
        viewport = page.find("meta", attrs={"name": "viewport"})
        self.assertIsNotNone(viewport)
        self.assertEqual(viewport.get("content"), "width=device-width, initial-scale=1")
        scroll_region = page.find(
            attrs={"role": "region", "aria-label": "Ranking progression"}
        )
        self.assertIsNotNone(scroll_region)
        self.assertEqual(scroll_region.get("tabindex"), "0")
        described_by = scroll_region.get("aria-describedby")
        self.assertIsNotNone(described_by)
        self.assertIsNotNone(page.find(id=described_by))
        self.assertIn("overflow:auto", page.find("style").get_text())

        provenance = page.find("details")
        self.assertIsNotNone(provenance)
        self.assertNotIn("open", provenance.attrs)
        self.assertEqual(
            provenance.find("summary").get_text(strip=True), "Data provenance"
        )
        self.assertTrue(
            all(header.get("scope") == "col" for header in table.select("thead th"))
        )
        self.assertTrue(
            all(row.find("th").get("scope") == "row" for row in rows)
        )
        style = page.find("style").get_text()
        self.assertRegex(style, r"thead\s+th\{[^}]*position:sticky[^}]*top:0")
        self.assertRegex(style, r"tbody\s+th\{[^}]*position:sticky[^}]*left:0")

    def test_value_objects_reject_ambiguous_unavailable_cells(self):
        with self.assertRaisesRegex(ProgressionContractError, "require a reason"):
            ProgressionCell(available=False)
        with self.assertRaisesRegex(ProgressionContractError, "require points and rank"):
            ProgressionCell(available=True, points=1.0)
        with self.assertRaisesRegex(ProgressionContractError, "cannot carry ranking values"):
            ProgressionCell(available=False, points=1.0, reason="missing")

    def test_direct_construction_and_replace_cannot_reach_unsafe_serializers(self):
        with self.assertRaisesRegex(ProgressionContractError, "finite number"):
            ProgressionCell(available=True, points=float("nan"), rank=1)
        with self.assertRaisesRegex(ProgressionContractError, "integer"):
            ProgressionCell(available=True, points=1.0, rank="<img src=x>")
        with self.assertRaisesRegex(ProgressionContractError, "unsafe text"):
            ProgressionCell(available=False, reason="<script>alert(1)</script>")

        document = build_progression(
            _snapshot(complete=True),
            FULL_RANKINGS,
            phase="final",
            dataset_id=DATASET,
            carryover_identity=CARRYOVER,
        )
        with self.assertRaisesRegex(ProgressionContractError, "phase and target"):
            progression_json(replace(document, phase="preseason", target_week=0))
        with self.assertRaisesRegex(ProgressionContractError, "source_snapshot"):
            render_progression_html(replace(document, source_snapshot="<script>"))

        # ``replace`` validates the cell's own scalar fields, while the
        # output boundary must also reject a positive but impossible rank
        # that only the complete document roster can identify.
        rank_outside_roster = replace(
            document.team("Alpha State").cells["W0"], rank=len(document.teams) + 1
        )
        replaced_team = replace(
            document.team("Alpha State"),
            cells={
                **document.team("Alpha State").cells,
                "W0": rank_outside_roster,
            },
        )
        replaced_document = replace(
            document,
            teams=tuple(
                replaced_team if team.school == "Alpha State" else team
                for team in document.teams
            ),
        )
        with self.assertRaisesRegex(ProgressionContractError, "rank exceeds roster"):
            progression_json(replaced_document)
        with self.assertRaisesRegex(ProgressionContractError, "rank exceeds roster"):
            render_progression_html(replaced_document)

    def test_independent_artifact_validator_rejects_semantic_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot, payload = _write_validation_artifacts(root)
            json_path = root / "cfb/years/2025/history/2025_FBS_progression.json"
            html_path = root / "cfb/years/2025/history/2025_FBS_progression.html"
            rankings = {
                week: _validation_rows(f"W{week}") for week in range(3)
            }

            def validate() -> list[str]:
                return validate_progression_artifacts(
                    root,
                    snapshot,
                    phase="final",
                    target_week=2,
                    preseason=_validation_rows("PRESEASON"),
                    rankings=rankings,
                    model_version=MODEL_VERSION,
                    dataset_id=DATASET,
                    carryover_identity=CARRYOVER,
                )

            self.assertEqual(validate(), [])

            tampered = json.loads(json_path.read_text(encoding="utf-8"))
            tampered["unexpected"] = "must be rejected"
            json_path.write_text(json.dumps(tampered), encoding="utf-8")
            self.assertTrue(validate(), "an unexpected JSON field must fail validation")

            json_path.write_text(
                json.dumps(payload["rows"]) + "\n", encoding="utf-8"
            )
            self.assertTrue(validate(), "a malformed JSON root must fail validation")

            json_path.write_text(
                '{"schema_version":1,"schema_version":1}\n', encoding="utf-8"
            )
            self.assertTrue(validate(), "duplicate JSON keys must fail validation")

            json_path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            tampered = json.loads(json_path.read_text(encoding="utf-8"))
            tampered["rows"][0]["checkpoints"]["W1"]["points"] = 999.0
            json_path.write_text(json.dumps(tampered), encoding="utf-8")
            self.assertTrue(validate(), "a changed point must fail validation")

            json_path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            tampered = json.loads(json_path.read_text(encoding="utf-8"))
            tampered["source_snapshot"] = "different-snapshot"
            json_path.write_text(json.dumps(tampered), encoding="utf-8")
            self.assertTrue(validate(), "changed provenance must fail validation")

            original_html = html_path.read_text(encoding="utf-8")
            html_path.write_text(
                original_html.replace(
                    "2025 CORS ranking progression — FBS",
                    "2024 CORS ranking progression — FBS",
                ),
                encoding="utf-8",
            )
            self.assertTrue(validate(), "a wrong season title or heading must fail validation")

            html_path.write_text(original_html, encoding="utf-8")
            html_path.write_text(original_html.replace("W1 Points", "WRONG"), encoding="utf-8")
            self.assertTrue(validate(), "a changed HTML column must fail validation")

            html_path.write_text(
                original_html.replace(
                    "../rankings/2025_W1_FBS_cors.html", "../rankings/missing.html"
                ),
                encoding="utf-8",
            )
            self.assertTrue(validate(), "a missing permanent link must fail validation")

            html_path.write_text(
                original_html.replace("</body>", "<table></table></body>"),
                encoding="utf-8",
            )
            self.assertTrue(validate(), "a second table must fail validation")

            html_path.write_text(
                original_html.replace(
                    "</body>", "<script>document.body.replaceChildren()</script></body>"
                ),
                encoding="utf-8",
            )
            self.assertTrue(validate(), "executable script markup must fail validation")

            html_path.write_text(
                original_html.replace(
                    "<table>", '<table onclick="alert(1)">'
                ),
                encoding="utf-8",
            )
            self.assertTrue(validate(), "inline event handlers must fail validation")

            html_path.write_text(
                original_html.replace(
                    "</nav>", '<a href="javascript:alert(1)">unsafe</a></nav>'
                ),
                encoding="utf-8",
            )
            self.assertTrue(validate(), "executable URLs must fail validation")


if __name__ == "__main__":
    unittest.main()
