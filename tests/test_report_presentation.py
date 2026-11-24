from pathlib import Path
import unittest

from bs4 import BeautifulSoup

from cfb.forecast_html import render_forecast_page
from cfb.reference_markup import source_bytes
from cfb.report_presentation import decorate_report


_SITE = Path(__file__).resolve().parents[1] / "website"
_PERFORMANCE_INDEX = "cfb/performance/index.html"
_PERFORMANCE_SEASON = "cfb/performance/2026/index.html"
_WEEKLY_RESULTS = "cfb/years/2026/spread/2026_W11_FBS_spread_results.html"
_SEASON_RESULTS = "cfb/years/2026/spread/2026_FBS_forecast_results.html"
_LEGACY_RESULTS = "cfb/years/1995/spread/1995_W1_FBS_spread_results.html"
_LATIN1_RESULTS = "cfb/years/1950/spread/1950_W10_FBS_spread_results.html"
_LEGACY_TABLE = "cfb/years/2024/spread/2024_W0_FBS_spread_results.html"


class ReportPresentationTests(unittest.TestCase):
    def _decorate(self, source: str, relative: str) -> BeautifulSoup:
        rendered = decorate_report(source, relative)
        self.assertEqual(source_bytes(rendered.encode()), source.encode())

        page = BeautifulSoup(rendered, "html.parser")
        self.assertEqual(len(page.select("h1")), 1)
        self.assertEqual(len(page.select('head link[href="/cfb/assets/report-pages.css"]')), 1)
        ids = [tag["id"] for tag in page.select("[id]")]
        self.assertEqual(len(ids), len(set(ids)))
        for link in page.select('.sr-report nav[aria-label="On this page"] a'):
            self.assertIn(link["href"].removeprefix("#"), ids)
        return page

    def test_archived_reports_keep_their_cells_and_links(self):
        for relative in (_PERFORMANCE_INDEX, _PERFORMANCE_SEASON, _WEEKLY_RESULTS,
                         _SEASON_RESULTS, _LEGACY_RESULTS, _LATIN1_RESULTS, _LEGACY_TABLE):
            with self.subTest(relative=relative):
                raw = source_bytes((_SITE / relative).read_bytes())
                source = raw.decode("utf-8")
                page = self._decorate(source, relative)
                original = BeautifulSoup(source, "html.parser")
                self.assertEqual(
                    [str(table) for table in page.select("table")],
                    [str(table) for table in original.select("table")],
                )
                self.assertEqual(
                    [link["href"] for link in page.select("a[href]") if not link["href"].startswith("#sr-report-")],
                    [link["href"] for link in original.select("a[href]")],
                )

    def test_latin1_archived_results_roundtrip_without_transcoding(self):
        raw = (
            "<html><head><title>1950 W10 Results</title></head><body><h1>1950 W10 Results</h1>"
            "<h2>Summary</h2><p>Total Games: 51</p>"
            "<p>Against The Spread:</p><ul><li>Correct Predictions: 25</li><li>Accuracy: 49.0%</li></ul>"
            "<p>Straight Up:</p><ul><li>Correct Predictions: 33</li><li>Accuracy: 64.7%</li></ul><hr>"
            "<h2>Results</h2><table><thead><tr><th>home_team</th><th>away_team</th></tr></thead>"
            "<tbody><tr><td>San José State</td><td>Utah</td></tr></tbody></table></body></html>"
        ).encode("latin-1")
        rendered = decorate_report(raw.decode("latin-1"), _LATIN1_RESULTS)
        page = BeautifulSoup(rendered, "html.parser")
        self.assertIsNotNone(page.find(id="sr-report-results"))
        self.assertIn("25 correct (49.0%)", page.select_one(".metrics").text)
        self.assertIn("33 correct (64.7%)", page.select_one(".metrics").text)
        self.assertEqual(source_bytes(rendered.encode("latin-1")), raw)

    def test_jumps_expose_season_summaries_downloads_and_saved_inputs(self):
        for relative, expected in (
            (_PERFORMANCE_INDEX, ["Market", "Season summaries"]),
            (_PERFORMANCE_SEASON, ["Market", "Downloads", "By week", "Games", "Sources"]),
            (_SEASON_RESULTS, ["Market", "Weekly results", "Games"]),
        ):
            with self.subTest(relative=relative):
                source = source_bytes((_SITE / relative).read_bytes()).decode("utf-8")
                page = self._decorate(source, relative)
                links = page.select('nav[aria-label="On this page"] a')
                if relative == _PERFORMANCE_SEASON and "<h2>Forecasts awaiting results</h2>" in source:
                    expected.insert(1, "Ungraded forecasts")
                self.assertEqual([link.get_text() for link in links], expected)
                if relative == _PERFORMANCE_SEASON:
                    self.assertEqual(
                        [link["href"] for link in page.select("#sr-report-downloads a")],
                        ["games.csv", "weekly.csv", "report.json"],
                    )
                    self.assertTrue(page.select_one("#sr-report-saved-inputs details li a")["href"])
                if relative == _SEASON_RESULTS:
                    self.assertTrue(page.select_one('nav[aria-label="Weekly forecast results"] a')["href"])

    def test_generated_forecast_keeps_its_scroll_region_and_avoids_id_collision(self):
        source = render_forecast_page(
            "Forecast Results", "2026-11-16",
            '<table id="sr-report-game-results"><thead><tr><th>Week</th><th>Home</th><th>Away</th>'
            '<th>Home score</th></tr></thead><tbody><tr><td>11</td><td>Oregon</td><td>Washington</td>'
            '<td>34</td></tr></tbody></table><h2>Weekly summary</h2>',
        )
        page = self._decorate(source, _WEEKLY_RESULTS)
        self.assertEqual(page.select_one('nav[aria-label="On this page"] a')["href"],
                         "#sr-report-game-results-2")
        self.assertEqual(page.select_one("h2#sr-report-game-results-2").get_text(), "Game results")
        self.assertIsNotNone(page.select_one(
            '.sr-report-matchups > .forecast-table-scroll[aria-label="Games"] > table.forecast-games'
        ))

    def test_leaves_other_paths_and_unrecognized_markup_untouched(self):
        source = source_bytes((_SITE / _WEEKLY_RESULTS).read_bytes()).decode("utf-8")
        for relative in (
            "cfb/years/2026/spread/2026_W11_FBS_spread.html",
            "cfb/years/2026/spread/2025_W11_FBS_spread_results.html",
            "/cfb/performance/index.html",
            "cfb/performance/archive/index.html",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(decorate_report(source, relative), source)
        fragment = "<h1>Forecast report</h1>"
        self.assertEqual(decorate_report(fragment, _WEEKLY_RESULTS), fragment)

    def test_scoring_rules_and_market_coverage_match_the_season(self):
        for year in (1897, 2026):
            with self.subTest(year=year):
                route = f"cfb/performance/{year}/index.html"
                raw = source_bytes((_SITE / route).read_bytes()).decode()
                page = self._decorate(raw, route)
                rules = {d.summary.text: d.get_text(" ", strip=True) for d in page.find_all("details")}
                self.assertIn("excluding pushes", rules["Scoring rules"])
                self.assertIn("even spread", rules["Scoring rules"])
                self.assertNotIn("cfb/years/", rules["Sources"])
                self.assertIn("Week 1 scores", rules["Sources"])
                self.assertNotIn("0 unavailable", page.get_text(" ", strip=True))
                if year == 1897:
                    self.assertIn("Market lines start in 2013.", page.get_text())
                    self.assertNotIn("0 paired games", page.get_text())
                else:
                    self.assertIn("Closing-line status is unknown", rules["Lines and grading"])
                    self.assertIn("Both margin errors use the same games", rules["Lines and grading"])

    def test_nonzero_omissions_survive_even_when_there_is_no_provider_comparison(self):
        text = ('<html><head></head><body><h1>Forecast grades</h1>'
                '<p>1 unavailable · 2 pending scores · 0 non-FBS games excluded.</p>'
                '<p>League qualification changed for this edition.</p>'
                '<table><thead><tr><th>Week</th></tr></thead><tbody></tbody></table></body></html>')
        page = self._decorate(text, _PERFORMANCE_SEASON)
        self.assertIn("1 missing forecast · 2 scores pending", page.get_text())
        self.assertIn("League qualification changed for this edition.", page.get_text())

    def test_fcs_archive_does_not_claim_fbs_game_coverage(self):
        route = "cfb/years/2025/spread/2025_W1_FCS_spread_results.html"
        source = ('<html><head></head><body><h1>Forecast results</h1>'
                  '<p class="metrics"><strong>1 games</strong> · Winner accuracy 100% (1–0; 0 ties)</p>'
                  '<table><thead><tr><th>Week</th></tr></thead><tbody></tbody></table></body></html>')
        page = self._decorate(source, route)
        self.assertEqual(page.select_one(".metrics").text, "1 game · Winner accuracy 100% (1–0)")
