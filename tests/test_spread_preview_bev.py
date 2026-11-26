"""Read the BEV visible to a reader, a link crawler and a PNG recipient."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PIL import Image, ImageChops

from spread_cards import Spread, Team, _document
from spread_preview import refresh_retained_previews


class SpreadPreviewTests(unittest.TestCase):
    def test_retained_pick_uses_frozen_score_and_restores_missing_image(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "cfb"
            mark = root / "assets/mark.png"
            mark.parent.mkdir(parents=True)
            Image.new("RGB", (48, 48), "tomato").save(mark)
            previews = []
            for day, bev in (("old", "0.0"), ("new", "88.2")):
                game = Spread("cfb", day, day, "/edition.html",
                              Team("Home", "/cfb/assets/mark.png", "#4", "1-1"), Team("Away", "", "", "0-2"),
                              False, Decimal("2.00"), "OOC", bev, "77.0 · Play-by-play")
                target = Path(temp) / game.path[1:]
                target.parent.mkdir(parents=True)
                # An issued card with a BEV in the HTML and the original school-only icon.
                old = _document(replace(game, bev=None), ("/cfb/assets/mark.png", 48))
                score = f'<p class="game-excitement">Pregame appeal (BEV) <strong>{bev}</strong></p>'
                raw = old.replace('<div class="share-actions">', score + '<div class="share-actions">')
                target.write_text(raw)
                refresh_retained_previews(root, "cfb")
                upgraded = target.read_text()
                self.assertEqual(raw.split("</head>")[1], upgraded.split("</head>")[1])
                doc = BeautifulSoup(upgraded, "html.parser")
                self.assertEqual(doc.select_one('meta[name="sportsrank:bev"]')["content"], bev)
                title = doc.select_one('meta[property="og:title"]')["content"]
                self.assertTrue(title.endswith(" · BEV " + bev))
                self.assertEqual(doc.select_one('meta[name="twitter:title"]')["content"], title)
                image = Path(temp) / urlsplit(doc.select_one('meta[property="og:image"]')["content"]).path[1:]
                previews.append(image)
                data = image.read_bytes()
                image.unlink()
                refresh_retained_previews(root, "cfb")
                self.assertEqual(image.read_bytes(), data)
                self.assertEqual(target.read_text(), upgraded)
            with Image.open(previews[0]) as a, Image.open(previews[1]) as b:
                self.assertEqual(a.size, (120, 120))
                diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
                self.assertIsNone(diff.crop((0, 0, 120, 80)).getbbox())
                self.assertIsNotNone(diff.crop((0, 80, 120, 120)).getbbox())

    def test_all_published_scored_picks_share_their_visible_bev(self):
        site = Path(__file__).resolve().parents[1] / "website"
        for sport in ("cfb", "cbb"):
            seen = 0
            for path in (site / sport / "picks").rglob("*.html"):
                page = BeautifulSoup(path.read_bytes(), "html.parser", from_encoding="utf-8")
                stored = page.select_one(".game-excitement strong")
                if stored is None:
                    self.assertIsNone(page.select_one('meta[name="sportsrank:bev"]'))
                    continue
                score = stored.get_text().partition("·")[0].strip()
                self.assertEqual(page.select_one('meta[name="sportsrank:bev"]')["content"], score, path)
                title = page.select_one('meta[property="og:title"]')["content"]
                self.assertTrue(title.endswith(" · BEV " + score), path)
                self.assertEqual(page.select_one('meta[name="twitter:title"]')["content"], title)
                icon = page.select_one('meta[property="og:image"]')["content"]
                self.assertEqual(page.select_one('meta[name="twitter:image"]')["content"], icon)
                self.assertEqual(page.select_one('link[rel="apple-touch-icon"]')["href"], icon)
                with Image.open(site / urlsplit(icon).path[1:]) as image:
                    self.assertEqual(image.size, (120, 120))
                seen += 1
            self.assertGreater(seen, 0, sport)
