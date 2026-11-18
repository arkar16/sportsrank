"""Verify source fidelity and navigation for the legacy record sheets."""

import unittest

from bs4 import BeautifulSoup

from cfb.history_sheets import render_history_sheet


CHAMPIONS = "CORS_NC_FBS_CFB.html"
LAST_PLACE = "CORS_WT_FBS_CFB.html"

# Values transcribed from the published pandas fragments, including both 1973
# entries. The fixture places 1897 last to check preservation of input order.
CHAMPION_ROWS = (
    ("1919", "Texas A&M", "Southwest", "10-0-0", "1.0", "23.88", "27.5", "3.0", "10.0", "0.0"),
    ("1973", "Oklahoma", "Big 8", "10-0-1", "0.91", "25.96", "24.27", "14.0", "10.25", "-0.25"),
    ("1973", "Oklahoma", "Big 8", "10-0-1", "0.91", "25.96", "24.27", "14.0", "10.25", "-0.25"),
    ("1897", "Pennsylvania", "FBS Independents", "15-0-0", "1.0", "24.96", "29.53", "8.0", "14.99", "0.01"),
)
LAST_PLACE_ROWS = (
    ("28", "1897", "Williams", "FBS Independents", "1-7-1", "0.11", "0.29", "-20.56", "9.0", "0.01", "0.99"),
    ("133", "2023", "Kent State", "Mid-American", "1-11", "0.08", "-0.57", "-19.92", "9.0", "1.39", "-0.39"),
)


def legacy_fragment(rows, *, has_index=False):
    tables = []
    for row in rows:
        index_cell = f"<th>{row[0]}</th>" if has_index else ""
        values = "".join(f"<td>{value}</td>" for value in row[int(has_index):])
        tables.append(f'<table border="1" class="dataframe"><tbody><tr>{index_cell}{values}</tr></tbody></table>')
    return "".join(tables).encode("utf-8")


class HistorySheetsTests(unittest.TestCase):
    def test_preserves_archived_values_order_repeats_and_the_optional_index(self):
        for filename, values, has_index in (
            (CHAMPIONS, CHAMPION_ROWS, False),
            (LAST_PLACE, LAST_PLACE_ROWS, True),
        ):
            with self.subTest(filename=filename):
                original = legacy_fragment(values, has_index=has_index)
                page = BeautifulSoup(render_history_sheet(filename, original, {}, set()), "html5lib")
                table, = page.find_all("table")
                head = table.select("thead th")
                expected_headings = ["Row index"] if has_index else []
                expected_headings += [
                    "Season", "School", "Conference", "Record", "Win fraction", "CORS rating",
                    "Avg. scoring margin", "Schedule strength", "Expected wins", "Wins vs. expected",
                ]
                self.assertEqual([cell.text for cell in head], expected_headings)
                self.assertEqual([cell.get("scope") for cell in head], ["col"] * len(head))
                body = table.select("tbody tr")
                self.assertEqual(tuple(tuple(cell.text for cell in row.find_all(["th", "td"])) for row in body), values)
                self.assertEqual([cell.text for cell in table.select('tbody th[scope="row"]')],
                                 [row[int(has_index)] for row in values])

    def test_links_existing_programs_and_seasons_and_labels_the_scroll_region(self):
        program = "/cfb/teams/texas-am/index.html"
        original = legacy_fragment(CHAMPION_ROWS)
        page = BeautifulSoup(render_history_sheet(CHAMPIONS, original, {"Texas A&M": program}, {1919}), "html5lib")

        self.assertEqual(page.html["lang"], "en")
        self.assertEqual(page.link["href"], "/cfb/assets/record-book.css")
        self.assertIsNotNone(page.select_one('meta[name="viewport"]'))
        heading, = page.find_all("h1")
        self.assertEqual(heading.text, "CORS national champions by season")
        self.assertIn(heading.text, page.title.text)
        region = page.select_one('.table-scroll[role="region"][tabindex="0"][aria-label]')
        self.assertEqual(region.table["aria-labelledby"], heading["id"])
        self.assertEqual([(link.text, link["href"]) for link in region.table.select("tbody a")], [
            ("1919", "/cfb/years/1919/1919_CFB.html"), ("Texas A&M", program),
        ])
        self.assertIsNone(page.select_one(".masthead, script"))
        self.assertEqual(page.nav.a["href"], "/cfb/years/history/nc_FBS_CFB_output.html")

        original = legacy_fragment(LAST_PLACE_ROWS, has_index=True)
        page = BeautifulSoup(render_history_sheet(LAST_PLACE, original, {}, set()), "html5lib")
        self.assertEqual(page.h1.text, "CORS last-place finishers by season")
        self.assertEqual(page.nav.a["href"], "/cfb/years/history/wt_FBS_CFB_output.html")

    def test_rejects_an_unexpected_source_column_layout(self):
        with self.assertRaisesRegex(ValueError, "unexpected legacy table shape"):
            render_history_sheet(LAST_PLACE, legacy_fragment(CHAMPION_ROWS), {}, set())
