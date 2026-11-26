"""Acquire a bounded, resumable range of CollegeBasketballData game and team responses."""

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zlib

from cbb.api_history import HOST


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def cache_years(path, first, last, allowance):
    if allowance < 0 or not 1949 <= first <= last:
        raise ValueError("invalid season range or request allowance")
    token = os.environ.get("CFBD_API")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    http = build_opener(NoRedirect())
    used = 0
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS responses(url TEXT PRIMARY KEY, acquired_utc TEXT "
            "DEFAULT CURRENT_TIMESTAMP, rows INTEGER, compressed_json BLOB)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, url TEXT NOT NULL, "
            "requested_utc TEXT DEFAULT CURRENT_TIMESTAMP, rows INTEGER)"
        )

        def get(endpoint, year, **filters):
            nonlocal used
            url = f"https://{HOST}/{endpoint}?" + urlencode(dict(season=year, **filters))
            cached = db.execute("SELECT rows FROM responses WHERE url=?", (url,)).fetchone()
            if cached:
                return cached[0]
            if used >= allowance:
                raise RuntimeError(f"request allowance exhausted after {used} calls")
            if not token:
                raise RuntimeError("T3-injected CFBD_API is unavailable")
            with db:
                receipt = db.execute("INSERT INTO requests(url) VALUES(?)", (url,)).lastrowid
            used += 1
            request = Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
            with http.open(request, timeout=60) as response:
                data = response.read()
            rows = json.loads(data)
            if not isinstance(rows, list):
                raise ValueError(f"unexpected {endpoint} response")
            with db:
                db.execute(
                    "INSERT INTO responses(url,rows,compressed_json) VALUES(?,?,?)",
                    (url, len(rows), zlib.compress(data)),
                )
                db.execute("UPDATE requests SET rows=? WHERE id=?", (len(rows), receipt))
            return len(rows)

        def interval(year, start, end):
            filters = {
                "startDateRange": start.isoformat() + "T00:00:00Z",
                "endDateRange": end.isoformat() + "T00:00:00Z",
            }
            if get("games", year, **filters) < 3000:
                return
            if end - start <= timedelta(days=1):
                raise ValueError("daily game response still reaches the provider cap")
            middle = start + timedelta(days=(end - start).days // 2)
            interval(year, start, middle)
            interval(year, middle, end)

        for year in range(first, last + 1):
            if get("games", year) >= 3000:
                begin, end = date(year - 1, 7, 1), date(year, 7, 1)
                middle = date(year, 1, 1)
                interval(year, begin, middle)
                interval(year, middle, end)
            get("teams", year)
            print(f"Retained {year} games and teams ({used} new requests)", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--first-season", required=True, type=int)
    parser.add_argument("--last-season", required=True, type=int)
    parser.add_argument("--max-requests", required=True, type=int)
    options = parser.parse_args()
    cache_years(options.database, options.first_season, options.last_season, options.max_requests)
