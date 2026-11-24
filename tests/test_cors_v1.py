"""Tests for college football CORS v1.0.0 (kalman-2scale + Other)."""
from __future__ import annotations

from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest

from bs4 import BeautifulSoup

from cfb.cors_v1 import (
    MODEL_VERSION,
    CompletedGame,
    ScheduledGame,
    SeasonInput,
    rebuild_site,
    replay_seasons,
    rounded,
)
from cfb.record_book import build as build_record_book


def _sample_seasons() -> list[SeasonInput]:
    members_2024 = (
        ("Alpha", "East"),
        ("Beta", "East"),
        ("Gamma", "West"),
        ("Delta", "West"),
    )
    games_2024 = (
        CompletedGame(1, "Alpha", "fbs", 35, "Beta", "fbs", 14, False),
        CompletedGame(1, "Gamma", "fbs", 21, "Delta", "fbs", 21, True),
        CompletedGame(1, "Alpha", "fbs", 49, "FCS State", "fcs", 7, False),
        CompletedGame(2, "Beta", "fbs", 24, "Gamma", "fbs", 17, False),
        CompletedGame(2, "Delta", "fbs", 10, "Alpha", "fbs", 31, False),
        CompletedGame(3, "Alpha", "fbs", 28, "Gamma", "fbs", 24, True),
        CompletedGame(3, "Beta", "fbs", 27, "Delta", "fbs", 13, False),
    )
    members_2025 = members_2024 + (("Epsilon", "West"),)
    games_2025 = (
        CompletedGame(1, "Alpha", "fbs", 42, "Epsilon", "fbs", 10, False),
        CompletedGame(1, "Beta", "fbs", 20, "Delta", "fbs", 17, False),
        CompletedGame(2, "Gamma", "fbs", 27, "Alpha", "fbs", 24, False),
        CompletedGame(2, "Epsilon", "fbs", 14, "FCS State", "fcs", 21, False),
    )
    upcoming_2025 = (
        ScheduledGame(3, "Alpha", "fbs", "Beta", "fbs", False),
        ScheduledGame(3, "Gamma", "fbs", "Epsilon", "fbs", True),
    )
    return [
        SeasonInput(2024, members_2024, games_2024, is_final=True),
        SeasonInput(2025, members_2025, games_2025, upcoming_games=upcoming_2025, is_final=False),
    ]


