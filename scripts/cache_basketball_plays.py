"""Fetch and retain dated CollegeBasketballData play responses for offline scoring."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener
import zlib

from cbb.api_history import HOST
from cbb.play_requests import cover_urls
from scripts.cache_basketball_history import NoRedirect

# Keep only the public provider's fields needed to verify the game, clock and score.
FIELDS = (
    "id",
    "gameId",
    "period",
    "clock",
    "secondsRemaining",
    "homeScore",
    "awayScore",
    "teamId",
    "isHomeTeam",
    "scoringPlay",
    "scoreValue",
    "gameStartDate",
)


def wanted_urls(database, seasons, *, cover=False):
    by_game = {}
    with closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        for url, blob in db.execute(
            "SELECT url,compressed_json FROM responses ORDER BY acquired_utc,url"
        ):
            if not url.startswith(f"https://{HOST}/games?"):
                continue
            for game in json.loads(zlib.decompress(blob)):
                if game["season"] in seasons:
                    by_game[game["id"]] = game
    games = [
        g
        for g in by_game.values()
        if g["status"] == "final" and g.get("excitement") is not None
    ]
    if cover:
        return cover_urls(games)
    dates = defaultdict(set)
    for g in by_game.values():
        if g["status"] == "final" and g.get("excitement") is not None:
            dates[g["season"]].add(g["startDate"][:10])
    # Round-robin within seasons so a paused run includes a chronological spread for each.
    shuffled = {
        y: sorted(days, key=lambda d: sha256(d.encode()).digest())
        for y, days in dates.items()
    }
    return [
        f"https://{HOST}/plays/date?date={shuffled[y][i]}&shootingPlaysOnly=true"
        for i in range(max(map(len, shuffled.values()), default=0))
        for y in seasons
        if y in shuffled and len(shuffled[y]) > i
    ]


def download(path, history, seasons, limit, workers, cover=False, reserve=0):
    if min(limit, reserve) < 0 or workers not in range(1, 5):
        raise ValueError("invalid acquisition limit or concurrency")
    token = os.environ.get("CFBD_API")
    if not token:
        raise RuntimeError("T3-injected CFBD_API is unavailable")
    with closing(sqlite3.connect(path, timeout=30)) as db:
        # An offline scoring build can inspect a consistent snapshot during acquisition.
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS responses(url TEXT PRIMARY KEY,acquired_utc TEXT DEFAULT CURRENT_TIMESTAMP,"
            "rows INTEGER,compressed_json BLOB)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,url TEXT,requested_utc TEXT DEFAULT "
            "CURRENT_TIMESTAMP,rows INTEGER)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS hashes(url TEXT PRIMARY KEY,sha256 TEXT,remaining INTEGER)"
        )
        saved = {u for (u,) in db.execute("SELECT url FROM responses")}
        planned = wanted_urls(history, seasons, cover=cover)
        urls = [
            u
            for u in planned
            if u not in saved and u.removesuffix("&shootingPlaysOnly=true") not in saved
        ]

        def get(url):
            req = Request(
                url,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                },
            )
            try:
                response = build_opener(NoRedirect()).open(req, timeout=100)
            except HTTPError as err:
                if err.code == 429:
                    retry = err.headers.get("Retry-After")
                    print(
                        "Provider rate limit; Retry-After",
                        retry or "unspecified",
                        "remaining",
                        err.headers.get("X-CallLimit-Remaining", "unknown"),
                        flush=True,
                    )
                raise
            with response:
                data = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    data = gzip.decompress(data)
                remaining = response.headers.get("X-CallLimit-Remaining")
            plays = json.loads(data)
            if not isinstance(plays, list):
                raise ValueError("unexpected play response")
            compact = [[play[k] for k in FIELDS] for play in plays]
            encoded = json.dumps(compact, separators=(",", ":")).encode()
            return (
                len(plays),
                zlib.compress(encoded, 3),
                sha256(data).hexdigest(),
                remaining,
            )

        print(
            f"{len(planned)} selections, {len(urls)} uncached, up to {limit} new requests",
            flush=True,
        )
        remaining = db.execute(
            "SELECT MIN(remaining) FROM hashes JOIN responses USING(url) "
            "WHERE strftime('%Y-%m',acquired_utc)=strftime('%Y-%m','now')"
        ).fetchone()[0]
        attempted = 0
        failed = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for i in range(0, min(len(urls), limit), workers):
                if i:
                    time.sleep(0.3 if cover else 1.2)
                capacity = (
                    min(workers, max(0, remaining - reserve))
                    if remaining is not None
                    else workers
                )
                if capacity == 0:
                    print("Shared quota reserve reached", reserve, flush=True)
                    break
                batch = {}
                readings = []
                for url in urls[i : min(i + capacity, limit)]:
                    with db:
                        receipt = db.execute(
                            "INSERT INTO requests(url) VALUES(?)", (url,)
                        ).lastrowid
                    attempted += 1
                    batch[executor.submit(get, url)] = url, receipt
                stop = False
                for response in as_completed(batch):
                    url, receipt = batch[response]
                    try:
                        n, data, digest, header_remaining = response.result()
                    except (HTTPError, OSError) as err:
                        status = getattr(err, "code", None)
                        print(
                            "Request failed",
                            url,
                            status or type(err).__name__,
                            flush=True,
                        )
                        failed.append(url)
                        stop |= status in (401, 403, 429) or len(failed) >= 5
                        continue
                    with db:
                        db.execute(
                            "INSERT INTO responses(url,rows,compressed_json) VALUES(?,?,?)",
                            (url, n, data),
                        )
                        db.execute(
                            "INSERT OR REPLACE INTO hashes VALUES(?,?,?)",
                            (url, digest, header_remaining),
                        )
                        db.execute(
                            "UPDATE requests SET rows=? WHERE id=?", (n, receipt)
                        )
                    if header_remaining is not None:
                        readings.append(int(header_remaining))
                    elif reserve:
                        raise ValueError("provider omitted the shared quota balance")
                if readings:
                    remaining = min(readings)
                    if remaining <= reserve:
                        print("Shared quota reserve reached", remaining, flush=True)
                        stop = True
                if attempted % 12 < workers or stop:
                    print(f"{attempted} calls, {len(failed)} unsuccessful", flush=True)
                if stop:
                    break
        print(
            f"Finished {attempted} calls, {len(failed)} failed; saved responses are resumable",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--max-requests", required=True, type=int)
    parser.add_argument(
        "--cover",
        action="store_true",
        help="Cover historical games across dates, teams and tournaments",
    )
    parser.add_argument("--workers", default=2, type=int)
    parser.add_argument(
        "--reserve",
        default=0,
        type=int,
        help="Keep this many shared provider requests available",
    )
    parser.add_argument("seasons", type=int, nargs="+")
    args = parser.parse_args()
    download(
        args.output,
        args.history,
        args.seasons,
        args.max_requests,
        args.workers,
        args.cover,
        args.reserve,
    )
