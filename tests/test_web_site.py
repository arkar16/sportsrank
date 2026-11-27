"""Offline tests for the SQLite exporter and dynamic WSGI web application."""

from __future__ import annotations

import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from web.app import create_app
from web.export_db import export_database
from web.legacy import resolve_legacy_redirect, rewrite_html_links


def _wsgi_call(app, path: str, *, method: str = "GET", headers: dict[str, str] | None = None):
    captured = {}

    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8080",
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(),
        "wsgi.errors": io.StringIO(),
    }
    for k, v in (headers or {}).items():
        environ["HTTP_" + k.upper().replace("-", "_")] = v
    body = b"".join(app(environ, start_response))
    code = int(captured["status"].split()[0])
    return code, captured["headers"], body


def _write_table_html(path: Path, headers: list[str], rows: list[list[str]], updated: str = "2026-10-20 12:00:00"):
    path.parent.mkdir(parents=True, exist_ok=True)
    ths = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    path.write_text(
        f"<!doctype html><html><body><p>Last updated: {updated}</p>"
        f'<table class="dataframe"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table></body></html>',
        encoding="utf-8",
    )


def _build_fixture_site(root: Path) -> None:
    # Minimal 2025 completed season + 2026 active season + CBB edition + assets
    assets = root / "cfb/assets"
    (assets / "program-marks").mkdir(parents=True, exist_ok=True)
    (assets / "record-book.css").write_text("body { color: #222; }", encoding="utf-8")
    (assets / "report-pages.css").write_text(".sr-report { margin: 0; }", encoding="utf-8")

    # 2025 (completed with PRESEASON, W1, FINAL)
    y25 = root / "cfb/years/2025"
    _write_table_html(
        y25 / "rankings/2025_PRESEASON_FBS_cors.html",
        ["rank", "school", "cors", "record", "conference"],
        [["1", "Alabama", "24.00", "0-0", "SEC"], ["2", "Oregon", "22.50", "0-0", "Big Ten"]],
    )
    _write_table_html(
        y25 / "rankings/2025_W1_FBS_cors.html",
        ["rank", "school", "cors", "record", "conference"],
        [["1", "Oregon", "25.10", "1-0", "Big Ten"], ["2", "Alabama", "21.00", "0-1", "SEC"]],
    )
    _write_table_html(
        y25 / "rankings/2025_FINAL_FBS_cors.html",
        ["rank", "school", "cors", "record", "conference"],
        [["1", "Oregon", "26.40", "13-1", "Big Ten"], ["2", "Alabama", "22.10", "11-3", "SEC"]],
    )
    _write_table_html(
        y25 / "data/results/2025_FBS_results.html",
        ["week", "home_team", "away_team", "home_score", "away_score", "neutral_site"],
        [["1", "Alabama", "Oregon", "17", "24", "False"]],
    )
    perf25 = root / "cfb/performance/2025"
    perf25.mkdir(parents=True, exist_ok=True)
    (perf25 / "report.json").write_text(
        json.dumps({
            "season": 2025,
            "summary": {
                "game_count": 1, "straight_up_wins": 0, "straight_up_losses": 1, "straight_up_ties": 0,
                "straight_up_percentage": "0.0", "covers": 0, "no_covers": 1, "pushes": 0,
                "mae": "15.5", "rmse": "15.5", "market_games": 1, "market_wins": 0, "market_losses": 1,
                "market_pushes": 0, "market_no_picks": 0, "market_ats_percentage": "0.0",
                "paired_cors_mae": "15.5", "market_mae": "10.0", "missing_market_lines": 0,
            },
            "weekly": [{"week": 1, "game_count": 1, "straight_up_wins": 0, "straight_up_losses": 1,
                        "covers": 0, "no_covers": 1, "mae": "15.5", "rmse": "15.5",
                        "market_wins": 0, "market_losses": 1, "market_ats_percentage": "0.0"}],
            "games": [{"week": 1, "home": "Alabama", "away": "Oregon", "home_score": 17, "away_score": 24,
                       "home_handicap": "-8.5", "market_home_handicap": "-3.0",
                       "neutral_site": False, "straight_up": "incorrect", "market_ats": "Loss"}],
            "upcoming": [{"week": 2, "home": "Alabama", "away": "Oregon", "home_handicap": "-17.76",
                          "neutral_site": False, "provider": "Bovada", "market_home_handicap": "-16",
                          "market_pick": "Alabama", "status": "Pending result"}],
        }),
        encoding="utf-8",
    )
    (root / "cfb/performance/seasons.csv").write_text("season,game_count\n2025,1\n", encoding="utf-8")

    # 2026 (active with PRESEASON + W1)
    y26 = root / "cfb/years/2026"
    _write_table_html(
        y26 / "rankings/2026_PRESEASON_FBS_cors.html",
        ["rank", "school", "cors", "record", "conference"],
        [["1", "Oregon", "25.00", "0-0", "Big Ten"], ["2", "Alabama", "23.00", "0-0", "SEC"]],
    )
    _write_table_html(
        y26 / "rankings/2026_W1_FBS_cors.html",
        ["rank", "school", "cors", "record", "conference"],
        [["1", "Alabama", "25.50", "1-0", "SEC"], ["2", "Oregon", "24.20", "1-0", "Big Ten"]],
    )
    _write_table_html(
        y26 / "data/results/2026_FBS_results.html",
        ["week", "home_team", "away_team", "home_score", "away_score", "neutral_site"],
        [["1", "Alabama", "Oregon", "31", "20", "False"]],
    )
    _write_table_html(
        y26 / "data/slate/weekly_slate/2026_W2_FBS_slate.html",
        ["week", "home_team", "away_team", "neutral_site"],
        [["2", "Alabama", "Oregon", "False"]],
    )
    _write_table_html(
        y26 / "spread/2026_W2_FBS_spread.html",
        ["week", "home_team", "away_team", "neutral_site", "spread", "home_margin", "home_handicap"],
        [["2", "Alabama", "Oregon", "False", "Alabama -17.76", "17.76", "-17.76"]],
    )
    (root / "cfb/picks/2026/week-2").mkdir(parents=True, exist_ok=True)
    (root / "cfb/picks/2026/week-2/oregon-at-alabama.html").write_text(
        '<!doctype html><html><body><header class="masthead"></header><main><h1>Oregon @ Alabama</h1></main></body></html>',
        encoding="utf-8",
    )

    # CBB dated edition
    ed_dir = root / "cbb/editions"
    ed_dir.mkdir(parents=True, exist_ok=True)
    (ed_dir / "2026-11-19T185800Z.html").write_text(
        "<!doctype html><html><head><title>2026–27 men's college basketball rankings | SportsRank</title></head>"
        '<body><main><h1>Men’s college basketball</h1><p class="edition">2026–27 Division I · Nov 16, 2026</p>'
        "<section><h2>CORS rankings</h2><table><thead><tr><th>Rank</th><th>Team</th><th>Record</th><th>CORS rating</th></tr></thead>"
        "<tbody><tr><td>1</td><th>Texas</th><td>4–0</td><td>29.58</td></tr></tbody></table></section>"
        "<section><h2>Game forecasts</h2><p>Prepared Nov 19, 2026</p><table><thead><tr><th>Tipoff</th><th>Away</th><th>Home</th><th>Pick</th><th>Margin</th></tr></thead>"
        "<tbody><tr><td>Nov 19, 8:00 PM</td><td>Duke</td><td>Texas</td><td>Texas</td><td>3.20</td></tr></tbody></table></section>"
        "</main></body></html>",
        encoding="utf-8",
    )


    # Conference, excitement, pick card, and CBB assets + tournament fixtures
    (root / "cbb/assets/team-logos").mkdir(parents=True, exist_ok=True)
    (root / "cbb/assets/team-logos/72.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "cfb/conferences/sec").mkdir(parents=True, exist_ok=True)
    (root / "cfb/conferences/sec/2025.html").write_text(
        '<!doctype html><html><head><title>2025 SEC</title></head><body>'
        '<header class="masthead"><a href="/cfb/cfb.html">Old</a></header>'
        '<main><h1>2025 SEC</h1><a href="/cfb/teams/alabama/2025.html">Alabama</a>'
        '<a href="/cfb/conferences/matchups/2025/acc--sec.html">ACC vs SEC</a></main></body></html>',
        encoding="utf-8",
    )
    (root / "cfb/conferences/matchups/2025").mkdir(parents=True, exist_ok=True)
    (root / "cfb/conferences/matchups/2025/acc--sec.html").write_text(
        '<!doctype html><html><body><header class="masthead"></header><main><h1>ACC vs SEC</h1></main></body></html>',
        encoding="utf-8",
    )
    (root / "excitement").mkdir(parents=True, exist_ok=True)
    (root / "excitement/grades.html").write_text(
        '<!doctype html><html><body><header class="masthead"></header>'
        '<main><h1>Excitement letter grades</h1><a href="/excitement/bev.html">BEV</a></main></body></html>',
        encoding="utf-8",
    )
    (root / "cfb/picks/2026/week-13").mkdir(parents=True, exist_ok=True)
    (root / "cfb/picks/2026/week-13/auburn-at-alabama.html").write_text(
        '<!doctype html><html><head>'
        '<link rel="canonical" href="https://www.sportsrank.top/cfb/picks/2026/week-13/auburn-at-alabama.html">'
        '<meta property="og:url" content="https://www.sportsrank.top/cfb/picks/2026/week-13/auburn-at-alabama.html">'
        '</head><body><header class="masthead"></header><main><h1>Auburn @ Alabama</h1>'
        '<a href="/cfb/years/2026/spread/2026_W13_FBS_spread.html">Spreads</a></main></body></html>',
        encoding="utf-8",
    )
    (root / "cbb/tournaments").mkdir(parents=True, exist_ok=True)
    (root / "cbb/tournaments/2025.html").write_text(
        '<!doctype html><html><body><header class="masthead"></header>'
        '<main><h1>2025 March Madness</h1><a href="/cbb/seasons/2025/index.html">Season</a></main></body></html>',
        encoding="utf-8",
    )


class WebSiteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.site_dir = self.root / "website"
        self.db_path = self.root / "sportsrank.sqlite3"
        _build_fixture_site(self.site_dir)
        self.stats = export_database(self.site_dir, self.db_path, release_id="test-rel-1")
        self.app = create_app(self.db_path, self.site_dir)

    def tearDown(self):
        self.app.close()
        self._tmp.cleanup()

    def test_export_counts(self):
        self.assertEqual(self.stats["cfb_seasons"], 2)
        self.assertEqual(self.stats["cfb_programs"], 2)
        self.assertEqual(self.stats["cfb_performance_seasons"], 1)
        self.assertEqual(self.stats["cbb_editions"], 1)

    def test_canonical_pages_render_clean_html_without_scripts_or_html_links(self):
        routes = [
            "/",
            "/cfb",
            "/cfb/teams",
            "/cfb/teams/alabama",
            "/cfb/teams/alabama/2025",
            "/cfb/2025",
            "/cfb/2025/final",
            "/cfb/2025/w1",
            "/cfb/2025/w1/results",
            "/cfb/2025/w1/schedule",
            "/cfb/2025/w1/spreads",
            "/cfb/2025/results",
            "/cfb/2026",
            "/cfb/history/champions",
            "/cfb/history/last-place",
            "/cfb/performance",
            "/cfb/performance/2025",
            "/cfb/conferences/sec/2025",
            "/cfb/conferences/matchups/2025/acc--sec",
            "/cfb/picks/2026/week-13/auburn-at-alabama",
            "/excitement/grades",
            "/cbb",
            "/cbb/teams",
            "/cbb/teams/texas",
            "/cbb/tournaments/2025",
            "/cbb/editions/2026-11-19T185800Z",
        ]
        for route in routes:
            code, headers, body = _wsgi_call(self.app, route)
            self.assertEqual(code, 200, f"Expected 200 for {route}, got {code}")
            html = body.decode("utf-8")
            self.assertIn("<!doctype html>", html.lower())
            self.assertNotIn("<script", html.lower(), f"Unexpected <script> on {route}")
            # No internal .html links should appear anywhere on dynamic pages
            html_hrefs = re.findall(r'href="([^"]+\.html)"', html)
            self.assertEqual(html_hrefs, [], f"Found legacy .html links on {route}: {html_hrefs}")
            self.assertIn("ETag", headers)

    def test_upsets_and_honors_data_accuracy(self):
        # 2025 Alabama (-8.5) lost to Oregon 17-24 -> qualifying 7+ point upset
        code, _, body = _wsgi_call(self.app, "/cfb/2025/final")
        self.assertEqual(code, 200)
        text = body.decode("utf-8")
        self.assertIn("Biggest CORS upsets", text)
        self.assertIn("8.50", text)
        self.assertIn("24–17", text)

        # Champions should list Oregon for 2025; last place should list Alabama
        code, _, body = _wsgi_call(self.app, "/cfb/history/champions")
        self.assertEqual(code, 200)
        self.assertIn("Oregon", body.decode("utf-8"))

        code, _, body = _wsgi_call(self.app, "/cfb/history/last-place")
        self.assertEqual(code, 200)
        self.assertIn("Alabama", body.decode("utf-8"))

    def test_team_schedule_win_chance_projected_record_and_links(self):
        # Active 2026 Alabama (1-0) with upcoming Week 2 home game favored by 17.76 -> 89% win chance, 1.9-0.1 projected
        code, _, body = _wsgi_call(self.app, "/cfb/teams/alabama/2026")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn("Win chance", html)
        self.assertIn("89%", html)
        self.assertIn('id="remaining-schedule"', html)
        self.assertIn('id="projected-season-record">Projected season record <strong>1.9–0.1</strong>', html)

        # Opponent Oregon (1-0) has 11% win chance -> 1.1-0.9 projected
        code, _, body = _wsgi_call(self.app, "/cfb/teams/oregon/2026")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn("11%", html)
        self.assertIn('id="projected-season-record">Projected season record <strong>1.1–0.9</strong>', html)

        # Program history links best rank to checkpoint and conference to clean conference page
        code, _, body = _wsgi_call(self.app, "/cfb/teams/alabama")
        self.assertEqual(code, 200)
        hist = body.decode("utf-8")
        self.assertIn('href="/cfb/2025/preseason"', hist)
        self.assertIn('href="/cfb/conferences/sec/2025"', hist)

        # Weekly spreads include win chance and shareable pick card link
        code, _, body = _wsgi_call(self.app, "/cfb/2026/w2/spreads")
        self.assertEqual(code, 200)
        sp = body.decode("utf-8")
        self.assertIn("Win chance", sp)
        self.assertIn("89%", sp)
        self.assertIn('href="/cfb/picks/2026/week-2/oregon-at-alabama"', sp)

        # Performance page shows ungraded forecasts with actual provider name
        code, _, body = _wsgi_call(self.app, "/cfb/performance/2025")
        self.assertEqual(code, 200)
        perf = body.decode("utf-8")
        self.assertIn("Ungraded forecasts", perf)
        self.assertIn("Bovada", perf)

    def test_legacy_redirects_resolve_to_live_canonical_pages(self):
        legacy_paths = [
            "/index.html",
            "/cfb/cfb.html",
            "/cfb/teams/index.html",
            "/cfb/teams/alabama/index.html",
            "/cfb/teams/alabama/2025.html",
            "/cfb/years/2025/2025_CFB.html",
            "/cfb/years/2025/checkpoints/FINAL.html",
            "/cfb/years/2025/checkpoints/W1.html",
            "/cfb/years/2025/rankings/2025_W1_FBS_cors.html",
            "/cfb/years/2025/rankings/2025_W0_FBS_cors.html",
            "/cfb/years/2025/data/results/weekly_results/2025_W1_FBS_results.html",
            "/cfb/years/2025/data/results/2025_FBS_results.html",
            "/cfb/years/2025/data/slate/weekly_slate/2025_W1_FBS_slate.html",
            "/cfb/years/2025/data/records/2025_W1_FBS_records.html",
            "/cfb/years/2025/spread/2025_W1_FBS_spread.html",
            "/cfb/years/2025/spread/2025_W1_FBS_spread_results.html",
            "/cfb/years/2025/reconstructed/2025_W1_FBS_spread.html",
            "/cfb/years/2025/history/reconstruction.html",
            "/cfb/performance/index.html",
            "/cfb/performance/2025/index.html",
            "/cfb/history/nc_FBS_CFB_output.html",
            "/cfb/years/history/wt_FBS_CFB_output.html",
            "/cbb/index.html",
            "/cbb/editions/2026-11-19T185800Z.html",
        ]
        for legacy in legacy_paths:
            code, headers, _ = _wsgi_call(self.app, legacy)
            self.assertEqual(code, 301, f"Expected 301 for {legacy}, got {code}")
            dest = headers["Location"]
            self.assertFalse(dest.endswith(".html"), f"Redirect target still ends in .html: {dest}")
            # Follow up to 2 hops (e.g. legacy W0 -> /cfb/2025/w0 -> /cfb/2025/preseason)
            for _ in range(2):
                dcode, dheaders, _ = _wsgi_call(self.app, dest)
                if dcode == 301:
                    dest = dheaders["Location"]
                else:
                    break
            self.assertEqual(dcode, 200, f"Redirect chain from {legacy} ended at {dest} with {dcode}")

    def test_assets_etag_head_downloads_and_security(self):
        # Asset serving + caching
        code, headers, body = _wsgi_call(self.app, "/cfb/assets/record-book.css")
        self.assertEqual(code, 200)
        self.assertIn("text/css", headers["Content-Type"])
        self.assertIn(b"color: #222", body)

        # If-None-Match -> 304
        code304, _, body304 = _wsgi_call(self.app, "/cfb/assets/record-book.css", headers={"If-None-Match": headers["ETag"]})
        self.assertEqual(code304, 304)
        self.assertEqual(body304, b"")

        # Path traversal blocked
        code, _, _ = _wsgi_call(self.app, "/cfb/assets/../../sportsrank.sqlite3")
        self.assertEqual(code, 404)

        # HEAD returns headers with empty body
        code, headers, body = _wsgi_call(self.app, "/cfb/2025", method="HEAD")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

        # POST rejected
        code, _, _ = _wsgi_call(self.app, "/", method="POST")
        self.assertEqual(code, 405)

        # Performance downloads & health endpoints
        for path, ctype in (
            ("/cfb/performance/seasons.csv", "text/csv"),
            ("/cfb/performance/2025/report.json", "application/json"),
            ("/cfb/performance/2025/games.csv", "text/csv"),
            ("/cfb/performance/2025/weekly.csv", "text/csv"),
            ("/health", "application/json"),
            ("/healthz", "application/json"),
        ):
            code, headers, body = _wsgi_call(self.app, path)
            self.assertEqual(code, 200, path)
            self.assertIn(ctype, headers["Content-Type"])
            self.assertGreater(len(body), 0)
            if path in ("/health", "/healthz"):
                self.assertEqual(json.loads(body), {"status": "ok", "release_id": "test-rel-1"})

        # Trailing post-season week in a completed season falls back to FINAL; bogus week 99 stays 404
        code, headers, _ = _wsgi_call(self.app, "/cfb/2025/w16")
        self.assertEqual(code, 301)
        self.assertEqual(headers["Location"], "/cfb/2025/final")
        code, _, _ = _wsgi_call(self.app, "/cfb/2025/w99")
        self.assertEqual(code, 404)

        # 404 page
        code, _, body = _wsgi_call(self.app, "/cfb/does-not-exist")
        self.assertEqual(code, 404)
        self.assertIn(b"Page not found", body)

    def test_legacy_resolver_edge_cases(self):
        self.assertIsNone(resolve_legacy_redirect("/cfb/2026"))
        self.assertEqual(resolve_legacy_redirect("/cfb/years/1897/1897_CFB.html"), "/cfb/1897")
        self.assertEqual(
            resolve_legacy_redirect("/cfb/years/2026/spread/2026_FBS_forecast_results.html"),
            "/cfb/performance/2026",
        )
        self.assertEqual(
            resolve_legacy_redirect("/cbb/editions/2026-11-27T034800Z-cbbd.html"),
            "/cbb/editions/2026-11-27T034800Z-cbbd",
        )
        self.assertEqual(
            resolve_legacy_redirect("/cbb/conferences/matchups/1994/horizon--sun-belt.html"),
            "/cbb/conferences/matchups/1994/horizon--sun-belt",
        )
        rewritten = rewrite_html_links(
            '<a href="../2026_CFB.html">Season</a>'
            '<a href="/cbb/current.html#forecasts">Forecasts</a>'
            '<meta property="og:url" content="https://www.sportsrank.top/cfb/picks/2026/week-13/auburn-at-alabama.html">',
            "/cfb/years/2026/excitement/index.html",
        )
        self.assertIn('href="/cfb/2026"', rewritten)
        self.assertIn('href="/cbb/current#forecasts"', rewritten)
        self.assertIn('content="https://www.sportsrank.top/cfb/picks/2026/week-13/auburn-at-alabama"', rewritten)
        # CBB asset serving
        code, headers, _ = _wsgi_call(self.app, "/cbb/assets/team-logos/72.png")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "image/png")


if __name__ == "__main__":
    unittest.main()
