from pathlib import Path
import re
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
_UI = re.compile(r"<!--sr-ui-->.*?<!--/sr-ui-->", re.DOTALL)


class ReportPresentationTests(unittest.TestCase):
    def _decorate(self, source: str, relative: str) -> BeautifulSoup:
        ui_fragments = []

        def ui(markup: str) -> str:
            fragment = f"<!--sr-ui-->{markup}<!--/sr-ui-->"
            ui_fragments.append(fragment)
            return fragment

        rendered = decorate_report(source, relative, ui)
        self.assertEqual(_UI.sub("", rendered).encode(), source.encode())
        self.assertCountEqual(_UI.findall(rendered), ui_fragments)
        self.assertTrue(ui_fragments)

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
                         _SEASON_RESULTS, _LEGACY_RESULTS, _LEGACY_TABLE):
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
        raw = source_bytes((_SITE / _LATIN1_RESULTS).read_bytes())
        rendered = decorate_report(
            raw.decode("latin-1"), _LATIN1_RESULTS,
            lambda markup: f"<!--sr-ui-->{markup}<!--/sr-ui-->",
        )
        self.assertIn('aria-label="On this page"', rendered)
        self.assertEqual(source_bytes(rendered.encode("latin-1")), raw)

    def test_jumps_expose_season_summaries_downloads_and_saved_inputs(self):
        for relative, expected in (
            (_PERFORMANCE_INDEX, ["Provider comparison", "Season summaries"]),
            (_PERFORMANCE_SEASON, ["Provider comparison", "Downloads", "Weekly performance",
                                   "Game results", "Saved inputs"]),
            (_SEASON_RESULTS, ["Provider comparison", "Weekly results", "Game results"]),
        ):
            with self.subTest(relative=relative):
                source = source_bytes((_SITE / relative).read_bytes()).decode("utf-8")
                page = self._decorate(source, relative)
                links = page.select('nav[aria-label="On this page"] a')
                if relative == _PERFORMANCE_SEASON and "<h2>Forecasts awaiting results</h2>" in source:
                    expected.insert(1, "Forecasts awaiting results")
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
        def unexpected_ui(markup: str) -> str:
            self.fail(f"Unexpected presentation on an unrelated page: {markup}")

        source = source_bytes((_SITE / _WEEKLY_RESULTS).read_bytes()).decode("utf-8")
        for relative in (
            "cfb/years/2026/spread/2026_W11_FBS_spread.html",
            "cfb/years/2026/spread/2025_W11_FBS_spread_results.html",
            "/cfb/performance/index.html",
            "cfb/performance/archive/index.html",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(decorate_report(source, relative, unexpected_ui), source)
        fragment = "<h1>Forecast report</h1>"
        self.assertEqual(decorate_report(fragment, _WEEKLY_RESULTS, unexpected_ui), fragment)
