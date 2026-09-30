"""Regression coverage for grading retained, owner-confirmed spread pages."""

from decimal import Decimal
import json
from pathlib import Path
from types import MappingProxyType
import tempfile
import unittest

from cfb.forecast_record import (
    EvidenceRef,
    ForecastCandidate,
    ForecastContractError,
    ForecastProvenance,
    ForecastSelection,
)
from cfb.forecast_release import (
    build_forecast_artifacts,
    canonical_json,
    load_retained_spread_forecasts,
    validate_evaluation_semantics,
    validate_public_forecast_json,
)
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam


def _snapshot() -> SeasonSnapshot:
    teams = tuple(
        SourceTeam(name, "FBS")
        for name in (
            "Home Positive", "Away Positive", "Home Negative", "Away Negative",
            "Home Zero", "Away Zero", "Home Pending", "Away Pending",
            "Home Missing", "Away Missing",
        )
    )
    games = (
        SourceGame(0, "Home Positive", "fbs", 28, "Away Positive", "fbs", 21, False, provider_id="g-positive", completed=True),
        SourceGame(0, "Home Negative", "fbs", 10, "Away Negative", "fbs", 17, False, provider_id="g-negative", completed=True),
        SourceGame(0, "Home Zero", "fbs", 14, "Away Zero", "fbs", 14, False, provider_id="g-zero", completed=True),
        SourceGame(1, "Home Pending", "fbs", None, "Away Pending", "fbs", None, False, provider_id="g-pending", completed=False),
        SourceGame(1, "Home Missing", "fbs", 20, "Away Missing", "fbs", 10, False, provider_id="g-missing", completed=True),
    )
    metadata = {
        "schema_version": 2,
        "sport": "cfb",
        "classification": "FBS",
        "year": 2026,
        "teams_fetched_at": None,
        "games_fetched_at": None,
        "complete_through_week": 0,
        "calendar_provenance": None,
        "correction_registry_provenance": None,
        "migration_provenance": None,
    }
    return SeasonSnapshot(
        "cfb", "FBS", 2026, teams, games, MappingProxyType(metadata),
        _checksum(metadata, teams, games),
    )


def _write_page(root: Path, week: int, rows: list[tuple[str, ...]]) -> Path:
    relative = Path("cfb") / "years" / "2026" / "spread" / f"2026_W{week}_FBS_spread.html"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "week", "home_team", "away_team", "neutral_site", "home_cors",
        "away_cors", "spread_value", "spread",
    )
    table = "<table><thead><tr>" + "".join(f"<th>{item}</th>" for item in header) + "</tr></thead><tbody>"
    table += "".join(
        "<tr>" + "".join(f"<td>{item}</td>" for item in row) + "</tr>"
        for row in rows
    )
    path.write_text(table + "</tbody></table>", encoding="utf-8")
    return path


def _rows() -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    return (
        [
            ("0", "Home Positive", "Away Positive", "False", "10.00", "8.00", "2.50", "Home Positive -2.50"),
            ("0", "Home Negative", "Away Negative", "False", "8.00", "10.00", "-3.0", "Home Negative +3.0"),
            ("0", "Home Zero", "Away Zero", "False", "9.00", "9.00", "0", "Home Zero +0"),
        ],
        [
            ("1", "Home Pending", "Away Pending", "False", "7.00", "6.00", "1.0", "Home Pending -1.0"),
        ],
    )


class PublishedSpreadResultsTests(unittest.TestCase):
    def test_retained_values_grade_without_timing_and_rebuild_stably(self) -> None:
        snapshot = _snapshot()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page0 = _write_page(root, 0, _rows()[0])
            page1 = _write_page(root, 1, _rows()[1])
            candidates, attestations = load_retained_spread_forecasts(
                snapshot,
                (
                    ("cfb/years/2026/spread/2026_W0_FBS_spread.html", page0),
                    ("cfb/years/2026/spread/2026_W1_FBS_spread.html", page1),
                ),
                model_version="v0.4.0",
                home_field_advantage=Decimal("2"),
            )
            by_provider = {item.game.provider_id: item for item in candidates}
            self.assertEqual(by_provider["g-positive"].home_margin, Decimal("2.50"))
            self.assertEqual(by_provider["g-positive"].precision, 2)
            self.assertEqual(by_provider["g-positive"].selection, ForecastSelection.HOME)
            self.assertEqual(by_provider["g-negative"].home_margin, Decimal("-3.0"))
            self.assertEqual(by_provider["g-negative"].precision, 1)
            self.assertEqual(by_provider["g-negative"].selection, ForecastSelection.AWAY)
            self.assertEqual(by_provider["g-zero"].selection, ForecastSelection.LEGACY_UNKNOWN)
            self.assertEqual(len(attestations), len(candidates))

            ledger, evaluation = build_forecast_artifacts(
                snapshot,
                inherited_ledger=root / "ledger.json",
                generated_candidates=candidates,
                forecast_publications=(),
                timing_evidence=(),
                observed_at="2026-09-30T12:00:00+00:00",
                owner_attestations=attestations,
            )
            self.assertEqual(evaluation["season_summary"]["game_count"], 3)
            self.assertEqual(evaluation["season_summary"]["unknown_selections"], 1)
            self.assertEqual(evaluation["season_summary"]["mae"], "2.833333333333333333333333333")
            self.assertEqual(evaluation["season_summary"]["rmse"], "3.476108935769035034246117268")
            missing = next(item for item in evaluation["games"] if item["game"]["provider_id"] == "g-missing")
            self.assertEqual(missing["disposition"], "missing_forecast")
            pending = next(item for item in evaluation["games"] if item["game"]["provider_id"] == "g-pending")
            self.assertEqual(pending["disposition"], "pending")
            self.assertTrue(validate_public_forecast_json(ledger))
            validate_evaluation_semantics(ledger, evaluation, snapshot)

            ledger_path = root / "ledger.json"
            ledger_path.write_bytes(canonical_json(ledger))
            changed = ForecastCandidate.create(
                game=by_provider["g-positive"].game,
                home_margin="99",
                precision=0,
                provenance=ForecastProvenance(
                    "PRESEASON", "before-week-0", "sha256:" + "a" * 64,
                    "sha256:" + "b" * 64, "v0.4.0", "season-snapshot", "rebuild",
                    Decimal("99"), Decimal("1"), Decimal("2"), 1, 2,
                ),
            )
            rebuilt_ledger, rebuilt_evaluation = build_forecast_artifacts(
                snapshot,
                inherited_ledger=ledger_path,
                generated_candidates=(changed,),
                forecast_publications=(),
                timing_evidence=(),
                observed_at="2026-09-30T12:00:00+00:00",
            )
            self.assertEqual(rebuilt_ledger, ledger)
            self.assertEqual(rebuilt_evaluation, evaluation)

    def test_reversed_orientation_cannot_bind_to_a_source_game(self) -> None:
        snapshot = _snapshot()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = _write_page(
                root,
                0,
                [("0", "Away Positive", "Home Positive", "False", "8", "10", "2.5", "Away Positive -2.5")],
            )
            with self.assertRaisesRegex(ForecastContractError, "home orientation"):
                load_retained_spread_forecasts(
                    snapshot,
                    (("cfb/years/2026/spread/2026_W0_FBS_spread.html", page),),
                    model_version="v0.4.0",
                    home_field_advantage=Decimal("2"),
                )


if __name__ == "__main__":
    unittest.main()
