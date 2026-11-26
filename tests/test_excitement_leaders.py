"""Verify the all-time ranks against the public one-decimal scores in every game guide."""
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lxml import html

from alltime_excitement import Game, Leaders, publish_pages
from cfb.game_excitement import GameAppeal


def document(path):
    return html.parse(path, parser=html.HTMLParser(encoding="utf-8"))


class AllTimeGameTests(unittest.TestCase):
    def test_scheduled_bev_ties_estimates_and_single_sport_refresh(self):
        root = Path(self.enterContext(TemporaryDirectory())) / "website"
        football, basketball = root / "cfb", root / "cbb"
        fb = Leaders()
        games = []
        for n in range(12):
            score = f"{100 - min(n, 9):.1f}"
            appeal = (GameAppeal(score, None) if n == 0 else
                      GameAppeal(score, (3, 0), float(score), "quarter"))
            games.append(Game.from_score("cfb", 2000, "Week 2", str(n), "Home", f"game-{n}", appeal))
        fb.add_season(games[:6])
        fb.add_season(games[6:])
        fb.save(football, "cfb")
        publish_pages(football, "cfb")
        self.assertEqual(fb.counts, {"BEV": 12, "AEV": 11})
        self.assertEqual(len(fb.games["BEV"]), 12)

        mbb = Leaders()
        mbb.add_season([Game.from_score("cbb", 2020, "2020-02-02", "A", "B", "game-8",
                                        GameAppeal("99.0", (62, 64), 100, "full"))])
        first = root / "fresh-site" / "cbb"
        mbb.save(first, "cbb")
        publish_pages(first, "cbb")
        first_page = document(first.parent / "excitement/aev.html")
        self.assertEqual(first_page.xpath("//tbody/tr[1]/td/strong/text()"), ["100.0"])
        mbb.save(basketball, "cbb")
        publish_pages(basketball, "cbb")
        aev = document(root / "excitement/aev.html")
        self.assertEqual(aev.xpath("//tbody/tr[1]/td/strong/text()"), ["100.0"])
        self.assertIn("99.0*", aev.xpath("//tbody/tr/td/strong/text()"))
        self.assertEqual(aev.xpath("//tbody/tr[1]/th/a/@href"), ["/cbb/seasons/2020/excitement/index.html#game-8"])
        self.assertEqual(publish_pages(football, "cfb"), 0)
        Leaders().save(basketball, "cbb")
        publish_pages(basketball, "cbb")
        self.assertEqual(document(root / "excitement/aev.html").xpath("//tbody/tr[1]/td/strong/text()"), ["99.0*"])
        self.assertIn("No qualifying games", document(basketball / "excitement/aev.html").getroot().text_content())

    def test_every_rank_and_link_agrees_with_the_complete_season_guides(self):
        site = Path(__file__).resolve().parents[1] / "website"
        metrics = ("BEV", "AEV")
        claimed = {}
        linked = set()
        for scope in ("cfb", "cbb", ""):
            for metric in metrics:
                rows = []
                for tr in document(site / scope / "excitement" / f"{metric.lower()}.html").xpath("//tbody/tr"):
                    cells = tr.xpath("./td|./th")
                    href = tr.xpath("./th/a/@href")[0]
                    linked.add(href)
                    rows.append((int(cells[0].text), href, cells[2].text_content(),
                                 cells[-2].text_content(), cells[1].text_content(), cells[-1].text_content()))
                self.assertGreaterEqual(len(rows), 10)
                self.assertEqual(len(rows), len({r[1] for r in rows}))
                claimed[scope, metric] = rows

        hist = {(s, m): Counter() for s in ("cfb", "cbb") for m in metrics}
        found = {}
        for sport, seasons in (("cfb", "years"), ("cbb", "seasons")):
            for path in (site / sport / seasons).glob("*/excitement/index.html"):
                prefix = "/" + path.relative_to(site).as_posix() + "#"
                for tr in document(path).xpath("//tbody/tr"):
                    cells = list(tr)
                    idx = 3 if sport == "cfb" else 4
                    bev, _, basis = cells[idx].text_content().strip().partition(" · ")
                    hist[sport, "BEV"][float(bev)] += 1
                    final, aev, evidence = "Pending", "", ""
                    if len(cells) == idx + 3:
                        final = cells[-2].text_content().strip()
                        aev, _, evidence = cells[-1].text_content().strip().partition(" · ")
                        hist[sport, "AEV"][float(aev.rstrip("*"))] += 1
                    anchor = tr.get("id")
                    href = prefix + anchor
                    if href in linked:
                        self.assertNotIn(href, found)
                        matchup = f"{cells[idx-2].text_content().strip()} vs. {cells[idx-1].text_content().strip()}"
                        found[href] = (matchup, final, bev, basis or "Pregame CORS", aev, evidence)

        self.assertEqual(set(found), linked)
        for (scope, metric), rows in claimed.items():
            population = hist[scope, metric] if scope else hist["cfb", metric] + hist["cbb", metric]
            expected = []
            for score, count in sorted(population.items(), reverse=True):
                rank = len(expected) + 1
                expected.extend([(rank, score)] * count)
                if len(expected) >= 10:
                    break
            self.assertEqual([(r[0], float(r[-2].rstrip("*"))) for r in rows], expected)
            for rank, href, matchup, final, value, basis in rows:
                if scope:
                    self.assertTrue(href.startswith(f"/{scope}/"))
                game, result, bev, bev_basis, aev, aev_basis = found[href]
                source_value, source_basis = (bev, bev_basis) if metric == "BEV" else (aev, aev_basis)
                self.assertEqual((matchup, final, value, basis), (game, result, source_value, source_basis))
