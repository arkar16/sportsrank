"""Reader-visible game excitement, pregame cutoffs and retained evidence."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from lxml import html

from cfb.excitement import QualifiedFinal, normalize_timeline
from cfb.reference_archive import Game, Season, Standing
from cfb.excitement_pages import add_score_columns, season_guide
from cfb.game_excitement import (
    Forecast, GameAppeal, Matchup, flow_inputs, frozen_forecasts, read_forecasts, score_for, season_scores, site_reference,
)
from cfb.reference_markup import source_bytes
from scripts.prepare_game_drama import cumulative_quarters, scoring_states, supports_regulation


def sheet(path, title, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = rows[0]
    body = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>" + "".join(
        "<tr>" + "".join(f"<td>{row[k]}</td>" for k in headers) + "</tr>" for row in rows)
    raw = f"<html><head><title>{title}</title></head><body><h1>{title}</h1><table>{body}</table></body></html>".encode()
    path.write_bytes(raw)
    return raw


class ArchiveExcitementTests(unittest.TestCase):
    def test_weekly_rematches_ties_and_missing_ranks_keep_their_own_finals(self):
        from decimal import Decimal
        games = [Game(2, "Chicago", "Visitor", h, a, None) for h, a in ((18, 0), (9, 0))]
        games += [Game(2, "A", "B", n, n, None) for n in (0, 7)]
        games.append(Game(2, "A", "Pending", None, None, None))
        prior = Standing("A", 1, Decimal("10"), "1–0", "")
        later = Standing("B", 1, Decimal("40"), "2–0", "")
        with TemporaryDirectory() as tmp:
            season = Season(1898, {"W1": [prior], "W2": [later]}, games)
            policy = site_reference()
            scores, ledger = season_scores(Path(tmp), season, policy)
            self.assertEqual(len(scores), 5)
            for g in games[:-1]:
                score = score_for(scores, Matchup(g.week, g.home, g.away, False), (g.home_score, g.away_score))
                self.assertEqual(score.final, (g.home_score, g.away_score))
                self.assertEqual(score.tier, "final")
                self.assertTrue(0 <= score.aev <= 100)
            self.assertEqual(score_for(scores, Matchup(2, "A", "B", False), (0, 0)).aev, 83)
            self.assertIsNone(score_for(scores, Matchup(2, "A", "Pending", False)).aev)
            self.assertEqual({r["checkpoint"] for r in ledger["pregame_baselines_v1"]}, {"W1"})
            del season.rankings["W2"]
            self.assertEqual(season_scores(Path(tmp), season, policy)[0], scores)

    def test_early_forecasts_and_historical_quarter_estimates_use_their_own_seasons(self):
        years = Path(__file__).resolve().parents[1] / "website/cfb/years"
        early = read_forecasts(years / "1897", 1897)
        tufts = next(v for k, v in early.items() if k.week == 3 and (k.home, k.away) == ("Brown", "Tufts"))
        self.assertEqual((tufts.checkpoint, tufts.bev()), ("W2", "85.9"))

        final = Game(9, "Tennessee", "South Carolina", 15, 16, None)
        scores, _ = season_scores(years / "2005", Season(2005, {}, [final]), site_reference())
        score = scores[Matchup(9, "Tennessee", "South Carolina", False)]
        self.assertEqual((score.bev, f"{score.aev:.1f}", score.tier), ("39.7", "76.1", "quarter"))

    def test_forecast_uses_only_previous_checkpoint_and_remains_frozen(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            game = dict(week="0", home_team="H", away_team="A", neutral_site="False",
                        spread="H -2", home_cors="20", away_cors="18")
            sheet(base / "spread/2026_W0_FBS_spread.html", "2026 W0 CORS v1.0.0 spreads", [game])
            pre = base / "rankings/2026_PRESEASON_FBS_cors.html"
            teams = [dict(school="H", rank=1, cors=20), dict(school="A", rank=2, cors=18)]
            sheet(pre, "2026 PRESEASON CORS v1.0.0 rankings", teams)
            sheet(base / "rankings/2026_FINAL_FBS_cors.html", "2026 FINAL CORS v1.0.0 rankings",
                  [dict(school="H", rank=93, cors=-27), dict(school="A", rank=1, cors=91)])
            key = Matchup(0, "H", "A", False)
            current = read_forecasts(base, 2026)
            self.assertEqual(current[key].bev(), "98.6")
            path = base / "bev-v1.json"
            frozen, ledger = frozen_forecasts(path, 2026, current)
            path.write_text(json.dumps(ledger))
            pre.write_bytes(pre.read_bytes() + b"\n")
            self.assertEqual(frozen_forecasts(path, 2026, read_forecasts(base, 2026)), (frozen, ledger))
            sheet(pre, "2026 PRESEASON CORS v1.0.0 rankings", [teams[0], dict(school="A", rank=99, cors=18)])
            with self.assertRaisesRegex(ValueError, "frozen BEV"):
                frozen_forecasts(path, 2026, read_forecasts(base, 2026))
            pre.unlink()
            self.assertEqual(read_forecasts(base, 2026), {})  # FINAL never fills a missing checkpoint.

    def test_score_sheet_preserves_original_and_marks_estimates_and_missing_inputs(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "data/results/2026_FBS_results.html"
            rows = [dict(week=1, home_team="H", away_team="A", neutral_site="False", home_score=27, away_score=24),
                    dict(week=2, home_team="H", away_team="B", neutral_site="False", home_score=3, away_score=7)]
            raw = sheet(path, "Scores", rows)
            key = Matchup(1, "H", "A", False)
            scores = {key: GameAppeal("89.1", (27, 24), 77.25, "quarter")}
            page = add_score_columns(raw, base, path, scores, 2026)
            self.assertEqual(source_bytes(page), raw)
            table = html.fromstring(page).xpath("//table")[0]
            self.assertEqual(table.xpath(".//tr[1]/th/abbr/text()"), ["BEV", "AEV"])
            self.assertEqual(table.xpath(".//tr[2]/td/text()")[-2:], ["89.1", "77.2*"])
            self.assertEqual(table.xpath(".//tr[3]/td/text()")[-2:], ["—", "—"])
            changed = {key: GameAppeal("89.1", (27, 21), 44, "full")}
            with self.assertRaisesRegex(ValueError, "displayed"):
                add_score_columns(raw, base, path, changed, 2026)
            scores[Matchup(3, "A", "H", False)] = GameAppeal("94.0", None)
            guide = html.fromstring(season_guide(2026, scores, {}))
            self.assertEqual(len(guide.xpath("//table")), 2)
            self.assertNotIn("AEV", guide.xpath("(//table)[1]//th/text()"))
            self.assertIn("77.2*", guide.xpath("(//table)[2]//td/text()"))

    def test_future_game_gets_no_aev_even_if_the_evidence_directory_has_its_later_final(self):
        key = Matchup(0, "Home", "Away", False)
        evidence = {"matchup": list(key), "game_id": "101", "final": [3, 0],
                    "quarters": [[0, 0], [3, 0], [3, 0]], "overtime": False,
                    "states": [[0, 0, 0, 1], [900, 3, 0, 2], [3600, 3, 0, 4]]}
        with TemporaryDirectory() as tmp, patch("cfb.game_excitement.DATA", Path(tmp)), patch(
            "cfb.game_excitement.read_forecasts",
            return_value={key: Forecast("PRESEASON", 1, 2, "2", "forecast", "rankings")},
        ):
            base = Path(tmp)
            (base / "2026.json").write_text(json.dumps({
                "schema": "espn-scoring-summary/v1", "season": 2026, "source": "revision", "games": [evidence],
            }))
            season = Season(2026, {}, [Game(0, "Home", "Away", None, None, None)])
            before, _ = season_scores(base, season, None)
            self.assertEqual(before[key].bev, "98.6")
            self.assertIsNone(before[key].aev)
            season.games = [Game(0, "Home", "Away", 3, 0, None)]
            after, _ = season_scores(base, season, None)
            self.assertEqual(after[key].bev, before[key].bev)
            self.assertEqual(after[key].tier, "full")
            self.assertTrue(0 < after[key].aev < 100)
            # At 15:00 of Q2 the Q1 score was still 0–0.
            final, points = flow_inputs(evidence, 2026, "revision")
            normalize_timeline(points, final)
            wrong = QualifiedFinal("101", 2026, 3, 0, "revision", overtime=False,
                                   quarter_scores=((3, 0), (3, 0), (3, 0)))
            with self.assertRaisesRegex(ValueError, "quarter"):
                normalize_timeline(points, wrong)
            season.games = [Game(0, "Home", "Away", 0, 3, None)]
            with self.assertRaisesRegex(ValueError, "disagrees with the CORS final"):
                season_scores(base, season, None)

    def test_released_reference_preserves_quarter_scores_and_estimates_from_finals(self):
        base = Path(__file__).resolve().parents[1] / "website/cfb/years/2024"
        duke = Game(2, "Northwestern", "Duke", 20, 26, None, neutral_site=True)
        policy = site_reference()
        self.assertEqual(policy.tiers, {"quarter", "final"})
        scores, _ = season_scores(base, Season(2024, {}, [duke]), policy)
        score = scores[Matchup(2, "Northwestern", "Duke", True)]
        self.assertTrue(score.estimated)
        self.assertEqual(f"{score.aev:.1f}", "70.7")
        self.assertEqual(score.support, 50)
        row = {"matchup": [2, "Northwestern", "Duke", True], "game_id": "401635533",
               "final": [20, 26], "quarters": None, "overtime": True, "reduced_because": "missing quarters"}
        with TemporaryDirectory() as tmp, patch("cfb.game_excitement.DATA", Path(tmp)):
            (Path(tmp) / "2024.json").write_text(json.dumps({
                "schema": "espn-scoring-summary/v1", "season": 2024, "source": "test", "games": [row],
            }))
            limited, _ = season_scores(base, Season(2024, {}, [duke]), policy)
        self.assertEqual(limited[Matchup(*row["matchup"])].tier, "final")
        self.assertGreater(limited[Matchup(*row["matchup"])].aev, 0)

    def test_scoring_summary_checks_scorer_clock_event_order_and_box_score(self):
        teams = [{"linescores": [{"displayValue": str(s)} for s in vals]} for vals in ((0, 7, 0, 0), (0, 0, 0, 3))]
        totals = cumulative_quarters(teams, [7, 3])
        events = [dict(id="a", period={"number": 2}, clock={"value": 600, "displayValue": "10:00"},
                       homeScore=7, awayScore=0, team={"id": "1"}, scoringType={"name": "touchdown"}),
                  dict(id="b", period={"number": 4}, clock={"value": 5, "displayValue": "0:05"},
                       homeScore=7, awayScore=3, team={"id": "2"}, scoringType={"name": "field-goal"})]
        self.assertEqual(scoring_states(events, totals, ["1", "2"]),
                         [[0, 0, 0, 1], [1200, 7, 0, 2], [3595, 7, 3, 4], [3600, 7, 3, 4]])
        for bad in (events[:1], events[::-1], [dict(events[0], team={"id": "2"}), events[1]],
                    [dict(events[0], clock={"value": 500, "displayValue": "10:00"}), events[1]]):
            with self.assertRaises(ValueError):
                scoring_states(bad, totals, ["1", "2"])
        with self.assertRaises(ValueError):
            cumulative_quarters(teams, [8, 3])
        short = {"drives": {"previous": [{"plays": [
            {"period": {"number": q}, "clock": {"displayValue": clock}}
            for q in range(1, 5) for clock in ("10:00" if q == 4 else "15:00", "0:00")
        ]}]}}
        self.assertFalse(supports_regulation(short))
