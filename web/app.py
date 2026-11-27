"""Stateless WSGI application serving dynamic HTML from read-only SQLite."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
from socketserver import ThreadingMixIn
import sqlite3
import threading
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
import zlib

from .legacy import resolve_legacy_redirect
from .render import (
    render_edition,
    render_games_view,
    render_home,
    render_honors,
    render_masthead,
    render_not_found,
    render_performance_index,
    render_performance_season,
    render_program_history,
    render_program_season,
    render_season_or_checkpoint,
    render_sport_home,
    render_teams_index,
)
from .schema import connect_readonly

_RE_SPORT = re.compile(r"^/(cfb|cbb)$")
_RE_TEAMS = re.compile(r"^/(cfb|cbb)/teams$")
_RE_TEAM = re.compile(r"^/(cfb|cbb)/teams/([a-z0-9-]+)$")
_RE_TEAM_SEASON = re.compile(r"^/(cfb|cbb)/teams/([a-z0-9-]+)/([0-9]{4})$")
_RE_SEASON = re.compile(r"^/(cfb|cbb)/([0-9]{4})$")
_RE_CHECKPOINT = re.compile(r"^/(cfb|cbb)/([0-9]{4})/(preseason|final|w[0-9]+)$")
_RE_WEEK_VIEW = re.compile(r"^/(cfb|cbb)/([0-9]{4})/w([0-9]+)/(results|schedule|spreads)$")
_RE_SEASON_VIEW = re.compile(r"^/(cfb|cbb)/([0-9]{4})/(results|schedule|spreads)$")
_RE_HONORS = re.compile(r"^/(cfb|cbb)/history/(champions|last-place)$")
_RE_EDITION = re.compile(r"^/(cfb|cbb)/editions/([A-Za-z0-9._-]+)$")
_RE_PERF_SEASON = re.compile(r"^/cfb/performance/([0-9]{4})$")
_RE_PERF_FILE = re.compile(r"^/cfb/performance/([0-9]{4})/(report\.json|games\.csv|weekly\.csv)$")
_RE_ASSET = re.compile(r"^/(?:(cfb|cbb)/)?assets/(.+)$")
_IMMUTABLE_ASSET = re.compile(r"^(?:program-marks|share-icons/[^/]+)/[0-9a-f]{64}\.png$|^team-logos/[0-9]+\.png$")

mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/javascript", ".js")


def _dicts_to_csv(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


class SportsRankApp:
    """Thread-safe WSGI application backed by a read-only SQLite database."""

    def __init__(self, db_path: Path, assets_dir: Path, *, immutable: bool = False):
        self.db_path = db_path.resolve()
        self.assets_dir = assets_dir.resolve()
        self.immutable = immutable
        self._local = threading.local()
        con = self._db()
        row = con.execute("SELECT value FROM meta WHERE key = 'release_id'").fetchone()
        self.release_id = row["value"] if row else "dev"

    def _db(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = connect_readonly(self.db_path, immutable=self.immutable)
            self._local.con = con
        return con

    def close(self) -> None:
        con = getattr(self._local, "con", None)
        if con is not None:
            con.close()
            self._local.con = None

    def _respond(
        self,
        environ: dict,
        start_response,
        status: str,
        body: bytes,
        *,
        content_type: str = "text/html; charset=utf-8",
        cache_control: str = "public, max-age=300, s-maxage=3600",
        extra_headers: list[tuple[str, str]] | None = None,
        etag: str | None = None,
    ):
        headers = [
            ("Content-Type", content_type),
            ("Cache-Control", cache_control),
            ("X-Content-Type-Options", "nosniff"),
        ]
        if etag:
            headers.append(("ETag", etag))
            if environ.get("HTTP_IF_NONE_MATCH") == etag:
                start_response("304 Not Modified", headers)
                return [b""]
        if extra_headers:
            headers.extend(extra_headers)
        headers.append(("Content-Length", str(len(body))))
        start_response(status, headers)
        if environ.get("REQUEST_METHOD") == "HEAD":
            return [b""]
        return [body]

    def _html(self, environ: dict, start_response, html: str, *, status: str = "200 OK", is_final: bool = False):
        body = html.encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()[:16]
        etag = f'W/"{self.release_id}-{digest}"'
        cc = (
            "public, max-age=3600, s-maxage=604800"
            if is_final
            else "public, max-age=300, s-maxage=3600"
        )
        return self._respond(environ, start_response, status, body, cache_control=cc, etag=etag)

    def _redirect(self, environ: dict, start_response, location: str):
        return self._respond(
            environ,
            start_response,
            "301 Moved Permanently",
            b"",
            content_type="text/plain; charset=utf-8",
            cache_control="public, max-age=86400, s-maxage=604800",
            extra_headers=[("Location", location)],
        )

    def _resolve_asset_file(self, sport: str | None, rel_path: str) -> Path | None:
        """Resolve asset under either a multi-sport root (website/) or a single-sport assets dir."""
        candidates = []
        if sport:
            candidates.append(self.assets_dir / sport / "assets" / rel_path)
        if sport in (None, "cfb"):
            candidates.append(self.assets_dir / rel_path)
            candidates.append(self.assets_dir / "cfb" / "assets" / rel_path)
        for cand in candidates:
            resolved = cand.resolve()
            if resolved.is_relative_to(self.assets_dir) and resolved.is_file():
                return resolved
        return None

    def _serve_asset(self, environ: dict, start_response, sport: str | None, rel_path: str):
        candidate = self._resolve_asset_file(sport, rel_path)
        if candidate is None:
            return self._not_found(environ, start_response)
        body = candidate.read_bytes()
        ctype, _ = mimetypes.guess_type(candidate.name)
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
        cc = (
            "public, max-age=31536000, immutable"
            if (_IMMUTABLE_ASSET.match(rel_path) or rel_path.endswith(".ttf"))
            else "public, max-age=86400, s-maxage=604800"
        )
        return self._respond(environ, start_response, "200 OK", body, content_type=ctype, cache_control=cc, etag=etag)

    def _serve_stored_page(self, environ: dict, start_response, con: sqlite3.Connection, path: str):
        row = con.execute(
            "SELECT sport_slug, active_nav, is_final, html_zlib FROM pages WHERE canonical_path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        html = zlib.decompress(row["html_zlib"]).decode("utf-8")
        if "<!--sr-masthead-->" in html:
            masthead = render_masthead(con, sport_slug=row["sport_slug"] or "cfb", active=row["active_nav"])
            html = html.replace("<!--sr-masthead-->", masthead, 1)
        return self._html(environ, start_response, html, is_final=bool(row["is_final"]))

    def _not_found(self, environ: dict, start_response):
        html = render_not_found(self._db())
        return self._respond(
            environ,
            start_response,
            "404 Not Found",
            html.encode("utf-8"),
            cache_control="public, max-age=60",
        )

    def __call__(self, environ: dict, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        if method not in ("GET", "HEAD"):
            return self._respond(
                environ,
                start_response,
                "405 Method Not Allowed",
                b"Method not allowed",
                content_type="text/plain; charset=utf-8",
                cache_control="no-store",
                extra_headers=[("Allow", "GET, HEAD")],
            )

        path = environ.get("PATH_INFO", "/") or "/"
        con = self._db()

        if path == "/healthz":
            payload = json.dumps({"status": "ok", "release_id": self.release_id}).encode("utf-8")
            return self._respond(
                environ, start_response, "200 OK", payload,
                content_type="application/json; charset=utf-8", cache_control="no-store",
            )

        # Static assets (/assets/*, /cfb/assets/*, /cbb/assets/*)
        if m := _RE_ASSET.match(path):
            return self._serve_asset(environ, start_response, m[1], m[2])

        # Legacy .html / trailing-slash redirects
        legacy_target = resolve_legacy_redirect(path)
        if legacy_target is not None:
            return self._redirect(environ, start_response, legacy_target)
        if path != "/" and path.endswith("/"):
            return self._redirect(environ, start_response, path.rstrip("/"))

        # Check stored rich clean-URL pages first (conferences, excitement, picks, CBB full archive, rich landings)
        stored = self._serve_stored_page(environ, start_response, con, path)
        if stored is not None:
            return stored

        # Check stored public JSON/CSV data blobs
        blob = con.execute(
            "SELECT content_type, is_final, data_zlib FROM blobs WHERE path = ?",
            (path,),
        ).fetchone()
        if blob is not None:
            cc = "public, max-age=3600, s-maxage=86400" if blob["is_final"] else "public, max-age=300, s-maxage=3600"
            return self._respond(
                environ, start_response, "200 OK", zlib.decompress(blob["data_zlib"]),
                content_type=blob["content_type"], cache_control=cc,
            )

        # Home fallback (when no rich landing page is stored in `pages`)
        if path == "/":
            return self._html(environ, start_response, render_home(con), is_final=False)

        # Sport landing fallback (/cfb, /cbb)
        if m := _RE_SPORT.match(path):
            html = render_sport_home(con, m[1])
            return self._html(environ, start_response, html) if html else self._not_found(environ, start_response)

        # Teams index (/cfb/teams, /cbb/teams)
        if m := _RE_TEAMS.match(path):
            html = render_teams_index(con, m[1])
            return self._html(environ, start_response, html) if html else self._not_found(environ, start_response)

        # Program history (/cfb/teams/{slug})
        if m := _RE_TEAM.match(path):
            html = render_program_history(con, m[1], m[2])
            return self._html(environ, start_response, html) if html else self._not_found(environ, start_response)

        # Program season (/cfb/teams/{slug}/{year})
        if m := _RE_TEAM_SEASON.match(path):
            year = int(m[3])
            html = render_program_season(con, m[1], m[2], year)
            if html is None:
                return self._not_found(environ, start_response)
            sn = con.execute(
                "SELECT sn.is_final FROM seasons sn JOIN sports s ON s.sport_id = sn.sport_id "
                "WHERE s.slug = ? AND sn.year = ?",
                (m[1], year),
            ).fetchone()
            return self._html(environ, start_response, html, is_final=bool(sn["is_final"]) if sn else False)

        # Season landing (/cfb/{year})
        if m := _RE_SEASON.match(path):
            html, is_final = render_season_or_checkpoint(con, m[1], int(m[2]))
            return self._html(environ, start_response, html, is_final=is_final) if html else self._not_found(environ, start_response)

        # Checkpoint (/cfb/{year}/{checkpoint})
        if m := _RE_CHECKPOINT.match(path):
            sport_slug, year, cp_code = m[1], int(m[2]), m[3]
            html, is_final = render_season_or_checkpoint(con, sport_slug, year, cp_code)
            if html is not None:
                return self._html(environ, start_response, html, is_final=is_final)
            if cp_code.startswith("w"):
                week_num = int(cp_code[1:])
                season = con.execute(
                    "SELECT sn.season_id, sn.is_final FROM seasons sn JOIN sports s ON s.sport_id = sn.sport_id "
                    "WHERE s.slug = ? AND sn.year = ?",
                    (sport_slug, year),
                ).fetchone()
                if season is not None:
                    if week_num == 0:
                        has_pre = con.execute(
                            "SELECT 1 FROM checkpoints WHERE season_id = ? AND code = 'PRESEASON'",
                            (season["season_id"],),
                        ).fetchone()
                        return self._redirect(
                            environ, start_response,
                            f"/{sport_slug}/{year}/preseason" if has_pre else f"/{sport_slug}/{year}",
                        )
                    if season["is_final"] and 1 <= week_num <= 25:
                        max_wk_row = con.execute(
                            "SELECT MAX(sort_order) FROM checkpoints WHERE season_id = ? AND code LIKE 'W%'",
                            (season["season_id"],),
                        ).fetchone()
                        if max_wk_row and max_wk_row[0] is not None and week_num > int(max_wk_row[0]):
                            return self._redirect(environ, start_response, f"/{sport_slug}/{year}/final")
            return self._not_found(environ, start_response)

        # Weekly & full-season game views
        if m := _RE_WEEK_VIEW.match(path):
            html, is_final = render_games_view(con, m[1], int(m[2]), m[4], week=int(m[3]))
            return self._html(environ, start_response, html, is_final=is_final) if html else self._not_found(environ, start_response)

        if m := _RE_SEASON_VIEW.match(path):
            html, is_final = render_games_view(con, m[1], int(m[2]), m[3])
            return self._html(environ, start_response, html, is_final=is_final) if html else self._not_found(environ, start_response)

        # Honors (/cfb/history/champions, /cfb/history/last-place)
        if m := _RE_HONORS.match(path):
            html = render_honors(con, m[1], m[2])
            return self._html(environ, start_response, html) if html else self._not_found(environ, start_response)

        # Dated editions fallback (/cbb/editions/{ts})
        if m := _RE_EDITION.match(path):
            html = render_edition(con, m[1], m[2])
            return self._html(environ, start_response, html, is_final=True) if html else self._not_found(environ, start_response)

        # Model performance HTML & downloads
        if path == "/cfb/performance":
            html = render_performance_index(con)
            return self._html(environ, start_response, html) if html else self._not_found(environ, start_response)

        if path in ("/cfb/performance/seasons.csv", "/cfb/performance/market-lines.json"):
            name = path.rsplit("/", 1)[1]
            pblob = con.execute(
                "SELECT pb.content_type, pb.data_zlib FROM performance_blobs pb "
                "JOIN sports s ON s.sport_id = pb.sport_id WHERE s.slug = 'cfb' AND pb.name = ?",
                (name,),
            ).fetchone()
            if pblob is None:
                return self._not_found(environ, start_response)
            raw = zlib.decompress(pblob["data_zlib"])
            return self._respond(
                environ, start_response, "200 OK", raw,
                content_type=pblob["content_type"], cache_control="public, max-age=300, s-maxage=3600",
            )

        if m := _RE_PERF_SEASON.match(path):
            html, is_final = render_performance_season(con, int(m[1]))
            return self._html(environ, start_response, html, is_final=is_final) if html else self._not_found(environ, start_response)

        if m := _RE_PERF_FILE.match(path):
            year, filename = int(m[1]), m[2]
            row = con.execute(
                "SELECT ps.report_json_zlib FROM performance_seasons ps "
                "JOIN sports s ON s.sport_id = ps.sport_id WHERE s.slug = 'cfb' AND ps.year = ?",
                (year,),
            ).fetchone()
            if row is None:
                return self._not_found(environ, start_response)
            raw_json = zlib.decompress(row["report_json_zlib"])
            if filename == "report.json":
                return self._respond(
                    environ, start_response, "200 OK", raw_json,
                    content_type="application/json; charset=utf-8",
                    cache_control="public, max-age=3600, s-maxage=86400",
                )
            data = json.loads(raw_json)
            key = "games" if filename == "games.csv" else "weekly"
            return self._respond(
                environ, start_response, "200 OK", _dicts_to_csv(data.get(key, [])),
                content_type="text/csv; charset=utf-8",
                cache_control="public, max-age=3600, s-maxage=86400",
            )

        return self._not_found(environ, start_response)


def create_app(db_path: Path, assets_dir: Path, *, immutable: bool = False) -> SportsRankApp:
    return SportsRankApp(db_path, assets_dir, immutable=immutable)


def from_env() -> SportsRankApp:
    """WSGI entrypoint factory for Gunicorn / Cloud Run containers."""
    db_path = Path(os.environ.get("SPORTSRANK_DB", ".sportsrank/web/sportsrank.sqlite3"))
    assets_dir = Path(os.environ.get("SPORTSRANK_ASSETS", "website"))
    immutable = os.environ.get("SPORTSRANK_DB_IMMUTABLE", "0") in ("1", "true", "yes")
    return create_app(db_path, assets_dir, immutable=immutable)


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        super().log_message(format, *args)


def serve_local(db_path: Path, assets_dir: Path, *, host: str = "127.0.0.1", port: int = 8080) -> None:
    app = create_app(db_path, assets_dir)
    httpd = make_server(host, port, app, server_class=_ThreadingWSGIServer, handler_class=_QuietHandler)
    print(f"Serving SportsRank from {db_path} at http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.close()
