from dataclasses import replace
from decimal import Decimal
from math import gamma, pi, sqrt
import unittest

from lxml import html
import numpy as np

from cbb.cors_v1 import _update
from cfb.conference_pages import _football_deltas, build_conference_pages
from cfb.win_probability import win_probability
from cfb.reference_archive import Game, Season, Standing, _postseason


def standing(name, rank, cors, record, conference):
    return Standing(name, rank, Decimal(cors), record, conference)


def cells(row):
    return [cell.text_content() for cell in row]


class ConferencePagesTest(unittest.TestCase):
    def test_conference_standings_use_regular_season_and_project_only_regular_schedule(self):
        rows = [
            standing("A", 1, "10", "1–2", "East"),
            standing("B", 3, "7", "2–1", "East"),
            standing("C", 4, "2", "0–1", "West"),
            standing("D", 5, "-3", "0–0", "West"),
            standing("I", 2, "8", "0–0", "Indep."),
        ]
        preseason = [replace(row, rating=row.rating - Decimal("2.00")) for row in rows]
        games = [
            Game(0, "B", "A", 75, 70, None, conference_game=True, home_cors_delta=1.25, away_cors_delta=-1.1),
            Game(0, "A", "B", 80, 70, None, conference_game=False, home_cors_delta=3.2, away_cors_delta=-2.0),
            Game(0, "A", "B", 68, 70, None, postseason=True, conference_game=True,
                 home_cors_delta=-0.5, away_cors_delta=0.6),
            Game(0, "B", "C", 74, 66, None, home_cors_delta=0.2, away_cors_delta=-0.7),
            Game(1, "A", "B", None, None, Decimal(0)),
            Game(1, "A", "D", None, None, Decimal(0)),
            Game(1, "A", "C", None, None, Decimal(0), postseason=True),
        ]
        routes = {row.school: f"/cbb/teams/{row.school}/index.html" for row in rows}
        pages = dict(build_conference_pages(
            [Season(2024, {"W0": preseason, "FINAL": rows}, []),
             Season(2025, {"W0": preseason, "W1": rows}, games)], routes, sport="basketball"))
        page = html.fromstring(pages["cbb/conferences/east/2025.html"])
        summary = dict(zip(page.xpath("//dl//dt/text()"), (dd.text_content() for dd in page.xpath("//dl//dd")), strict=True))
        self.assertEqual(summary["Conference rank"], "#1 of 2")
        self.assertEqual(summary["Combined OOC"], "1–0 (100.0%)")
        self.assertEqual(summary["Preseason Mean CORS"], "6.50 (+2.00)")
        table, ooc = page.xpath("//table[@data-sortable]")
        self.assertEqual(cells(table.xpath(".//thead/tr")[0]), [
            "National rank", "Team", "Overall", "Conf", "CORS rating", "Pre",
            "Best win (Δ CORS)", "Worst loss (Δ CORS)", "Proj conf", "Proj overall"])
        b, a = [cells(tr) for tr in table.xpath(".//tbody/tr")]
        self.assertEqual(b[:4], ["3", "B", "2–1", "1–0"])
        self.assertEqual(a[:4], ["1", "A", "1–2", "0–1"])
        self.assertEqual(a[6:], ["W 80–70 vs #3 B (+3.20)", "L 70–75 @ #3 B (-1.10)", "0.5–1.5", "2.0–3.0"])
        self.assertEqual(ooc.xpath(".//tbody/tr/th/a/@href"), ["/cbb/conferences/west/2025.html"])
        self.assertEqual(cells(ooc.xpath(".//tfoot/tr")[0]), ["Total OOC", "1–0", "100.0%", "+8.0"])
        final = html.fromstring(pages["cbb/conferences/east/2024.html"])
        self.assertEqual(len(final.xpath("//table")), 1)
        self.assertNotIn("Proj conf", final.text_content())
        independents = html.fromstring(pages["cbb/conferences/indep/2025.html"])
        self.assertEqual(cells(independents.xpath("//tbody/tr")[0])[3], "—")
        directory = html.fromstring(pages["cbb/conferences/index.html"])
        self.assertEqual(directory.xpath("//tbody/tr[1]/th/a/@href"), ["/cbb/conferences/east/2025.html"])
        self.assertIn("/cbb/conferences/indep/index.html", directory.xpath("//ul[@class='program-list']//a/@href"))

    def test_football_weekly_deltas_and_calendar(self):
        a, b = standing("A", 1, "1", "0–0", "East"), standing("B", 2, "-1", "0–0", "East")
        new_a, new_b = replace(a, rating=Decimal("4.50")), replace(b, rating=Decimal("-2.50"))
        games = [Game(1, "A", "B", 28, 7, Decimal(-3)), Game(1, "C", "A", 17, 14, Decimal(3))]
        season = Season(2025, {"PRESEASON": [a, b], "W1": [new_a, new_b]}, games)
        deltas = _football_deltas(season, games)
        self.assertAlmostEqual(deltas[0, "A"] + deltas[1, "A"], 3.5)
        self.assertGreater(deltas[0, "A"], deltas[1, "A"])
        self.assertEqual(deltas[0, "B"], -1.5)
        self.assertFalse(_postseason(2025, 14, ""))
        self.assertTrue(_postseason(2025, 15, ""))
        self.assertTrue(_postseason(2025, 19, ""))
        self.assertFalse(_postseason(2026, 13, ""))

    def test_student_t_win_probability_matches_integrated_density(self):
        nu = 14
        density_constant = gamma((nu + 1) / 2) / (sqrt(nu * pi) * gamma(nu / 2))
        for sport, sigma in (("basketball", 11), ("football", 15)):
            for margin in (-50, -5, 0, 5, 50):
                upper = margin / (sigma * sqrt((nu - 2) / nu))
                step = upper / 10000
                integral = sum(
                    density_constant * (1 + ((i + .5) * step)**2 / nu)**(-(nu + 1) / 2)
                    for i in range(10000)
                ) * step
                self.assertAlmostEqual(win_probability(margin, sport=sport), .5 + integral, places=8)

    def test_basketball_game_delta_matches_omitting_game_from_same_fit(self):
        mu = np.array([2., -2., 0., -26., 3.6])
        cov = np.diag([9., 13., 10., 25., 1.])
        games = [(0, 1, 9., 1), (1, 2, 20., 0), (0, 2, -8., 1)]
        deltas = []
        full_mu, full_cov = _update(mu, cov.copy(), games, True, game_deltas=deltas)
        original_mu, original_cov = _update(mu, cov.copy(), games, True)
        np.testing.assert_array_equal(full_mu, original_mu)
        np.testing.assert_array_equal(full_cov, original_cov)
        for i, (home, away, _, _) in enumerate(games):
            omitted_mu, _ = _update(mu, cov.copy(), games[:i] + games[i + 1:], True)
            diff = full_mu[:3] - omitted_mu[:3]
            diff -= diff.mean()
            np.testing.assert_allclose(deltas[i], diff[[home, away]], atol=1e-12, rtol=0)