class CorsV1Tests(unittest.TestCase):
    def test_cutoff_invariance_for_fbs_and_fcs_results(self):
        seasons = _sample_seasons()
        base, _ = replay_seasons(seasons)

        # Perturb 2024 Week >= 2 FBS scores by +40; Week 1 spreads and Preseason/W1 must not change.
        perturbed_fbs = []
        for g in seasons[0].completed_games:
            if g.week >= 2 and g.home_division == "fbs" and g.away_division == "fbs":
                perturbed_fbs.append(
                    CompletedGame(
                        g.week, g.home_team, g.home_division, g.home_score + 40,
                        g.away_team, g.away_division, g.away_score, g.neutral_site,
                    )
                )
            else:
                perturbed_fbs.append(g)
        s_pert_fbs = [SeasonInput(2024, seasons[0].members, tuple(perturbed_fbs), is_final=True), seasons[1]]
        pert_fbs, _ = replay_seasons(s_pert_fbs)
        self.assertEqual(base[0].preseason, pert_fbs[0].preseason)
        self.assertEqual(base[0].weekly_spreads[1], pert_fbs[0].weekly_spreads[1])
        self.assertEqual(base[0].weekly_spreads[2], pert_fbs[0].weekly_spreads[2])
        self.assertNotEqual(base[0].weekly_spreads[3], pert_fbs[0].weekly_spreads[3])

        # Perturb 2025 Week 2 FCS game; Week 1 and Week 2 spreads stay identical, Week 3 upcoming changes.
        perturbed_fcs = []
        for g in seasons[1].completed_games:
            if g.week == 2 and "fcs" in (g.home_division, g.away_division):
                perturbed_fcs.append(
                    CompletedGame(
                        g.week, g.home_team, g.home_division, g.home_score + 50,
                        g.away_team, g.away_division, g.away_score, g.neutral_site,
                    )
                )
            else:
                perturbed_fcs.append(g)
        s_pert_fcs = [
            seasons[0],
            SeasonInput(2025, seasons[1].members, tuple(perturbed_fcs), upcoming_games=seasons[1].upcoming_games, is_final=False),
        ]
        pert_fcs, _ = replay_seasons(s_pert_fcs)
        self.assertEqual(base[1].weekly_spreads[1], pert_fcs[1].weekly_spreads[1])
        self.assertEqual(base[1].weekly_spreads[2], pert_fcs[1].weekly_spreads[2])
        self.assertNotEqual(base[1].upcoming_spreads[3], pert_fcs[1].upcoming_spreads[3])

    def test_within_week_game_order_invariance(self):
        seasons = _sample_seasons()
        base, _ = replay_seasons(seasons)
        rng = random.Random(20261124)
        shuffled_seasons = []
        for s in seasons:
            games = list(s.completed_games)
            rng.shuffle(games)
            shuffled_seasons.append(
                SeasonInput(s.year, tuple(reversed(s.members)), tuple(games), upcoming_games=s.upcoming_games, is_final=s.is_final)
            )
        shuf, _ = replay_seasons(shuffled_seasons)
        for r1, r2 in zip(base, shuf):
            self.assertEqual(r1.preseason, r2.preseason)
            self.assertEqual(r1.weekly_rankings, r2.weekly_rankings)
            self.assertEqual(r1.weekly_spreads, r2.weekly_spreads)
            self.assertEqual(r1.upcoming_spreads, r2.upcoming_spreads)

    def test_entrant_prior_zero_centering_and_ties(self):
        seasons = _sample_seasons()
        replays, _ = replay_seasons(seasons)
        # Genesis season preseason starts all teams at 0.00.
        self.assertTrue(all(r["cors"] == "0.00" for r in replays[0].preseason))
        # Tied Week 1 game gives 0-0-1 and 0.50 win_pct.
        w1 = {r["school"]: r for r in replays[0].weekly_rankings[1]}
        self.assertEqual(w1["Gamma"]["record"], "0-0-1")
        self.assertEqual(w1["Gamma"]["win_pct"], "0.50")
        # 2025 entrant Epsilon starts at the bottom of preseason rankings due to -10.0 entrant prior.
        pre25 = replays[1].preseason
        self.assertEqual(pre25[-1]["school"], "Epsilon")
        # Ratings remain zero-centered within rounding tolerance.
        mean_25 = sum(float(r["cors"]) for r in pre25) / len(pre25)
        self.assertAlmostEqual(mean_25, 0.0, places=1)
        # Upcoming Week 3 spreads are populated for active 2025 season.
        self.assertEqual(len(replays[1].upcoming_spreads[3]), 2)
        self.assertIsNone(replays[1].final_ranking)

    def test_rebuild_site_updates_all_pages_and_is_idempotent(self):
        site = Path(self.enterContext(TemporaryDirectory()))

        def write_table(rel: str, headers: tuple[str, ...], rows: list[tuple]) -> None:
            path = site / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            th = "".join(f"<th>{h}</th>" for h in headers)
            tr = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in r) + "</tr>" for r in rows)
            path.write_text(
                f"<html><head><title>CORS v0.4.0</title></head><body>"
                f"<p>Last updated: 2025-01-01T00:00:00Z</p>"
                f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></body></html>",
                encoding="utf-8",
            )

        # Create minimal 2-season archive (2024 FINAL + 2025 active through W2 with W3 slate).
        seasons = _sample_seasons()
        for s in seasons:
            base = f"cfb/years/{s.year}"
            write_table(
                f"{base}/rankings/{s.year}_PRESEASON_FBS_cors.html",
                ("rank", "school", "conference", "record", "win_pct", "cors"),
                [(i, school, conf, "0-0", "0.00", "0.00") for i, (school, conf) in enumerate(s.members, 1)],
            )
            if s.is_final:
                write_table(
                    f"{base}/rankings/{s.year}_FINAL_FBS_cors.html",
                    ("rank", "school", "conference", "record", "win_pct", "cors"),
                    [(i, school, conf, "1-0", "1.00", "10.00") for i, (school, conf) in enumerate(s.members, 1)],
                )
            res_headers = (
                "week", "home_team", "home_division", "home_score",
                "away_team", "away_division", "away_score", "neutral_site",
            )
            all_rows = [
                (g.week, g.home_team, g.home_division, g.home_score,
                 g.away_team, g.away_division, g.away_score, str(g.neutral_site))
                for g in s.completed_games
            ]
            write_table(f"{base}/data/results/{s.year}_FBS_results.html", res_headers, all_rows)
            for w in sorted({g.week for g in s.completed_games}):
                w_rows = [r for r in all_rows if r[0] == w]
                write_table(f"{base}/data/results/weekly_results/{s.year}_W{w}_FBS_results.html", res_headers, w_rows)
                write_table(
                    f"{base}/spread/{s.year}_W{w}_FBS_spread_results.html",
                    ("week", "home_team", "away_team", "spread"),
                    [(w, "Alpha", "Beta", "Alpha -1.0")],
                )
            for g in s.upcoming_games:
                write_table(
                    f"{base}/data/slate/weekly_slate/{s.year}_W{g.week}_FBS_slate.html",
                    ("week", "home_team", "home_division", "away_team", "away_division", "neutral_site"),
                    [
                        (u.week, u.home_team, u.home_division, u.away_team, u.away_division, str(u.neutral_site))
                        for u in s.upcoming_games if u.week == g.week
                    ],
                )
            (site / f"{base}/{s.year}_CFB.html").write_text(
                f"<html><head><title>{s.year} college football · CORS v0.4.0</title></head><body><h1>{s.year}</h1></body></html>",
                encoding="utf-8",
            )

        summary = rebuild_site(site, updated_at="2026-11-24T05:00:00+00:00")
        self.assertEqual(summary["model_version"], MODEL_VERSION)
        self.assertEqual(summary["seasons"], 2)
        self.assertEqual(summary["upcoming_weeks"], {3: 2})

        # Verify record_book is idempotent after rebuild_site.
        self.assertEqual(build_record_book(site), (2, 5, 0))

        # Check homepage, season title, W3 spreads, conference page, team page, national champions, performance.
        home = BeautifulSoup((site / "index.html").read_bytes(), "html.parser")
        self.assertIsNotNone(home.find("a", string="Week 3 forecasts"))
        season25 = (site / "cfb/years/2025/2025_CFB.html").read_text(encoding="utf-8")
        self.assertIn("CORS v1.0.0", season25)
        self.assertNotIn("CORS v0.4.0", season25)
        self.assertTrue((site / "cfb/years/2025/spread/2025_W3_FBS_spread.html").is_file())
        self.assertTrue((site / "cfb/conferences/east/2025.html").is_file())
        self.assertTrue((site / "cfb/teams/alpha/2025.html").is_file())
        self.assertTrue((site / "cfb/years/history/nc_FBS_CFB_output.html").is_file())
        self.assertTrue((site / "cfb/performance/2025/report.json").is_file())
        self.assertIn("Alpha", (site / "cfb/years/history/nc_FBS_CFB_output.html").read_text(encoding="utf-8"))

    def test_rounded_half_away_from_zero(self):
        self.assertEqual(rounded(1.125), 1.13)
        self.assertEqual(rounded(-1.125), -1.13)
        self.assertEqual(rounded(-0.001), 0.0)


if __name__ == "__main__":
    unittest.main()
