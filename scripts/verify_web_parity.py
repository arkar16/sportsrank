"""Verify full legacy URL coverage, data parity, and latency for the dynamic web app."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import time

from web.app import create_app


def _call(app, path: str) -> tuple[int, dict[str, str], bytes]:
    captured = {}

    def start_response(status, headers):
        captured["status"] = int(status.split()[0])
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8080",
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(),
        "wsgi.errors": io.StringIO(),
    }
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def verify(site_dir: Path, db_path: Path, assets_dir: Path) -> int:
    app = create_app(db_path, assets_dir)
    try:
        # 1. Sweep every single .html file in website/
        html_paths = sorted(
            "/" + p.relative_to(site_dir).as_posix()
            for p in site_dir.rglob("*.html")
            if p.name != "404.html"
        )
        t0 = time.perf_counter()
        failures: list[tuple[str, str]] = []
        unique_destinations: set[str] = set()
        for legacy in html_paths:
            code, headers, _ = _call(app, legacy)
            if code != 301:
                failures.append((legacy, f"expected 301, got {code}"))
                continue
            dest = headers.get("Location", "")
            if not dest or dest.endswith(".html"):
                failures.append((legacy, f"bad redirect target {dest!r}"))
                continue
            unique_destinations.add(dest)

        # Follow every unique canonical destination once
        dest_failures: list[tuple[str, str]] = []
        for dest in sorted(unique_destinations):
            cur = dest
            for _ in range(2):
                code, headers, _ = _call(app, cur)
                if code == 301:
                    cur = headers.get("Location", "")
                else:
                    break
            if code != 200:
                dest_failures.append((dest, f"final status {code} at {cur}"))

        sweep_s = time.perf_counter() - t0

        # 2. Benchmark representative hot pages
        hot_routes = [
            "/",
            "/cfb",
            "/cfb/2026",
            "/cfb/2025/final",
            "/cfb/teams",
            "/cfb/teams/alabama",
            "/cfb/teams/alabama/2025",
            "/cfb/conferences/sec/2026",
            "/cfb/conferences/matchups/2026/acc--sec",
            "/cfb/performance",
            "/cfb/performance/2025",
            "/excitement/grades",
            "/cbb",
            "/cbb/seasons/2026",
            "/cbb/tournaments/2025",
            "/cbb/teams",
        ]
        timings = []
        html_link_leaks = []
        for route in hot_routes:
            rt0 = time.perf_counter()
            code, _, body = _call(app, route)
            ms = (time.perf_counter() - rt0) * 1000
            timings.append((route, code, len(body), ms))
            if code == 200 and b'.html"' in body:
                html_link_leaks.append(route)

        print(f"Legacy .html sweep: {len(html_paths)} files -> {len(unique_destinations)} unique canonical routes in {sweep_s:.2f}s")
        print(f"Redirect failures: {len(failures)} | Destination failures: {len(dest_failures)} | .html link leaks: {len(html_link_leaks)}")
        for path, reason in (failures + dest_failures)[:20]:
            print(f"  FAIL: {path} -> {reason}")
        if html_link_leaks:
            print("  LEAKED .html links on:", html_link_leaks)
        print("\nRepresentative page render timings:")
        for route, code, nbytes, ms in timings:
            print(f"  {code}  {ms:6.2f} ms  {nbytes/1024:6.1f} KB  {route}")

        return 0 if not failures and not dest_failures and not html_link_leaks else 1
    finally:
        app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path("website"))
    parser.add_argument("--db", type=Path, default=Path(".sportsrank/web/sportsrank.sqlite3"))
    parser.add_argument("--assets", type=Path, default=Path("website"))
    args = parser.parse_args()
    raise SystemExit(verify(args.site, args.db, args.assets))


if __name__ == "__main__":
    main()
