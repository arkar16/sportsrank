"""CLI for building the SportsRank SQLite release DB and running the local web server."""

from __future__ import annotations

import argparse
from pathlib import Path
import time

from .app import serve_local
from .export_db import export_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="Build sportsrank.sqlite3 from offline archives")
    exp.add_argument("--site", type=Path, default=Path("website"))
    exp.add_argument("--output", type=Path, default=Path(".sportsrank/web/sportsrank.sqlite3"))
    exp.add_argument("--cbb-db", type=Path, default=Path(".sportsrank/cbb/history.sqlite3"))
    exp.add_argument("--release-id", default=None)

    srv = sub.add_parser("serve", help="Run the local dynamic SSR server")
    srv.add_argument("--db", type=Path, default=Path(".sportsrank/web/sportsrank.sqlite3"))
    srv.add_argument("--assets", type=Path, default=Path("website"))
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8080)

    args = parser.parse_args()
    if args.command == "export":
        t0 = time.perf_counter()
        cbb_db = args.cbb_db if args.cbb_db and args.cbb_db.is_file() else None
        stats = export_database(args.site, args.output, cbb_db=cbb_db, release_id=args.release_id)
        elapsed = time.perf_counter() - t0
        size_mb = int(stats["bytes"]) / (1024 * 1024)
        print(
            f"Exported {args.output} ({size_mb:.2f} MB) in {elapsed:.2f}s: "
            f"CFB={stats.get('cfb_seasons', 0)} seasons / {stats.get('cfb_programs', 0)} programs / "
            f"{stats.get('cfb_rankings', 0)} rankings / {stats.get('cfb_games', 0)} games; "
            f"pages={stats.get('stored_pages', 0)}, blobs={stats.get('stored_blobs', 0)}"
        )
    elif args.command == "serve":
        serve_local(args.db, args.assets, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
