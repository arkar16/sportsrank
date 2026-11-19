"""Check reader navigation and source compatibility at the static export boundary."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bs4 import BeautifulSoup

from cfb.record_book import build
from cfb.reference_markup import PagePresentation, source_bytes


class SitewideRecordBookTests(unittest.TestCase):
    def test_archived_week_has_context_and_keeps_its_machine_readable_score_table(self):
        raw = (
            '<html>\r\n<head><title>1897 results</title></head><body>'
            '<nav><a href="../../../cfb.html">CORS home</a></nav><h1>1897 W7 results</h1>'
            '</body>\r\n</html>\r\nLast updated: 2024-12-09 19:01:23<hr>'
            '<table class="dataframe"><thead><tr><th>week</th><th>home_team</th><th>home_score</th>'
            '<th>away_team</th><th>away_score</th></tr></thead><tbody>'
            '<tr><td>7</td><td>San José State</td><td>20</td><td>Visitors</td><td>6</td></tr></tbody></table>'
        ).encode("latin-1")
        season = "/cfb/years/1897/1897_CFB.html"
        week = "/cfb/years/1897/checkpoints/W7.html"
        local = {season, week, "/cfb/cfb.html"}
        view = PagePresentation({}, {})
        args = dict(archive=True, relative="cfb/years/1897/data/results/weekly_results/1897_W7_FBS_results.html", targets=local)
        rendered = view.render(raw, **args)
        doc = BeautifulSoup(rendered.decode("latin-1"), "html5lib")
        self.assertEqual(doc.html["lang"], "en")
        self.assertEqual([h.get_text() for h in doc.find_all("h1")], ["Week 7 scores"])
        self.assertEqual(doc.title.text, "1897 Week 7 scores")
        self.assertEqual([a["href"] for a in doc.select('.archive-nav a')], [season, week])
        self.assertEqual([a["href"] for a in doc.select(".masthead nav a")][:2],
                         ["/cfb/cfb.html", "/cbb/index.html"])
        self.assertEqual(doc.select_one("time")["datetime"], "2024-12-09T19:01:23")
        self.assertIsNotNone(doc.main.select_one('input[type="checkbox"]'))
        headers = doc.main.table.thead.find_all("th")
        self.assertEqual([h.text for h in headers], ["week", "home_team", "home_score", "away_team", "away_score"])
        self.assertEqual([h["aria-label"] for h in headers], ["Week", "Home team", "Home score", "Away team", "Away score"])
        self.assertEqual([td.text for td in doc.main.table.tbody.find_all("td")], ["7", "San José State", "20", "Visitors", "6"])
        self.assertEqual(source_bytes(rendered), raw)
        self.assertEqual(view.render(rendered, **args), rendered)

    def test_upcoming_forecast_links_back_to_season_without_inventing_a_checkpoint(self):
        season = "/cfb/years/2026/2026_CFB.html"
        stamp = b"<p>Last updated: 2026-11-16T14:27:47.962519+00:00</p>"
        raw = b"<html><head><title>Forecasts</title></head><body><h1>Forecasts</h1>" + stamp + b"</body></html>"
        output = PagePresentation({}, {}).render(
            raw, archive=True, relative="cfb/years/2026/reconstructed/2026_W12_FBS_spread.html",
            targets={season, "/cfb/years/2026/checkpoints/W11.html", "/cfb/cfb.html"},
        )
        page = BeautifulSoup(output, "html.parser")
        self.assertEqual(page.h1.text, "Week 12 reconstructed forecasts")
        self.assertEqual([a["href"] for a in page.select(".archive-nav a")], [season])
        self.assertIn(stamp, output)
        self.assertEqual(page.time.text, "Updated Nov 16, 2026 · 14:27 UTC")

    def test_offline_export_builds_history_download_and_usable_nested_missing_page(self):
        site = Path(self.enterContext(TemporaryDirectory()))
        rankings = site / "cfb/years/1897/rankings"
        rankings.mkdir(parents=True)
        (rankings / "1897_FINAL_FBS_cors.html").write_text(
            '<html><head></head><body><h1>Final</h1><table><tr><th>rank</th><th>school</th>'
            '<th>conference</th><th>record</th><th>cors</th></tr><tr><td>1</td><td>Princeton</td>'
            '<td>Independent</td><td>1-0</td><td>20.0</td></tr></table></body></html>'
        )
        history = site / "cfb/years/history"
        history.mkdir()
        sheet = history / "CORS_NC_FBS_CFB.html"
        row = ("1897", "Princeton", "Independent", "1-0", "1", "20.0", "8", "0", "1", "0")
        original = ('<table><tbody><tr>' + ''.join(f"<td>{v}</td>" for v in row) + '</tr></tbody></table>\r\n').encode()
        sheet.write_bytes(original)
        build(site)
        self.assertEqual(build(site), (1, 1, 0))
        doc = BeautifulSoup(sheet.read_bytes(), "html.parser")
        download = doc.select_one("a[download]")
        self.assertEqual((sheet.parent / download["href"]).read_bytes(), original)
        self.assertEqual([c.text for c in doc.tbody.tr.find_all(["td", "th"])], list(row))
        self.assertIsNotNone(doc.select_one("header.masthead"))
        missing = BeautifulSoup((site / "404.html").read_bytes(), "html.parser")
        self.assertEqual(missing.h1.text, "Page not found")
        for a in missing.main.select("a"):
            self.assertTrue(a["href"].startswith("/"))
            self.assertTrue((site / a["href"].lstrip("/")).is_file())

    def test_reconstructed_forecast_names_the_rating_used_and_retains_the_archive(self):
        raw = (b'<html><head><title>2025 Forecasts</title></head><body><h1>Forecasts</h1>'
               b'<p>Calculated from prior-week CORS ratings.</p></body></html>')
        for week, checkpoint, label in [(0, "PRESEASON", "Preseason ratings"), (7, "W6", "Week 6 ratings")]:
            with self.subTest(week=week):
                prior = f"/cfb/years/2025/rankings/2025_{checkpoint}_FBS_cors.html"
                relative = f"cfb/years/2025/reconstructed/2025_W{week}_FBS_spread.html"
                view = PagePresentation({}, {})
                rendered = view.render(raw, archive=True, relative=relative, targets={prior})
                page = BeautifulSoup(rendered, "html5lib")
                self.assertEqual(page.title.text, f"2025 Week {week} reconstructed forecasts")
                self.assertEqual(page.main.find("a", href=prior).text, label)
                self.assertEqual(source_bytes(rendered), raw)
                missing = BeautifulSoup(view.render(raw, archive=True, relative=relative, targets=set()), "html5lib")
                self.assertIn(label, missing.main.text)
                self.assertIsNone(missing.main.find("a", href=prior))
