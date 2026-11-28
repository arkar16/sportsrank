"""Build the versioned read-only SQLite serving database from offline archives."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata
import zlib

from lxml import html as lxml_html

from cfb.program_marks import program_logo_paths
from cfb.reference_archive import program_routes, read_archive

from .legacy import resolve_legacy_redirect, rewrite_html_links
from .schema import SCHEMA_VERSION, init_database

_MASTHEAD_RE = re.compile(
    r"(?:<!--sr-ui-->)?<header class=\"masthead\">.*?</header>(?:<!--/sr-ui-->)?",
    re.S | re.I,
)
_REL_HREF_FIX = re.compile(r'(<body\b[^>]*>)', re.I)

# CFB archive HTML prefixes that are rendered dynamically from normalized SQL tables
# rather than stored in the compressed `pages` table.
_SQL_RENDERED_CFB_PATTERNS = (
    re.compile(r"^/cfb/teams/"),
    re.compile(r"^/cfb/performance/"),
    re.compile(r"^/cfb/history/(?:nc|wt)_FBS_CFB_output\.html$"),
    re.compile(r"^/cfb/years/history/"),
    re.compile(r"^/cfb/years/[0-9]{4}/(?!excitement/)"),
)


def _slugify(name: str) -> str:
    slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", slug.replace("'", "").replace("&", " and ")).strip("-")


def _unique_slugs(names: list[str]) -> dict[str, str]:
    used: dict[str, str] = {}
    slugs: dict[str, str] = {}
    for name in sorted(set(names)):
        base = _slugify(name) or "team"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used[candidate] = name
        slugs[name] = candidate
    return slugs


def _cfb_checkpoint_sort(code: str) -> tuple[int, str]:
    if code == "PRESEASON":
        return (-1, "Preseason")
    if code == "FINAL":
        return (9999, "Final")
    week = int(code[1:])
    return (week, f"Week {week}")


def _compress_bytes(raw: bytes) -> bytes:
    return zlib.compress(raw, level=6)


def _matchup_pick_slug(away: str, home: str, neutral: bool) -> str:
    sep = "vs" if neutral else "at"
    raw = unicodedata.normalize("NFKD", f"{away} {sep} {home}").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def _export_cfb(con: sqlite3.Connection, site_dir: Path) -> dict[str, int]:
    from cfb.excitement_pages import aev_label, bev_label
    from cfb.game_excitement import Matchup, score_for, season_scores, site_reference

    seasons = read_archive(site_dir)
    if not seasons:
        return {"cfb_seasons": 0, "cfb_programs": 0, "cfb_rankings": 0, "cfb_games": 0}

    routes = program_routes(seasons)
    marks = program_logo_paths(site_dir)
    latest = seasons[-1]
    has_any_bev = any((site_dir / f"cfb/years/{s.year}/excitement/bev-v1.json").is_file() for s in seasons)
    excitement_ref = site_reference() if has_any_bev else None

    cur = con.execute(
        "INSERT INTO sports(slug, name, classification, current_season, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("cfb", "College football", "FBS", latest.year, latest.updated_at),
    )
    sport_id = int(cur.lastrowid)

    con.executemany(
        "INSERT OR REPLACE INTO school_marks(sport_id, school_name, mark_path) VALUES (?, ?, ?)",
        [(sport_id, school, path) for school, path in marks.items()],
    )

    program_ids: dict[str, int] = {}
    for name, route in sorted(routes.items()):
        slug = route.strip("/").split("/")[2]
        cur = con.execute(
            "INSERT INTO programs(sport_id, slug, canonical_name, mark_path) VALUES (?, ?, ?, ?)",
            (sport_id, slug, name, marks.get(name)),
        )
        program_ids[name] = int(cur.lastrowid)

    total_rankings = 0
    total_games = 0
    for season in seasons:
        checkpoints = list(season.rankings.keys())
        latest_cp = checkpoints[-1] if checkpoints else ""
        is_final = 1 if "FINAL" in season.rankings else 0
        year_dir = site_dir / f"cfb/years/{season.year}"
        scores = {}
        if excitement_ref is not None and (year_dir / "excitement/bev-v1.json").is_file():
            scores, _ = season_scores(year_dir, season, excitement_ref)
        has_excitement = 1 if (scores or (year_dir / "excitement/index.html").is_file()) else 0

        cur = con.execute(
            "INSERT INTO seasons(sport_id, year, label, is_final, updated_at, latest_checkpoint, "
            "home_advantage, has_excitement) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sport_id,
                season.year,
                str(season.year),
                is_final,
                season.updated_at,
                latest_cp,
                float(season.home_advantage),
                has_excitement,
            ),
        )
        season_id = int(cur.lastrowid)

        for cp_code, rows in season.rankings.items():
            sort_order, label = _cfb_checkpoint_sort(cp_code)
            cur = con.execute(
                "INSERT INTO checkpoints(season_id, code, label, sort_order, cutoff_utc) "
                "VALUES (?, ?, ?, ?, ?)",
                (season_id, cp_code, label, sort_order, ""),
            )
            cp_id = int(cur.lastrowid)
            ranking_rows = [
                (
                    cp_id,
                    program_ids[row.school],
                    row.rank,
                    format(row.rating, "f"),
                    float(row.rating),
                    row.record,
                    row.conference,
                )
                for row in rows
            ]
            con.executemany(
                "INSERT INTO rankings(checkpoint_id, program_id, rank, rating, rating_num, record_text, conference) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ranking_rows,
            )
            total_rankings += len(ranking_rows)

        picks_year_dir = site_dir / "cfb" / "picks" / str(season.year)
        has_picks_dir = picks_year_dir.is_dir()
        game_rows = []
        for g in season.games:
            has_final = g.home_score is not None and g.away_score is not None
            score = None
            if scores:
                key = Matchup(g.week, g.home, g.away, g.neutral_site)
                score = score_for(scores, key, (g.home_score, g.away_score))
            bev_html = bev_label(score, sport="cfb") if score else ""
            aev_html = aev_label(score, sport="cfb") if (score and has_final and score.aev is not None) else ""
            aev_estimated = 1 if (score and has_final and score.estimated) else 0
            pick_path = ""
            if has_picks_dir:
                pslug = _matchup_pick_slug(g.away, g.home, g.neutral_site)
                if (picks_year_dir / f"week-{g.week}" / f"{pslug}.html").is_file():
                    pick_path = f"/cfb/picks/{season.year}/week-{g.week}/{pslug}"
            game_rows.append(
                (
                    season_id,
                    g.week,
                    g.home,
                    g.away,
                    program_ids.get(g.home),
                    program_ids.get(g.away),
                    g.home_score,
                    g.away_score,
                    format(g.home_handicap, "f") if g.home_handicap is not None else None,
                    float(g.home_handicap) if g.home_handicap is not None else None,
                    1 if g.neutral_site else 0,
                    g.start_utc,
                    "final" if has_final else "scheduled",
                    g.home_division,
                    g.away_division,
                    1 if g.postseason else 0,
                    g.tipoff_label,
                    bev_html,
                    aev_html,
                    aev_estimated,
                    pick_path,
                )
            )
        con.executemany(
            "INSERT INTO games(season_id, week, home_school, away_school, home_program_id, "
            "away_program_id, home_score, away_score, home_handicap, home_handicap_num, "
            "neutral_site, start_utc, status, home_division, away_division, postseason, "
            "tipoff_label, bev_html, aev_html, aev_estimated, pick_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            game_rows,
        )
        total_games += len(game_rows)

    perf_count = _export_cfb_performance(con, sport_id, site_dir)
    return {
        "cfb_seasons": len(seasons),
        "cfb_programs": len(program_ids),
        "cfb_rankings": total_rankings,
        "cfb_games": total_games,
        "cfb_performance_seasons": perf_count,
    }


def _export_cfb_performance(con: sqlite3.Connection, sport_id: int, site_dir: Path) -> int:
    perf_dir = site_dir / "cfb" / "performance"
    if not perf_dir.is_dir():
        return 0

    for filename, content_type in (
        ("seasons.csv", "text/csv; charset=utf-8"),
        ("market-lines.json", "application/json; charset=utf-8"),
    ):
        path = perf_dir / filename
        if path.is_file():
            con.execute(
                "INSERT OR REPLACE INTO performance_blobs(sport_id, name, content_type, data_zlib) "
                "VALUES (?, ?, ?, ?)",
                (sport_id, filename, content_type, _compress_bytes(path.read_bytes())),
            )

    count = 0
    for subdir in sorted(perf_dir.iterdir()):
        if not subdir.is_dir() or not subdir.name.isdecimal():
            continue
        report_path = subdir / "report.json"
        if not report_path.is_file():
            continue
        raw = report_path.read_bytes()
        data = json.loads(raw)
        summary = data.get("summary", {})
        con.execute(
            "INSERT OR REPLACE INTO performance_seasons(sport_id, year, summary_json, report_json_zlib) "
            "VALUES (?, ?, ?, ?)",
            (sport_id, int(subdir.name), json.dumps(summary, separators=(",", ":")), _compress_bytes(raw)),
        )
        count += 1
    return count


def _parse_cbb_edition_html(path: Path) -> dict | None:
    """Parse an issued static CBB edition page so it survives without external DBs."""
    try:
        doc = lxml_html.fromstring(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    title_nodes = doc.xpath("//title/text()")
    title = title_nodes[0].strip() if title_nodes else path.stem
    edition_nodes = doc.xpath("//p[@class='edition']/text()")
    edition_text = edition_nodes[0].strip() if edition_nodes else ""

    tables = doc.xpath("//table")
    rankings = []
    if tables:
        for tr in tables[0].xpath(".//tbody/tr"):
            cells = [c.text_content().strip() for c in tr if c.tag in ("td", "th")]
            if len(cells) >= 4 and cells[0].isdigit():
                rankings.append({
                    "rank": int(cells[0]),
                    "team": cells[1],
                    "record": cells[2],
                    "rating": cells[3],
                })
    forecasts = []
    prepared_at = ""
    if len(tables) >= 2:
        prep_nodes = doc.xpath("//section[h2[contains(.,'Game forecasts')]]/p/text()")
        if prep_nodes:
            prepared_at = prep_nodes[0].strip()
        for tr in tables[1].xpath(".//tbody/tr"):
            cells = [c.text_content().strip() for c in tr if c.tag in ("td", "th")]
            if len(cells) >= 5:
                forecasts.append({
                    "tipoff": cells[0],
                    "away": cells[1],
                    "home": cells[2],
                    "pick": cells[3],
                    "margin": cells[4],
                })
    season_year = 2027
    match = re.search(r"(\d{4})[–-](\d{2})", edition_text or title)
    if match:
        season_year = int(match[1]) + 1
    return {
        "slug": path.stem,
        "season_year": season_year,
        "cutoff_utc": edition_text,
        "prepared_at": prepared_at,
        "title": title,
        "rankings": rankings,
        "forecasts": forecasts,
    }


def _export_cbb_metadata(con: sqlite3.Connection, site_dir: Path, cbb_db: Path | None) -> dict[str, int]:
    """Register CBB sport, dated editions, and optional replay rows when cbb_db is present."""
    editions_dir = site_dir / "cbb" / "editions"
    edition_files = sorted(editions_dir.glob("*.html")) if editions_dir.is_dir() else []
    parsed_editions = [e for p in edition_files if (e := _parse_cbb_edition_html(p)) is not None]
    has_cbb_dir = (site_dir / "cbb").is_dir()

    replays = []
    if cbb_db is not None and cbb_db.is_file():
        from cbb.replay import replay_seasons

        cbb_con = sqlite3.connect(cbb_db)
        try:
            row = cbb_con.execute("SELECT MAX(season) FROM games").fetchone()
            through_season = int(row[0]) if row and row[0] is not None else 2024
        finally:
            cbb_con.close()
        replays = list(replay_seasons(cbb_db, through_season=through_season))

    if not replays and not parsed_editions and not has_cbb_dir:
        return {"cbb_seasons": 0, "cbb_programs": 0, "cbb_rankings": 0, "cbb_games": 0, "cbb_editions": 0}

    if replays:
        current_season = replays[-1]["season"]
        updated_at = replays[-1]["final_cutoff_utc"]
    elif parsed_editions:
        current_season = parsed_editions[-1]["season_year"]
        updated_at = parsed_editions[-1]["slug"]
    else:
        current_season = 2027
        updated_at = ""

    cur = con.execute(
        "INSERT INTO sports(slug, name, classification, current_season, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("cbb", "Men’s college basketball", "Division I", current_season, updated_at),
    )
    sport_id = int(cur.lastrowid)

    for edition in parsed_editions:
        con.execute(
            "INSERT OR REPLACE INTO editions(sport_id, slug, season_year, cutoff_utc, prepared_at, "
            "title, rankings_json_zlib, forecasts_json_zlib) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sport_id,
                edition["slug"],
                edition["season_year"],
                edition["cutoff_utc"],
                edition["prepared_at"],
                edition["title"],
                _compress_bytes(json.dumps(edition["rankings"]).encode("utf-8")),
                _compress_bytes(json.dumps(edition["forecasts"]).encode("utf-8")),
            ),
        )

    all_schools: set[str] = set()
    for season_replay in replays:
        for cp in season_replay["rankings"]:
            for row in cp["rows"]:
                all_schools.add(row["school"])
        for row in season_replay["final_rankings"]:
            all_schools.add(row["school"])
    if not replays and parsed_editions:
        for row in parsed_editions[-1]["rankings"]:
            all_schools.add(row["team"])

    cfb_marks = dict(
        con.execute(
            "SELECT sm.school_name, sm.mark_path FROM school_marks sm "
            "JOIN sports s ON s.sport_id = sm.sport_id WHERE s.slug = 'cfb'"
        ).fetchall()
    )
    slugs = _unique_slugs(list(all_schools))
    program_ids: dict[str, int] = {}
    for name, slug in sorted(slugs.items()):
        cur = con.execute(
            "INSERT INTO programs(sport_id, slug, canonical_name, mark_path) VALUES (?, ?, ?, ?)",
            (sport_id, slug, name, cfb_marks.get(name)),
        )
        program_ids[name] = int(cur.lastrowid)

    total_rankings = 0
    total_games = 0
    if replays:
        latest_season_year = replays[-1]["season"]
        for season_replay in replays:
            year = season_replay["season"]
            label = f"{year - 1}–{str(year)[-2:]}"
            has_scheduled = any(f["status"] != "final" for f in season_replay["forecasts"])
            is_final = 0 if (year == latest_season_year and has_scheduled) else 1
            checkpoints = list(season_replay["rankings"])
            latest_cp = "FINAL" if is_final else f"W{checkpoints[-1]['week']}"
            cur = con.execute(
                "INSERT INTO seasons(sport_id, year, label, is_final, updated_at, latest_checkpoint) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sport_id, year, label, is_final, season_replay["final_cutoff_utc"], latest_cp),
            )
            season_id = int(cur.lastrowid)

            cutoff_to_week: dict[str, int] = {}
            for cp in checkpoints:
                week = int(cp["week"])
                code = f"W{week}"
                cutoff_to_week[cp["cutoff_utc"]] = week
                cur = con.execute(
                    "INSERT INTO checkpoints(season_id, code, label, sort_order, cutoff_utc) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (season_id, code, f"Week {week}", week, cp["cutoff_utc"]),
                )
                cp_id = int(cur.lastrowid)
                rows = [
                    (
                        cp_id,
                        program_ids[r["school"]],
                        int(r["rank"]),
                        f"{r['cors']:.2f}",
                        float(r["cors"]),
                        f"{r['wins']}–{r['losses']}",
                        "",
                    )
                    for r in cp["rows"]
                ]
                con.executemany(
                    "INSERT INTO rankings(checkpoint_id, program_id, rank, rating, rating_num, record_text, conference) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                total_rankings += len(rows)

            if is_final and season_replay["final_rankings"]:
                cur = con.execute(
                    "INSERT INTO checkpoints(season_id, code, label, sort_order, cutoff_utc) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (season_id, "FINAL", "Final", 9999, season_replay["final_cutoff_utc"]),
                )
                cp_id = int(cur.lastrowid)
                rows = [
                    (
                        cp_id,
                        program_ids[r["school"]],
                        int(r["rank"]),
                        f"{r['cors']:.2f}",
                        float(r["cors"]),
                        f"{r['wins']}–{r['losses']}",
                        "",
                    )
                    for r in season_replay["final_rankings"]
                ]
                con.executemany(
                    "INSERT INTO rankings(checkpoint_id, program_id, rank, rating, rating_num, record_text, conference) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                total_rankings += len(rows)

            game_rows = []
            for f in season_replay["forecasts"]:
                week = cutoff_to_week.get(f["cutoff_utc"], 0)
                handicap = -Decimal(f"{f['home_margin']:.2f}")
                game_rows.append(
                    (
                        season_id,
                        week,
                        f["home_team"],
                        f["away_team"],
                        program_ids.get(f["home_team"]),
                        program_ids.get(f["away_team"]),
                        f["home_points"],
                        f["away_points"],
                        format(handicap, "f"),
                        float(handicap),
                        1 if f["neutral_site"] else 0,
                        f["start_utc"],
                        f["status"],
                    )
                )
            con.executemany(
                "INSERT INTO games(season_id, week, home_school, away_school, home_program_id, "
                "away_program_id, home_score, away_score, home_handicap, home_handicap_num, "
                "neutral_site, start_utc, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                game_rows,
            )
            total_games += len(game_rows)
    elif parsed_editions:
        latest_ed = parsed_editions[-1]
        year = latest_ed["season_year"]
        label = f"{year - 1}–{str(year)[-2:]}"
        cur = con.execute(
            "INSERT INTO seasons(sport_id, year, label, is_final, updated_at, latest_checkpoint) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sport_id, year, label, 0, latest_ed["slug"], "W1"),
        )
        season_id = int(cur.lastrowid)
        cur = con.execute(
            "INSERT INTO checkpoints(season_id, code, label, sort_order, cutoff_utc) VALUES (?, ?, ?, ?, ?)",
            (season_id, "W1", "Current", 1, latest_ed["cutoff_utc"]),
        )
        cp_id = int(cur.lastrowid)
        rows = [
            (
                cp_id,
                program_ids[r["team"]],
                int(r["rank"]),
                str(r["rating"]),
                float(r["rating"]),
                str(r["record"]),
                "",
            )
            for r in latest_ed["rankings"]
        ]
        con.executemany(
            "INSERT INTO rankings(checkpoint_id, program_id, rank, rating, rating_num, record_text, conference) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        total_rankings += len(rows)

    return {
        "cbb_seasons": len(replays) if replays else (1 if parsed_editions else 0),
        "cbb_programs": len(program_ids),
        "cbb_rankings": total_rankings,
        "cbb_games": total_games,
        "cbb_editions": len(parsed_editions),
    }


def _is_sql_rendered_cfb(legacy_path: str) -> bool:
    return any(p.match(legacy_path) for p in _SQL_RENDERED_CFB_PATTERNS)


def _page_meta(canonical_path: str) -> tuple[str, str, int]:
    """Return (sport_slug, active_nav, is_final) for a stored clean-URL page."""
    if canonical_path.startswith("/cbb"):
        sport_slug = "cbb"
        if canonical_path.startswith("/cbb/teams"):
            active = "teams"
        elif canonical_path.startswith("/cbb/conferences"):
            active = "conferences"
        elif canonical_path.startswith("/cbb/performance"):
            active = "performance"
        elif canonical_path.startswith("/cbb/tournaments"):
            active = "tournaments"
        else:
            active = "cbb"
    elif canonical_path.startswith("/excitement"):
        sport_slug = ""
        active = "excitement"
    else:
        sport_slug = "cfb"
        if canonical_path.startswith("/cfb/conferences"):
            active = "conferences"
        elif canonical_path.startswith("/cfb/excitement") or canonical_path.endswith("/excitement"):
            active = "excitement"
        elif canonical_path == "/":
            active = ""
        else:
            active = "cfb"

    # Historical years and dated editions are immutable; current-season/index pages refresh regularly.
    year_match = re.search(r"/([12][0-9]{3})(?:/|$)", canonical_path)
    is_final = 0
    if "/editions/" in canonical_path:
        is_final = 1
    elif year_match:
        yr = int(year_match[1])
        is_final = 1 if (yr < 2026 or (sport_slug == "cbb" and yr < 2027)) else 0
    return sport_slug, active, is_final


def _export_rich_pages_and_blobs(con: sqlite3.Connection, site_dir: Path) -> dict[str, int]:
    """Store rich origin/main pages (conferences, excitement, picks, CBB, landings) with clean URLs."""
    page_batch: list[tuple[str, str, str, int, bytes]] = []
    blob_batch: list[tuple[str, str, int, bytes]] = []
    page_count = 0
    blob_count = 0

    def _flush_pages():
        nonlocal page_count
        if page_batch:
            con.executemany(
                "INSERT OR REPLACE INTO pages(canonical_path, sport_slug, active_nav, is_final, html_zlib) "
                "VALUES (?, ?, ?, ?, ?)",
                page_batch,
            )
            page_count += len(page_batch)
            page_batch.clear()

    def _flush_blobs():
        nonlocal blob_count
        if blob_batch:
            con.executemany(
                "INSERT OR REPLACE INTO blobs(path, content_type, is_final, data_zlib) VALUES (?, ?, ?, ?)",
                blob_batch,
            )
            blob_count += len(blob_batch)
            blob_batch.clear()

    for dirpath, dirnames, filenames in os.walk(site_dir):
        rel_dir = os.path.relpath(dirpath, site_dir).replace("\\", "/")
        # Skip static asset directories (served directly from disk) and legacy forecast shard JSONs
        if rel_dir in ("cfb/assets", "cbb/assets") or rel_dir.startswith(("cfb/assets/", "cbb/assets/")):
            dirnames[:] = []
            continue
        if rel_dir.endswith("/forecasts") or rel_dir.endswith("/snapshots"):
            dirnames[:] = []
            continue

        for filename in sorted(filenames):
            file_path = Path(dirpath) / filename
            rel = filename if rel_dir == "." else f"{rel_dir}/{filename}"
            legacy_path = "/" + rel

            if filename.endswith(".html"):
                if filename == "404.html" or _is_sql_rendered_cfb(legacy_path):
                    continue
                canonical = resolve_legacy_redirect(legacy_path)
                if canonical is None:
                    continue
                raw_bytes = file_path.read_bytes()
                try:
                    text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw_bytes.decode("latin-1")
                # Replace baked masthead with dynamic placeholder and rewrite all internal .html links
                text = _MASTHEAD_RE.sub("<!--sr-masthead-->", text, count=1)
                text = rewrite_html_links(text, legacy_path)
                sport_slug, active_nav, is_final = _page_meta(canonical)
                page_batch.append((canonical, sport_slug, active_nav, is_final, _compress_bytes(text.encode("utf-8"))))
                if len(page_batch) >= 500:
                    _flush_pages()
            elif filename.endswith((".json", ".csv", ".source.txt")) and rel not in (
                "manifest.json", "release.json", "historical-correction.json",
            ):
                # Skip sealed CFB season metadata receipts and CFB performance files stored separately
                if rel.startswith("cfb/performance/") or (filename == "metadata.json" and rel.startswith("cfb/years/")):
                    continue
                if filename.endswith(".json"):
                    ctype = "application/json; charset=utf-8"
                elif filename.endswith(".csv"):
                    ctype = "text/csv; charset=utf-8"
                else:
                    ctype = "text/plain; charset=utf-8"
                year_match = re.search(r"/([12][0-9]{3})(?:/|$)", legacy_path)
                is_final = 1 if (year_match and int(year_match[1]) < 2026) else 0
                blob_batch.append((legacy_path, ctype, is_final, _compress_bytes(file_path.read_bytes())))
                if len(blob_batch) >= 200:
                    _flush_blobs()

    _flush_pages()
    _flush_blobs()
    return {"stored_pages": page_count, "stored_blobs": blob_count}


def export_database(
    site_dir: Path,
    output_db: Path,
    *,
    cbb_db: Path | None = None,
    release_id: str | None = None,
) -> dict[str, int | str]:
    """Build a fresh, atomic, read-only-ready SQLite release database offline."""
    site_dir = site_dir.resolve()
    output_db = output_db.resolve()
    output_db.parent.mkdir(parents=True, exist_ok=True)

    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if release_id is None:
        release_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    fd, tmp_name = tempfile.mkstemp(prefix=".sportsrank-db-", suffix=".sqlite3", dir=output_db.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        con = sqlite3.connect(tmp_path)
        con.execute("PRAGMA journal_mode = MEMORY")
        con.execute("PRAGMA synchronous = OFF")
        init_database(con)

        stats: dict[str, int | str] = {
            "schema_version": SCHEMA_VERSION,
            "release_id": release_id,
            "built_at_utc": built_at,
        }
        stats.update(_export_cfb(con, site_dir))
        stats.update(_export_cbb_metadata(con, site_dir, cbb_db))
        stats.update(_export_rich_pages_and_blobs(con, site_dir))

        con.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in stats.items()],
        )
        con.commit()
        con.execute("PRAGMA journal_mode = DELETE")
        con.execute("ANALYZE")
        con.execute("VACUUM")
        con.close()

        tmp_path.chmod(0o644)
        tmp_path.replace(output_db)
        stats["bytes"] = output_db.stat().st_size
        return stats
    finally:
        tmp_path.unlink(missing_ok=True)
