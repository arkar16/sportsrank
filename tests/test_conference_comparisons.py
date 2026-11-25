from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lxml import html

from cbb.honors import write_honors
from cbb.record_book import season_name
from cfb.conference_pages import build_conference_pages
from cfb.record_book import build as build_football
from cfb.reference_archive import Game, Season, Standing
from cfb.reference_markup import PagePresentation


def _fixture(sport):
    rows = [
        Standing("West A", 2, Decimal("8"), "6–2", "West"),
        Standing("West B", 30, Decimal("0"), "3–5", "West"),
        Standing("East A", 3, Decimal("6"), "5–1", "East & Coast"),
        Standing("East B", 27, Decimal("2"), "7–1", "East & Coast"),
        Standing("Valley A", 26, Decimal("1"), "4–4", "Valley"),
        Standing("Valley B", 29, Decimal("-1"), "2–6", "Valley"),
        Standing("Solo Team", 1, Decimal("19"), "7–0", "Solo"),
        Standing("Independent A", 4, Decimal("10"), "6–2", "Indep."),
        Standing("Independent B", 5, Decimal("9"), "6–2", "Indep."),
        Standing("Unaffiliated", 28, Decimal("2"), "2–2", "—"),
    ]
    preseason = [replace(row, rating=row.rating - 2) for row in rows]
    middle = [replace(row, rating=row.rating + 10) if row.conference == "Valley" else row for row in rows]
    current = [replace(row, rating=row.rating - 8) if row.conference == "East & Coast" else row for row in rows]
    pre = "W0" if sport == "basketball" else "PRESEASON"
    games = [Game(1, "East A", "West A", 20, 10, None)]
    seasons = [
        Season(2027, {pre: preseason, "W3": current}, games),
        Season(2025, {pre: preseason, "FINAL": rows}, games),
        Season(2024, {"FINAL": rows[6:]}, []),
        Season(2026, {"FINAL": middle}, []),
        Season(2023, {"FINAL": rows[-1:]}, []),
    ]
    basketball = sport == "basketball"
    prefix = "cbb" if basketball else "cfb"
    routes = {row.school: f"/{prefix}/teams/{row.school.lower().replace(' ', '-')}/index.html" for row in rows}
    options = ({
        "year_label": season_name,
        "season_route": lambda year: f"/cbb/seasons/{year}/index.html",
    } if basketball else {})
    pages = {"/" + path: html.fromstring(markup) for path, markup in build_conference_pages(
        seasons, routes, sport=sport, **options,
    )}
    return prefix, pages


