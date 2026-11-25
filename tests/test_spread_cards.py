"""Read the same forecast, share page and social metadata as a visitor."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bs4 import BeautifulSoup

from cfb.reference_markup import source_bytes
from spread_cards import basketball_links, football_links


class GameSharingTests(unittest.TestCase):
    def test_football_keeps_archived_bytes_and_positive_home_handicap_favors_away(self):
        directory = Path(self.enterContext(TemporaryDirectory())) / "cfb"
        path = directory / "years/2026/spread/2026_W0_FBS_spread.html"
        raw = (b'<html><body><h1>Week 0</h1><table><tr><th>week</th><th>home_team</th>'
               b'<th>away_team</th><th>neutral_site</th><th>spread</th></tr>'
               b'<tr><td>0</td><td>Home</td><td>Away &amp; Co.</td><td>False</td>'
               b'<td>Home +2.50</td></tr></table></body></html>')
        linked = football_links(raw, path, directory)
        self.assertEqual(source_bytes(linked), raw)
        self.assertEqual(football_links(linked, path, directory), linked)
        link = BeautifulSoup(linked, "html.parser").find("a")
        self.assertEqual(link.get_text(strip=True), "Home +2.50")
        page = BeautifulSoup((directory.parent / link["href"][1:]).read_text(), "html.parser")
        self.assertEqual(page.h1.text, "Away & Co. at Home")
        self.assertEqual(page.select_one(".pick strong").text, "Away & Co. by 2.50")
        self.assertEqual([td.text for td in page.select(".game-line td")], ["−2.50", "+2.50"])
        self.assertEqual(page.find("link", rel="canonical")["href"], "https://www.sportsrank.top" + link["href"])
        self.assertEqual(page.select_one("a.back")["href"], "/cfb/years/2026/spread/2026_W0_FBS_spread.html")

    def test_basketball_rematches_pickem_and_neutral_court(self):
        site = Path(self.enterContext(TemporaryDirectory()))
        directory = site / "cbb"
        edition = directory / "editions/2026-11-24T193000Z.html"
        edition.parent.mkdir(parents=True)
        edition.write_bytes(b"frozen edition")
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
        basketball_links(directory)
        linked = schedule.read_bytes()
        basketball_links(directory)
        self.assertEqual(schedule.read_bytes(), linked)
        links = BeautifulSoup(linked.decode(), "html.parser").select(".share-spread")
        self.assertEqual(len(links), 2)
        self.assertNotEqual(links[0]["href"], links[1]["href"])
        first, second = [BeautifulSoup((site / a["href"][1:]).read_text(), "html.parser") for a in links]
        self.assertEqual(first.h1.text, "UConn vs. Duke")
        self.assertIn("07:00 PM ET", first.select_one(".edition").text)
        self.assertEqual(first.select_one('meta[property="og:image"]')["content"],
                         "https://www.sportsrank.top/cbb/assets/team-logos/72.png")
        self.assertIn("Duke by 0.34", first.select_one('meta[property="og:title"]')["content"])
        self.assertEqual(second.select_one(".pick strong").text, "Pick’em")
        self.assertEqual([td.text for td in second.select(".game-line td")], ["0", "0"])
        self.assertNotIn("97–95", second.get_text())
        self.assertFalse(list(directory.rglob("*.png")))
        self.assertEqual(edition.read_bytes(), b"frozen edition")
