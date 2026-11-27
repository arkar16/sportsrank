"""Read the same forecast, share page and social metadata as a visitor."""
from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PIL import Image

from cbb.home import landing_page
from cbb.site import share_conferences
from cfb.reference_archive import Game, Season, Standing
from cfb.reference_markup import PagePresentation, source_bytes
from spread_cards import basketball_links, football_links


class GameSharingTests(unittest.TestCase):
    def test_football_shares_positive_home_handicap_and_pregame_record(self):
        directory = Path(self.enterContext(TemporaryDirectory())) / "cfb"
        path = directory / "years/2026/spread/2026_W0_FBS_spread.html"
        season = Season(2026, {
            "PRESEASON": [Standing("Home", 24, Decimal(4), "0-0", "ACC")],
            "W0": [Standing("Home", 2, Decimal(7), "1-0", "ACC")],
        }, [Game(0, "Home", "Away & Co.", 21, 17, None, conference_game=False)])
        raw = (b'<html><body><h1>Week 0</h1><table><tr><th>week</th><th>home_team</th>'
               b'<th>away_team</th><th>neutral_site</th><th>spread</th></tr>'
               b'<tr><td>0</td><td>Home</td><td>Away &amp; Co.</td><td>False</td>'
               b'<td>Home +2.50</td></tr></table></body></html>')
        linked = football_links(raw, path, directory, season)
        self.assertEqual(source_bytes(linked), raw)
        self.assertEqual(football_links(linked, path, directory, season), linked)
        link = BeautifulSoup(linked, "html.parser").find("a")
        self.assertEqual(link.get_text(strip=True), "Home +2.50")
        page = BeautifulSoup((directory.parent / link["href"][1:]).read_text(), "html.parser")
        self.assertEqual(page.h1.text, "Away & Co. @ Home")
        self.assertTrue(link["href"].endswith("/away-co-at-home.html"))
        forecast = "Home to lose by 2.50 · 43% to win"
        self.assertEqual(page.select_one(".pick strong").text, forecast)
        self.assertEqual(page.select_one('meta[property="og:title"]')["content"], f"Away & Co. @ Home · {forecast}")
        self.assertIn(forecast, page.title.text)
        self.assertEqual(page.select_one(".game-details").text, "OOC · @ Home")
        self.assertEqual([td.text for td in page.select(".game-line td")], ["—", "0-0 (0-0)"])
        self.assertEqual([tag.text for tag in page.select(".rank")], ["", "#24"])
        self.assertEqual(page.main.get_text().count("2.50"), 1)
        self.assertEqual(page.find("link", rel="canonical")["href"], "https://www.sportsrank.top" + link["href"])
        self.assertEqual(page.select_one("a.back")["href"], "/cfb/years/2026/spread/2026_W0_FBS_spread.html")
        self.assertIsNone(page.select_one('meta[property="og:image"], link[rel="apple-touch-icon"]'))

    def test_large_home_mark_is_only_advertised_as_a_compact_thumbnail(self):
        site = Path(self.enterContext(TemporaryDirectory()))
        directory = site / "cfb"
        mark = "/cfb/assets/program-marks/home.png"
        source = site / mark[1:]
        source.parent.mkdir(parents=True)
        Image.new("RGBA", (500, 500), "crimson").save(source)
        raw = ('<table><tr><th>home_team</th><th>away_team</th><th>neutral_site</th><th>spread</th></tr>'
               f'<tr><td><img class="school-mark" src="{mark}">Home</td><td>Away</td><td>False</td>'
               '<td>Home -1.75</td></tr></table>').encode()
        slate = directory / "years/2026/spread/2026_W1_FBS_spread.html"
        season = Season(2026, {"W0": [Standing("Away", 2, Decimal(7), "0-0", "SEC"),
                                      Standing("Home", 3, Decimal(5), "0-0", "ACC")]},
                        [Game(1, "Home", "Away", None, None, None)])
        linked = football_links(raw, slate, directory, season)
        href = BeautifulSoup(linked, "html.parser").select_one("a.share-spread")["href"]
        game = BeautifulSoup((site / href[1:]).read_text(), "html.parser")
        self.assertEqual(game.select_one(".game-details").text, "OOC (SEC vs ACC) · @ Home")
        self.assertEqual(game.select_one(".game-line img")["src"], mark)
        url = game.select_one('meta[property="og:image"]')["content"]
        for selector, attr in [('link[rel="apple-touch-icon"]', "href"), ('link[rel="icon"]', "href"),
                               ('meta[name="twitter:image"]', "content")]:
            self.assertEqual(game.select_one(selector)[attr], url)
        self.assertEqual(urlsplit(url).netloc, "www.sportsrank.top")
        with Image.open(site / urlsplit(url).path[1:]) as icon:
            self.assertEqual(icon.width, icon.height)
            self.assertTrue(108 <= icon.width < 150)
            self.assertEqual(icon.convert("RGB").getpixel((icon.width // 2, icon.height // 2)), (220, 20, 60))
            for dimension in ("width", "height"):
                tag = game.select_one(f'meta[property="og:image:{dimension}"]')
                self.assertEqual(int(tag["content"]), getattr(icon, dimension))

    @unittest.skipUnless(shutil.which("node"), "Node.js exercises the image download")
    def test_download_keeps_the_full_card_inside_a_message_attachment(self):
        subprocess.run(["node", Path(__file__).with_name("spread_card_download.cjs")], check=True, timeout=15)

    def test_basketball_rematches_pickem_and_neutral_court(self):
        site = Path(self.enterContext(TemporaryDirectory()))
        directory = site / "cbb"
        edition = directory / "editions/2026-11-24T193000Z.html"
        edition.parent.mkdir(parents=True)
        original = (b"<table><tr><th>Rank</th><th>Team</th><th>Record</th></tr>"
                    b"<tr><td>4</td><th>UConn</th><td>4-1</td></tr>"
                    b"<tr><td>3</td><th>Duke</th><td>4-2</td></tr></table>")
        edition.write_bytes(original)
        mark = directory / "assets/team-logos/72.png"
        mark.parent.mkdir(parents=True)
        Image.new("RGBA", (48, 48), "blue").save(mark)
        schedule = directory / "current.html"
        heading = ("Tipoff (Eastern)", "Away", "Home", "Court", "Reconstructed pick", "Final (away–home)")
        rows = (
            ("Nov 25, 2026 · 07:00 PM", "UConn", '<img class="school-mark" src="/cbb/assets/team-logos/72.png">Duke',
             "Neutral", "Duke by 0.34", "—"),
            ("Dec 04, 2026", "UConn", "Duke", "Home", "Pick’em", "97–95"),
        )
        schedule.write_text('<h1>Forecasts</h1><a href="editions/2026-11-24T193000Z.html">Edition</a>'
                            + '<table><tr>' + ''.join(f'<th>{v}</th>' for v in heading) + '</tr>'
                            + ''.join('<tr>' + ''.join(f'<td>{v}</td>' for v in row) + '</tr>' for row in rows)
                            + '</table>')
        cutoff = "2026-11-23T11:00:00+00:00"
        checkpoint = {"cutoff_utc": cutoff, "week": 4, "rows": [
            {"team_id": 1, "school": "Duke", "conference": "ACC"},
            {"team_id": 2, "school": "UConn", "conference": "ACC"},
        ]}
        base = {"home_team_id": 1, "away_team_id": 2, "neutral_site": True, "status": "final",
                "home_margin": 0, "home_points": 97, "away_points": 95, "conference_game": True,
                "cutoff_utc": "2026-11-16T11:00:00+00:00", "start_utc": "2026-11-17T18:00:00+00:00"}
        forecasts = [base, {**base, "result_cutoff_utc": "2026-11-30T11:00:00+00:00"},
                     {**base, "cutoff_utc": cutoff, "start_utc": "2026-11-26T00:00:00+00:00", "conference_game": False},
                     {**base, "cutoff_utc": cutoff, "start_utc": "2026-12-04T23:00:00+00:00", "neutral_site": False}]
        conference = share_conferences({"forecasts": forecasts}, checkpoint)
        card_urls = {}
        basketball_links(directory, conference, card_urls=card_urls)
        linked = schedule.read_bytes()
        basketball_links(directory, conference)
        self.assertEqual(schedule.read_bytes(), linked)
        links = BeautifulSoup(linked.decode(), "html.parser").select(".share-spread")
        self.assertEqual(len(links), 2)
        self.assertNotEqual(links[0]["href"], links[1]["href"])
        first, second = [BeautifulSoup((site / a["href"][1:]).read_text(), "html.parser") for a in links]
        self.assertEqual(first.h1.text, "UConn vs. Duke")
        self.assertIn("7:00 PM ET", first.select_one(".edition").text)
        self.assertEqual(first.select_one('meta[property="og:image"]')["content"],
                         "https://www.sportsrank.top/cbb/assets/team-logos/72.png")
        self.assertEqual(first.select_one('link[rel="apple-touch-icon"]')["href"],
                         first.select_one('meta[property="og:image"]')["content"])
        self.assertIn("#4 UConn 4-1 (0-1) vs. #3 Duke 4-2 (1-0)", first.select_one('meta[property="og:description"]')["content"])
        self.assertEqual([r.get_text(" ", strip=True) for r in first.select(".game-line tbody tr")],
                         ["#4 UConn 4-1 (0-1)", "#3 Duke 4-2 (1-0)"])
        self.assertEqual(first.main.get_text().count("0.34"), 1)
        for page, matchup, forecast in [(first, "UConn vs. Duke", "Duke by 0.34 · 51% to win"),
                                        (second, "UConn @ Duke", "Pick’em · 50% to win")]:
            with self.subTest(matchup=matchup):
                self.assertEqual(page.select_one(".pick strong").text, forecast)
                self.assertEqual(page.select_one('meta[property="og:title"]')["content"], f"{matchup} · {forecast}")
                self.assertIn(forecast, page.title.text)
        self.assertEqual(first.select_one(".game-details").text, "OOC (ACC vs ACC) · Neutral site")
        self.assertEqual(second.select_one(".game-details").text, "Conference (ACC) · @ Duke")
        self.assertNotIn("97–95", second.get_text())
        self.assertEqual(list(directory.rglob("*.png")), [mark])
        self.assertEqual(edition.read_bytes(), original)

        teams = [Standing("Duke", 3, Decimal(5), "4-2", "ACC"), Standing("UConn", 4, Decimal(4), "4-1", "ACC")]
        games = [
            Game(4, "Duke", "UConn", None, None, Decimal("-0.34"), True, start_utc="2026-11-26T00:00:00Z"),
            Game(4, "Duke", "UConn", None, None, Decimal(0), False, start_utc="2026-12-04T23:00:00Z"),
            Game(4, "Duke", "UConn", None, None, Decimal("-1"), True, start_utc="2026-11-27T00:00:00Z"),
        ]
        home = landing_page([Season(2027, {"W4": teams}, games)], {}, PagePresentation({}, {}), checkpoint,
                            edition, [], card_urls=card_urls)
        duke, uconn = BeautifulSoup(home, "html.parser").select(".home-rankings tbody tr")
        for row, sign in [(duke, "-"), (uconn, "+")]:
            scores = row.select("td:last-child .home-game-score")
            self.assertEqual([(a.text, a["href"]) for a in row.select(".home-game-score a")],
                             [(sign + "0.34", links[0]["href"]), ("Pick’em", links[1]["href"])])
            self.assertEqual(scores[-1].text, sign + "1")
            self.assertIsNone(scores[-1].a)
