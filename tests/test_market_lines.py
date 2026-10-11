"""Independent ATS arithmetic, cached input selection, and full export binding."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from cfb.market_lines import (
    MarketLines, POLICY, SCHEMA, PUBLIC_PATH, compare, enrich, load_inputs,
    provider_for, summarize,
)
from cfb.performance import backforecast, build_performance_files, validate_performance
from cfb.public_site import validate_and_export, validate_public_output
from cfb.public_safety import assert_public_bytes
from tests import test_performance as performance_fixture
from tests.test_public_site import _fixture, REPOSITORY


def observation(year=2020, identifier="1", home="A", away="B", spread="-7", **changes):
    return {"season": year, "game_id": identifier, "phase": "regular", "provider_week": 1,
            "start": f"{year}-09-05T20:00:00Z", "home": home, "away": away,
            "provider": provider_for(year), "home_handicap": spread,
            "retrieved_at": "2026-10-01T12:00:00Z", "response_sha256": "a" * 64, **changes}


def lines(*rows):
    return MarketLines({"schema": SCHEMA, "policy": POLICY, "observations": list(rows)})


def game(cors="-3", home_score=10, away_score=5, **changes):
    return {"week": 1, "home": "A", "away": "B", "home_handicap": cors,
            "home_score": home_score, "away_score": away_score,
            "absolute_error": str(abs(Decimal(home_score - away_score) + Decimal(cors))), **changes}


class MarketArithmeticTests(unittest.TestCase):
    def test_handicap_orientation_underdog_favorite_push_and_no_pick(self):
        cases = [
            ("-3", "-7", 10, 5, "B", "Win", "2"),
            ("-10", "-7", 10, 5, "A", "Loss", "2"),
            ("-3", "-7", 14, 7, "B", "Push", "0"),
            ("-7", "-7", 10, 5, None, "No pick", "2"),
            ("3", "7", 7, 10, "A", "Win", "4"),
            ("10", "7", 7, 10, "B", "Loss", "4"),
            ("0", "0", 7, 7, None, "No pick", "0"),
            ("-0.02", "0", 7, 7, "A", "Push", "0"),
            ("0", "0.02", 7, 7, "A", "Win", "0.02"),
        ]
        for cors, market, hs, aws, pick, outcome, error in cases:
            with self.subTest(cors=cors, market=market):
                result = compare(game(cors, hs, aws), observation(spread=market), 2020)
                self.assertEqual((result["market_pick"], result["market_ats"], result["market_absolute_error"]),
                                 (pick, outcome, error))

    def test_missing_prediction_line_and_pending_never_get_a_grade(self):
        for missing in (None, observation(spread=None)):
            self.assertEqual(compare(game(), missing, 2020)["market_status"], "Missing line")
        self.assertEqual(compare(game(), None, 2012)["market_status"], "No provider selected")
        pending = game()
        pending.update(home_score=None, away_score=None)
        self.assertIsNone(compare(pending, observation(), 2020)["market_ats"])
        self.assertEqual(compare(pending, observation(), 2020)["market_pick"], "B")
        self.assertEqual(compare(game(home_handicap=None), observation(), 2020)["market_status"], "Missing prediction")

    def test_paired_errors_denominators_and_game_weighted_totals(self):
        games = [game("-3", 10, 5), game("-10", 10, 5), game("-3", 14, 7), game("-7", 10, 5), game("-100", 7, 7)]
        for index, row in enumerate(games):
            row.update(compare(row, observation() if index < 4 else None, 2020))
        result = summarize(games)
        self.assertEqual(result["market_games"], 4)
        self.assertEqual((result["market_wins"], result["market_losses"], result["market_pushes"], result["market_no_picks"]), (1, 1, 1, 1))
        self.assertEqual(result["market_ats_percentage"], "0.5")
        self.assertEqual(Decimal(result["paired_cors_mae"]), Decimal("3.25"))
        self.assertEqual(Decimal(result["market_mae"]), Decimal("1.5"))
        self.assertEqual(Decimal(result["paired_cors_rmse"]), (Decimal(49) / 4).sqrt())
        self.assertEqual(Decimal(result["market_rmse"]), Decimal(3).sqrt())
        self.assertEqual(result["missing_market_lines"], 1)
        self.assertIsNone(summarize([])["market_ats_percentage"])


class MarketInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manifest(self, rows):
        raw = json.dumps(rows).encode()
        (self.root / "response.json").write_bytes(raw)
        path = self.root / "inputs.json"
        path.write_text(json.dumps({"schema": "market-inputs/v1", "responses": [{
            "path": "response.json", "season": rows[0]["season"],
            "sha256": hashlib.sha256(raw).hexdigest(), "retrieved_at": "2026-10-01T12:00:00Z"}]}))
        return path

    def raw(self, year=2020, spread=-7):
        return {"id": 1, "season": year, "week": 1, "seasonType": "regular",
                "startDate": f"{year}-09-05T20:00:00Z", "homeTeam": "A", "awayTeam": "B",
                "homeClassification": "fbs", "awayClassification": "fbs",
                "lines": [{"provider": "teamrankings", "spread": spread, "spreadOpen": -99},
                          {"provider": "Bovada", "spread": -2},
                          {"provider": "DraftKings", "spread": -4}]}

    def test_exact_provider_switch_null_zero_nonfinite_and_no_fallback(self):
        for year, expected in ((2012, None), (2013, "-7"), (2020, "-7"), (2021, "-2"), (2026, "-2")):
            with patch("socket.socket", side_effect=AssertionError("network forbidden")):
                result = load_inputs(self.manifest([self.raw(year)]))
            self.assertEqual(result.observations[0]["home_handicap"] if expected else None, expected)
            if expected is None:
                self.assertEqual(result.observations, [])
        for value, expected in ((None, None), (0, "0"), (float("nan"), None), (float("inf"), None)):
            selected = load_inputs(self.manifest([self.raw(spread=value)])).observations[0]
            self.assertEqual(selected["home_handicap"], expected)
            self.assertEqual(selected["provider"], "teamrankings")

    def test_wrong_hash_duplicates_and_unexpected_public_fields_reject(self):
        path = self.manifest([self.raw()])
        (self.root / "response.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "digest"):
            load_inputs(path)
        with self.assertRaisesRegex(ValueError, "duplicate market"):
            load_inputs(self.manifest([self.raw(), self.raw()]))
        row = self.raw()
        row["lines"].append(row["lines"][0])
        with self.assertRaisesRegex(ValueError, "duplicate selected"):
            load_inputs(self.manifest([row]))
        with self.assertRaisesRegex(ValueError, "fields"):
            lines(observation(account="private"))
        with self.assertRaisesRegex(ValueError, "provider"):
            lines(observation(provider="DraftKings"))

    def test_public_projection_excludes_raw_fields_and_local_paths(self):
        row = self.raw()
        row.update(homeScore=12, awayScore=0, account="private", token="private")
        result = load_inputs(self.manifest([row]))
        assert_public_bytes(PUBLIC_PATH, result.to_bytes())
        for forbidden in (b"token", b"account", b"homeScore", b"spreadOpen", b"DraftKings", str(self.root).encode()):
            self.assertNotIn(forbidden, result.to_bytes())
        self.assertEqual(MarketLines(json.loads(result.to_bytes())).to_bytes(), result.to_bytes())

    def test_postseason_rematch_week_zero_and_ambiguous_match(self):
        normal = observation(year=2024, start="2024-09-01T00:00:00Z")
        bowl = observation(year=2024, identifier="2", phase="postseason", start="2025-01-21T00:00:00Z")
        early = observation(year=2024, identifier="3", home="C", start="2024-08-24T20:00:00Z")
        index = lines(normal, bowl, early).index(self.root)
        self.assertIn((2024, 1, "A", "B"), index)
        self.assertIn((2024, 22, "A", "B"), index)
        self.assertIn((2024, 0, "C", "B"), index)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            lines(normal, {**normal, "game_id": "9"}).index(self.root)


class MarketPerformanceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = performance_fixture.PerformanceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.seed()
        self.site = self.fixture.site
        self.market = lines(observation(home="San José State", spread="-7"),
                            observation(identifier="2", home="C", away="D", spread="0"),
                            observation(identifier="3", home="E", away="F", spread=None))

    def test_reports_bind_provider_lines_and_preserve_all_predictions(self):
        saved = self.fixture.spread.read_bytes()
        output = build_performance_files(self.site, market_lines=self.market)
        for name, raw in output.items():
            path = self.site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        validate_performance(self.site)
        report = json.loads(output["cfb/performance/2020/report.json"])
        self.assertEqual(report["summary"]["market_games"], 2)
        self.assertEqual(report["summary"]["missing_market_lines"], 2)
        self.assertEqual(report["games"][0]["market_pick"], "B")
        self.assertEqual(self.fixture.spread.read_bytes(), saved)
        self.assertIn(b"Provider comparison", output["cfb/performance/index.html"])
        self.assertIn(b"market_ats", output["cfb/performance/2020/games.csv"])
        target = self.site / "cfb/performance/2020/games.csv"
        target.write_bytes(target.read_bytes().replace(b"Win", b"Loss"))
        with self.assertRaisesRegex(ValueError, "differs"):
            validate_performance(self.site)

    def test_next_week_forecast_uses_saved_spread_and_is_not_graded(self):
        root = self.fixture.root
        self.fixture.table(root / "data/slate/weekly_slate/2020_W2_FBS_slate.html", [
            {"week": 2, "home_team": "A", "away_team": "B", "neutral_site": True,
             "home_division": "fbs", "away_division": "fbs"}])
        self.fixture.table(root / "spread/2020_W2_FBS_spread.html", [
            {"week": 2, "home_team": "A", "away_team": "B", "neutral_site": True, "spread": "A -3"}])
        market = lines(observation(provider_week=2))
        files = build_performance_files(self.site, market_lines=market)
        report = json.loads(files["cfb/performance/2020/report.json"])
        self.assertEqual(len(report["upcoming"]), 1)
        pending = report["upcoming"][0]
        self.assertEqual(pending["home_handicap"], "-3")
        self.assertEqual(pending["market_pick"], "B")
        self.assertIsNone(pending["market_ats"])
        self.assertEqual(report["summary"]["market_games"], 0)
        self.assertIn(b"Forecasts awaiting results", files["cfb/performance/2020/index.html"])

    def test_public_export_includes_selected_observations_in_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, baseline, sources, firebase, trust = _fixture(root)
            export = root / "public"
            receipt = validate_and_export(candidate, baseline, sources, firebase, export,
                root / "receipt.json", source_root=sources.root, trusted_input_manifest=trust,
                code_root=REPOSITORY, market_lines=self.market)
            self.assertIn(PUBLIC_PATH, receipt.transform["performance"]["paths"])
            self.assertTrue(validate_public_output(export, expected_receipt=receipt).ok)
            original = (export / PUBLIC_PATH).read_bytes()
            (export / PUBLIC_PATH).write_bytes(original.replace(b'"-7"', b'"-8"'))
            self.assertFalse(validate_public_output(export, expected_receipt=receipt).ok)


class MarketAcquisitionTests(MarketInputTests):
    def test_cli_rejects_raw_output_under_real_website_from_another_directory(self):
        from contextlib import chdir, redirect_stderr
        from io import StringIO
        from scripts.fetch_market_lines import main
        meter = self.root / "audit.sqlite3"
        meter.touch()
        target = REPOSITORY / "website/market-cache-test-must-not-exist"
        self.assertFalse(target.exists())
        with chdir(REPOSITORY / "scripts"), patch("scripts.fetch_market_lines.RequestMeter") as request_meter:
            for output in ("../website/market-cache-test-must-not-exist", str(target)):
                with self.subTest(output=output), redirect_stderr(StringIO()) as error:
                    with self.assertRaises(SystemExit) as stopped:
                        main(["--seasons", "2020", "--meter", str(meter), "--output", output])
                    self.assertEqual(stopped.exception.code, 2)
                    self.assertIn("outside website", error.getvalue())
            request_meter.assert_not_called()
        self.assertFalse(target.exists())

    def test_explicit_season_boundary_meters_once_and_never_reuses_claim(self):
        from cfb.request_meter import RequestMeter
        from cfb.season_source import ProductionSeasonSource
        from cfb.season_snapshot import SeasonSnapshotService
        from cfb.snapshot_cache import SnapshotCache
        meter = RequestMeter(self.root / "meter.sqlite3")
        source = ProductionSeasonSource(meter, category="historical")
        service = SeasonSnapshotService(source, SnapshotCache(self.root / "snapshots"))
        response = MagicMock(raw_data=json.dumps([self.raw()]).encode())
        with patch("cfb.season_source.create_api_client"), patch("cfb.season_source.cfbd.BettingApi") as api:
            api.return_value.get_lines_with_http_info.return_value = response
            service.fetch_market_snapshot(2020, self.root / "observation")
            with self.assertRaises(FileExistsError):
                service.fetch_market_snapshot(2020, self.root / "observation")
            self.assertEqual(api.return_value.get_lines_with_http_info.call_count, 1)
        self.assertEqual([(r["endpoint"], r["outcome"]) for r in meter.audit_records()], [("lines", "succeeded")])
        selected = load_inputs(self.root / "observation/inputs.json")
        self.assertEqual(selected.observations[0]["home_handicap"], "-7")

    def test_failed_transport_keeps_claim_consumes_budget_and_redacts_errors(self):
        from cfb.request_meter import MeteredRequestFailed, RequestMeter
        from cfb.season_source import ProductionSeasonSource
        from cfb.season_snapshot import SeasonSnapshotService
        from cfb.snapshot_cache import SnapshotCache
        meter = RequestMeter(self.root / "meter.sqlite3")
        service = SeasonSnapshotService(ProductionSeasonSource(meter), SnapshotCache(self.root / "snapshots"))
        with patch("cfb.season_source.create_api_client", side_effect=RuntimeError("SECRET-SENTINEL")):
            with self.assertRaises(MeteredRequestFailed) as error:
                service.fetch_market_snapshot(2026, self.root / "failed")
        self.assertNotIn("SECRET-SENTINEL", str(error.exception))
        self.assertNotIn("SECRET-SENTINEL", (self.root / "failed/failure.json").read_text())
        self.assertEqual(meter.audit_records()[0]["budget_impact"], 1)
        with self.assertRaises(FileExistsError):
            service.fetch_market_snapshot(2026, self.root / "failed")


if __name__ == "__main__":
    unittest.main()