class ConferenceComparisonTest(unittest.TestCase):
    def test_comparisons_use_the_displayed_season_ranks_and_summary(self):
        for sport in ("football", "basketball"):
            with self.subTest(sport=sport):
                prefix, pages = _fixture(sport)
                directory = f"/{prefix}/conferences"
                expected = {
                    2025: ("Final", ["East & Coast", "West", "Valley"]),
                    2027: ("Through Week 3", ["West", "Valley", "East & Coast"]),
                }
                for year, (edition, ranked) in expected.items():
                    dest = f"{directory}/{year}.html"
                    comparison = pages[dest]
                    self.assertEqual(comparison.xpath("//p[@class='edition']/text()"), [edition])
                    rows = comparison.xpath("//tbody/tr")
                    self.assertEqual(
                        [(row[0].text, row[1].text_content()) for row in rows],
                        list(zip(["#1", "#2", "#3", "—", "—"], ranked + ["Indep.", "Solo"], strict=True)),
                    )
                    for row in rows:
                        season_href, = row[1].xpath("a/@href")
                        self.assertTrue(season_href.endswith(f"/{year}.html"))
                        conference_page = pages[season_href]
                        rank, = conference_page.xpath("//dt[text()='Conference rank']/following-sibling::dd")
                        if row[0].text == "—":
                            self.assertEqual(rank.text_content(), "—")
                            self.assertFalse(rank.xpath("a"))
                            self.assertIn(dest, conference_page.xpath("//nav/a/@href"))
                        else:
                            self.assertEqual(rank.text_content(), f"{row[0].text} of 3")
                            self.assertEqual(rank.xpath("a/@href"), [dest])

                    east = next(row for row in rows if row[1].text_content() == "East & Coast")
                    self.assertEqual([cell.text_content() for cell in east[2:]], (
                        ["2", "4.00", "4.00", "1", "4.00", "1–0 (100.0%)", "12–2 (85.7%)", "2.00 (+2.00)"]
                        if year == 2025 else
                        ["2", "-4.00", "-4.00", "1", "4.00", "1–0 (100.0%)", "12–2 (85.7%)", "2.00 (-6.00)"]
                    ))

                self.assertNotIn(f"{directory}/2023.html", pages)
                no_eligible = pages[f"{directory}/2024.html"]
                self.assertIn("No conferences qualify", no_eligible.text_content())
                self.assertEqual(no_eligible.xpath("//tbody/tr/td[1]/text()"), ["—", "—"])
                index = pages[f"{directory}/index.html"]
                self.assertIn(f"{directory}/2027.html", index.xpath("//nav/a/@href"))
                self.assertEqual(index.xpath("//tbody/tr[1]/th/a/text()"), ["West"])

    def test_annual_honors_use_final_ranks_and_sport_specific_season_order(self):
        for sport in ("football", "basketball"):
            with self.subTest(sport=sport):
                prefix, pages = _fixture(sport)
                years = [2025, 2026] if sport == "football" else [2026, 2025]
                finishes = {2025: ["East & Coast", "Valley", "4.00", "0.00"],
                            2026: ["Valley", "West", "10.00", "4.00"]}
                path = f"/{prefix}/history/conference-honors.html"
                self.assertIn(path, pages[f"/{prefix}/conferences/index.html"].xpath("//nav/a/@href"))
                rows = pages[path].xpath("//tbody/tr")
                for row, year in zip(rows, years, strict=True):
                    label = str(year) if sport == "football" else {2025: "2024–25", 2026: "2025–26"}[year]
                    self.assertEqual(row[0].text_content(), label)
                    self.assertEqual([c.text_content() for c in row[1:5]], finishes[year])
                    for cell in row[1:3]:
                        href, = cell.xpath("a/@href")
                        self.assertEqual(pages[href].xpath("//h1/text()"), [f"{label} {cell.text_content()}"])
                    self.assertEqual(row[-1].text_content(), "3")
                    self.assertEqual(row[-1].xpath("a/@href"), [f"/{prefix}/conferences/{year}.html"])

    def test_football_record_book_preserves_new_static_honors_and_team_links(self):
        with TemporaryDirectory() as directory:
            site = Path(directory)
            rankings = site / "cfb/years/2025/rankings"
            rankings.mkdir(parents=True)
            (rankings / "2025_FINAL_FBS_cors.html").write_text(
                "<html><head><title>Rankings</title></head><body>"
                "<table><tr><th>rank</th><th>school</th><th>conference</th><th>record</th>"
                "<th>win_pct</th><th>cors</th></tr>"
                "<tr><td>1</td><td>Alpha</td><td>East</td><td>3–1</td><td>0.75</td><td>4.00</td></tr>"
                "<tr><td>2</td><td>Beta</td><td>East</td><td>1–3</td><td>0.25</td><td>0.00</td></tr>"
                "</table></body></html>",
                encoding="utf-8",
            )
            build_football(site)
            self.assertEqual(build_football(site), (1, 2, 0))
            honor_href = "/cfb/history/conference-honors.html"
            page = html.parse(site / honor_href.lstrip("/"))
            self.assertEqual(len(page.xpath("//main//div[@class='table-scroll']")), 1)
            self.assertTrue(page.xpath("//script[@src='/cfb/assets/sort-history.js']"))
            self.assertEqual(page.xpath("//tbody/tr/td/a/text()"), ["East", "East", "1"])
            for team in ("nc", "wt"):
                for history in ("cfb/history", "cfb/years/history"):
                    teams = html.parse(site / history / f"{team}_FBS_CFB_output.html")
                    self.assertIn(honor_href, teams.xpath("//nav/a/@href"))
            for home in ("index.html", "cfb/cfb.html"):
                self.assertIn(honor_href, html.parse(site / home).xpath("//section//a/@href"))

    def test_basketball_team_honors_link_to_conference_honors(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_honors(root, [], {}, PagePresentation({}, {}), datetime(2026, 11, 25, tzinfo=timezone.utc))
            for name in ("champions", "last-place"):
                page = html.parse(root / "history" / f"{name}.html")
                self.assertIn("/cbb/history/conference-honors.html", page.xpath("//main/nav/a/@href"))
