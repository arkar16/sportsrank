"""Check reader navigation and source compatibility at the static export boundary."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bs4 import BeautifulSoup

from cfb.record_book import build
from cfb.reference_markup import PagePresentation, source_bytes


class SitewideRecordBookTests(unittest.TestCase):
    def test_homepages_expand_current_rankings_with_movement_results_and_forecasts(self):
        site = Path(self.enterContext(TemporaryDirectory()))
        season = site / "cfb/years/2026"

        def write_table(relative, headers, rows):
            path = season / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            heading = "".join(f"<th>{value}</th>" for value in headers)
            body = "".join("<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>" for row in rows)
            path.write_text(f"<html><body><table><tr>{heading}</tr>{body}</table></body></html>")

        headers = ("rank", "school", "conference", "record", "cors")
        write_table("rankings/2026_W10_FBS_cors.html", headers, [
            (12, "Iowa State", "Big 12", "7-2", 20),
            (22, "Kansas State", "Big 12", "5-4", 19),
            (2, "Texas Tech", "Big 12", "7-2", 19),
            (4, "Utah", "Big 12", "7-2", 19),
        ])
        featured = ("Iowa State", "Kansas State", "Texas Tech", "Utah", "Kansas")
        teams = featured + tuple(f"School {rank:02d}" for rank in range(6, 28))
        write_table("rankings/2026_W11_FBS_cors.html", headers, [
            (rank, school, "ACC" if rank == 27 else "Big 12", "7-3", 20)
            for rank, school in enumerate(teams, 1)
        ])
        write_table("data/results/2026_FBS_results.html",
                    ("week", "home_team", "away_team", "home_score", "away_score",
                     "neutral_site", "home_division", "away_division"), [
                        (10, "Utah", "Iowa State", 14, 0, "false", "fbs", "fbs"),
                        (11, "Iowa State", "Kansas State", 49, 17, "false", "fbs", "fbs"),
                        (11, "Visitors", "Texas Tech", 0, 0, "true", "fcs", "fbs"),
                        (11, "Kansas", "Other School", "", "", "false", "fbs", ""),
                        (12, "Utah", "Kansas", 42, 7, "false", "fbs", "fbs"),
                    ])
        write_table("data/slate/weekly_slate/2026_W12_FBS_slate.html",
                    ("week", "home_team", "away_team", "neutral_site", "home_division", "away_division"), [
                        (12, "Utah", "Samford", "false", "fbs", "fcs"),
                        (12, "School 26", "Other School", "false", "fbs", ""),
                    ])
        forecast = "spread/2026_W12_FBS_spread.html"
        write_table(forecast, ("week", "home_team", "away_team", "neutral_site", "spread"),
                    [(12, "Iowa State", "Kansas State", "false", "Iowa State -3.5")])
        report = site / "cfb/performance/2026/report.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({"upcoming": [
            {"week": 12, "home": "Iowa State", "away": "Kansas State", "home_handicap": -3.5, "neutral_site": False},
            {"week": 12, "home": "Visitors", "away": "Texas Tech", "home_handicap": 0, "neutral_site": True},
            {"week": 13, "home": "Kansas", "away": "School 27", "home_handicap": -4, "neutral_site": False},
        ]}))
        build(site)
        self.assertEqual(build(site), (1, 27, 0))

        for relative in ("index.html", "cfb/cfb.html"):
            with self.subTest(page=relative):
                page = BeautifulSoup((site / relative).read_bytes(), "html.parser")
                top, remaining = page.select("table.home-rankings")
                self.assertEqual([th.get_text(" ", strip=True) for th in top.select("thead th")], [
                    "Rank", "Team", "Conference", "Rank change vs Week 10", "Record", "CORS rating",
                    "Last Week Week 11", "Next Week Week 12 · CORS spread",
                ])
                self.assertEqual([row.th.get_text(strip=True) for row in top.select("tbody tr")], list(teams[:25]))
                self.assertEqual(page.find("a", string="Week 12 forecasts")["href"], f"/cfb/years/2026/{forecast}")
                for row in page.select(".home-rankings tbody tr"):
                    conference = row.th.find_next_sibling("td").a
                    name, slug = ("ACC", "acc") if row.th.get_text(strip=True) == "School 27" else ("Big 12", "big-12")
                    self.assertEqual(conference.get_text(), name)
                    self.assertEqual(conference["href"], f"/cfb/conferences/{slug}/2026.html")
                    self.assertTrue((site / conference["href"].lstrip("/")).is_file())
                disclosure = remaining.find_parent("details")
                self.assertFalse(disclosure.has_attr("open"))
                self.assertEqual(disclosure.summary.get_text(strip=True), "Full rankings (27 teams)")
                self.assertEqual([row.th.get_text(strip=True) for row in remaining.select("tbody tr")], list(teams[25:]))
                rows = top.select("tbody tr")[:5]
                self.assertEqual([row.find_all("td")[2].get_text(strip=True) for row in rows],
                                 ["+11", "+20", "-1", "0", "—"])
                self.assertEqual([row.select(".home-result")[0].get_text(" ", strip=True) for row in rows], [
                    "W vs #22 Kansas State 49–17",
                    "L @ #12 Iowa State 17–49",
                    "T vs (FCS) Visitors (neutral) 0–0",
                    "Bye",
                    "vs Other School No final score",
                ])
                self.assertEqual([row.select(".home-result")[1].get_text(" ", strip=True) for row in rows], [
                    "vs #2 Kansas State -3.5",
                    "@ #1 Iowa State +3.5",
                    "vs Visitors (neutral) Pick’em",
                    "vs (FCS) Samford --",
                    "Bye",
                ])
                link = rows[0].select(".home-result")[0].a["href"]
                self.assertEqual(link, "/cfb/teams/kansas-state/2026.html")
                self.assertTrue((site / link.lstrip("/")).is_file())
                self.assertIsNone(rows[2].select(".home-result")[0].find("a"))
                self.assertEqual([row.select(".home-result")[1].get_text(" ", strip=True)
                                  for row in remaining.select("tbody tr")],
                                 ["vs Other School --", "Bye"])

        reconstructed = season / forecast.replace("spread/", "reconstructed/")
        reconstructed.parent.mkdir()
        (season / forecast).rename(reconstructed)
        build(site)
        home = BeautifulSoup((site / "index.html").read_bytes(), "html.parser")
        self.assertEqual(home.find("a", string="Week 12 reconstructed forecasts")["href"],
                         "/cfb/years/2026/reconstructed/2026_W12_FBS_spread.html")
        reconstructed.unlink()
        build(site)
        home = BeautifulSoup((site / "index.html").read_bytes(), "html.parser")
        self.assertIsNone(home.select_one('a[href$="_FBS_spread.html"]'))

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
            '<th>conference</th><th>record</th><th>win_pct</th><th>cors</th></tr>'
            '<tr><td>1</td><td>Princeton</td><td>Independent</td><td>1-0</td><td>1</td>'
            '<td>20.0</td></tr></table></body></html>'
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
        script = doc.select_one('script[src="/cfb/assets/sort-history.js"][defer]')
        self.assertIsNotNone(script)
        self.assertTrue((site / script["src"].lstrip("/")).is_file())
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

    def test_offline_export_replaces_stale_honors_from_final_ranks_only(self):
        site = Path(self.enterContext(TemporaryDirectory()))
        headers = ("rank", "school", "conference", "record", "win_pct", "cors")
        last_place = ("2", "Beta & Tech", "East", "0-3", "0", "20.00")
        champion = ("1", "Alpha State", "West", "2-1", "0.67", "20.00")
        for year, checkpoint, rows in (
            (2025, "FINAL", (last_place, champion)),
            (2026, "W1", (("1", "Alpha State", "East", "1-0", "1", "24.50"),)),
        ):
            rankings = site / f"cfb/years/{year}/rankings"
            rankings.mkdir(parents=True)
            headings = "".join(f"<th>{field}</th>" for field in headers)
            entries = "".join("<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>" for row in rows)
            (rankings / f"{year}_{checkpoint}_FBS_cors.html").write_text(
                f'<html><head><title>Standings</title></head><body>'
                f'<p>Last updated: 2026-01-20T00:00:00Z</p>'
                f'<table><thead><tr>{headings}</tr></thead><tbody>{entries}</tbody></table></body></html>',
                encoding="utf-8",
            )
        expected = {}
        for directory in ("cfb/history", "cfb/years/history"):
            for prefix, row, kind in (("nc", champion, "National champion"), ("wt", last_place, "Worst team")):
                path = site / directory / f"{prefix}_FBS_CFB_output.html"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("<html><body><table><tr><td>Stale row</td></tr></table></body></html>", encoding="utf-8")
                expected[path] = (["2025", *row[1:], kind], "/cfb/years/2025/2025_CFB.html")

        build(site)
        self.assertEqual(build(site), (2, 2, 0))
        for year, conference, edition in [(2025, "West", "Final"), (2026, "East", "Through Week 1")]:
            landing = BeautifulSoup((site / f"cfb/years/{year}/{year}_CFB.html").read_bytes(), "html.parser")
            link = landing.find("a", string=conference)["href"]
            page = BeautifulSoup((site / link.lstrip("/")).read_bytes(), "html.parser")
            self.assertEqual(page.h1.text, f"{year} {conference}")
            self.assertEqual(page.select_one(".edition").text, edition)
            self.assertEqual(page.find("a", string="Alpha State")["href"], f"/cfb/teams/alpha-state/{year}.html")
        self.assertFalse((site / "cfb/conferences/west/2026.html").exists())
        for path, (row, season_link) in expected.items():
            with self.subTest(route=path.relative_to(site)):
                page = BeautifulSoup(path.read_bytes(), "html.parser")
                self.assertEqual([[cell.get_text() for cell in entry.select("td")]
                                  for entry in page.select("tbody tr")], [row])
                self.assertEqual(page.select_one("tbody tr a")["href"], season_link)
                self.assertTrue(page.select_one("tbody td:nth-child(2) a")["href"].endswith("/2025.html"))
                conference = page.select_one("tbody td:nth-child(3) a")
                self.assertEqual(conference["href"], f"/cfb/conferences/{row[2].lower()}/2025.html")
                self.assertTrue((site / conference["href"].lstrip("/")).is_file())
                self.assertIsNotNone(page.select_one('script[src="/cfb/assets/sort-history.js"][defer]'))
                archive = BeautifulSoup(source_bytes(path.read_bytes()), "html.parser")
                self.assertIsNone(archive.select_one("tbody td:nth-child(3) a"))
                self.assertEqual(archive.select_one("tbody td:nth-child(3)").text, row[2])
                self.assertIsNone(archive.find("script"))
                self.assertIsNotNone(archive.select_one("tbody tr"))

    def test_repeated_school_labels_keep_marks_across_rows_and_game_logs(self):
        routes = {
            "Texas": "/cbb/teams/295/index.html",
            "Florida": "/cbb/teams/87/index.html",
            "Colorado": "/cbb/teams/56/index.html",
        }
        marks = {
            "Texas": "/cbb/assets/team-logos/295.png",
            "Florida": "/cbb/assets/team-logos/87.png",
            "Colorado": "/cbb/assets/team-logos/56.png",
        }
        view = PagePresentation(routes, marks)
        raw = (
            '<html><head></head><body><h1><span data-program="Texas">Texas</span></h1>'
            '<table><tbody>'
            '<tr><td><a href="/cbb/teams/87/2027.html">Florida</a></td></tr>'
            '<tr><td><a href="/cbb/teams/87/2027.html">Florida</a></td></tr>'
            '<tr><td><a href="/cbb/teams/56/2027.html">Colorado</a> by 15</td>'
            '<td><a href="/cbb/teams/56/2027.html">Colorado</a></td></tr>'
            '</tbody></table></body></html>'
        ).encode("utf-8")
        rendered = view.render(raw, archive=False)
        doc = BeautifulSoup(rendered, "html.parser")
        florida_links = doc.select('tbody a[href="/cbb/teams/87/2027.html"]')
        self.assertEqual(len(florida_links), 2)
        self.assertTrue(all(link.find("img", class_="school-mark") is not None for link in florida_links))
        colorado_links = doc.select('tbody a[href="/cbb/teams/56/2027.html"]')
        self.assertEqual(len(colorado_links), 2)
        self.assertTrue(all(link.find("img", class_="school-mark") is not None for link in colorado_links))
        self.assertEqual(source_bytes(rendered), raw)
        self.assertEqual(view.render(rendered, archive=False), rendered)
