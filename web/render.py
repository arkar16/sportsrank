"""Server-side HTML renderer using the SportsRank newspaper record-book design."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
import json
from math import atan, cos, sin, sqrt
import re
import sqlite3
import zlib

_MISSING = "—"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ESTIMATE_NOTE = "* AEV estimated from the available scoring totals and comparable games."
_MARGIN_ERROR = {"cfb": 15.0, "cbb": 11.0}
_RECORD_RE = re.compile(r"^(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?$")


def _win_probability(margin: float, *, sport_slug: str) -> float:
    """Variance-matched t(14) win probability matching cfb/win_probability.py."""
    sigma = _MARGIN_ERROR.get(sport_slug, 15.0)
    theta = atan(float(margin) / (sigma * sqrt(12 / 14) * sqrt(14)))
    sine, cosine = sin(theta), cos(theta)
    integral, half_integral = sine, 1.0
    for power in range(3, 14, 2):
        integral = (sine * cosine ** (power - 1) + (power - 1) * integral) / power
        half_integral *= (power - 1) / power
    return min(1.0, max(0.0, 0.5 + integral / (2 * half_integral)))


def _format_win_chance(handicap: Decimal | None, *, sport_slug: str) -> str:
    """Format a team handicap (negative when favored) as a whole percentage."""
    return _MISSING if handicap is None else f"{_win_probability(-float(handicap), sport_slug=sport_slug):.0%}"


def _project_record(record_text: str | None, probs: list[float]) -> str | None:
    if not record_text:
        return None
    match = _RECORD_RE.fullmatch(record_text.strip())
    if not match:
        return None
    w, l, t = (int(part or 0) for part in match.groups())
    wins = w + t / 2 + sum(probs)
    losses = l + t / 2 + sum(1.0 - p for p in probs)
    return f"{wins:.1f}–{losses:.1f}"


def _conference_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _conference_href(con: sqlite3.Connection, sport_slug: str, conf_name: str | None, year: int) -> str | None:
    if not conf_name or conf_name == _MISSING:
        return None
    slug = _conference_slug(conf_name)
    if not slug:
        return None
    path = f"/{sport_slug}/conferences/{slug}/{year}"
    exists = con.execute("SELECT 1 FROM pages WHERE canonical_path = ?", (path,)).fetchone()
    return path if exists else None


def _conference_paths_for_sport(con: sqlite3.Connection, sport_slug: str) -> set[str]:
    prefix = f"/{sport_slug}/conferences/"
    return {
        r[0]
        for r in con.execute(
            "SELECT canonical_path FROM pages WHERE canonical_path >= ? AND canonical_path < ?",
            (prefix, prefix[:-1] + "0"),
        ).fetchall()
    }


def _updated_time(value: str, label: str = "Updated") -> str:
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return ""
    zone = ""
    if stamp.tzinfo:
        stamp = stamp.astimezone(timezone.utc)
        zone = " UTC"
    clock = "" if len(value) == 10 else f" · {stamp:%H:%M}{zone}"
    return (
        f'<time class="archive-date" datetime="{escape(value.replace(" ", "T"))}" '
        f'title="{escape(value)}">{escape(label)} {_MONTHS[stamp.month - 1]} {stamp.day}, {stamp.year}{clock}</time>'
    )


def _mark_img(path: str | None, *, large: bool = False) -> str:
    if not path:
        return ""
    cls = "school-mark large" if large else "school-mark"
    size = 54 if large else 20
    return f'<img class="{cls}" src="{escape(path, quote=True)}" alt="" width="{size}" height="{size}" loading="lazy">'


def _team_label(
    name: str,
    href: str | None,
    mark_path: str | None,
    *,
    show_mark: bool = True,
) -> str:
    img = _mark_img(mark_path) if show_mark else ""
    label = escape(name)
    if href:
        return f'<a href="{escape(href)}">{img}{label}</a>'
    return f"<span data-school>{img}{label}</span>"


def _sports_nav(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute("SELECT sport_id, slug, name, classification, current_season, updated_at FROM sports ORDER BY sport_id").fetchall()
    return [dict(r) for r in rows]


def render_masthead(con: sqlite3.Connection, *, sport_slug: str = "cfb", active: str = "") -> str:
    """Render the single shared clean-URL site masthead."""
    sports = {s["slug"] for s in _sports_nav(con)}
    nav_items: list[tuple[str, str, str]] = [("/cfb", "College football", "cfb")]
    if "cbb" in sports:
        nav_items.append(("/cbb", "College basketball", "cbb"))
    if sport_slug == "cbb" and "cbb" in sports:
        nav_items.extend([
            ("/cbb/teams", "Teams", "teams"),
            ("/cbb/conferences", "Conferences", "conferences"),
            ("/cbb/tournaments", "Tournaments", "tournaments"),
            ("/cbb/performance", "Model performance", "performance"),
        ])
    else:
        nav_items.extend([
            ("/cfb/teams", "Teams", "teams"),
            ("/cfb/conferences", "Conferences", "conferences"),
            ("/cfb/performance", "Model performance", "performance"),
        ])
    nav_html = "".join(
        f'<a href="{href}"' + (' aria-current="page"' if key == active else "") + f">{escape(label)}</a>"
        for href, label, key in nav_items
    )
    return (
        '<header class="masthead"><a class="nameplate" href="/">SportsRank</a>'
        f'<nav aria-label="Site">{nav_html}</nav></header>'
    )


def _layout(
    con: sqlite3.Connection,
    title: str,
    body: str,
    *,
    active: str = "",
    body_class: str = "record-book",
    extra_css: tuple[str, ...] = (),
    footer: str = "",
    related_nav: list[tuple[str, str]] | None = None,
    sport_slug: str = "cfb",
) -> str:
    css_links = "".join(
        f'<link rel="stylesheet" href="{escape(href)}">'
        for href in (*extra_css, "/cfb/assets/record-book.css")
    )
    related_html = ""
    if related_nav:
        links = " · ".join(f'<a href="{escape(href)}">{escape(label)}</a>' for href, label in related_nav)
        related_html = f'<nav class="archive-nav" aria-label="Related records">{links}</nav>'
    footer_html = f"<footer>{footer}</footer>" if footer else ""
    cls_attr = f' class="{escape(body_class)}"' if body_class else ""
    return (
        "<!doctype html>\n"
        f'<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title>{css_links}</head>"
        f"<body{cls_attr}>"
        f"{render_masthead(con, sport_slug=sport_slug, active=active)}"
        f"{related_html}<main>{body}</main>{footer_html}</body></html>\n"
    )


def _season_archive_html(sport_slug: str, seasons: list[sqlite3.Row]) -> str:
    if sport_slug == "cfb":
        decades: dict[int, list[str]] = defaultdict(list)
        for s in seasons:
            year = int(s["year"])
            decades[year // 10 * 10].append(f'<a href="/cfb/{year}">{year}</a>')
        rows = "".join(
            f'<div><h3>{decade}s</h3><p>{" ".join(links)}</p></div>'
            for decade, links in decades.items()
        )
        return f'<section><h2>Season archive</h2><div class="year-archive">{rows}</div></section>'
    links = " ".join(f'<a href="/{sport_slug}/{s["year"]}">{escape(s["label"])}</a>' for s in seasons)
    return f'<section><h2>Season archive</h2><p class="week-links">{links}</p></section>'


def _top_table(con: sqlite3.Connection, sport_slug: str, season: sqlite3.Row, limit: int = 10) -> tuple[str, str]:
    cp = con.execute(
        "SELECT checkpoint_id, code, label FROM checkpoints WHERE season_id = ? AND code = ?",
        (season["season_id"], season["latest_checkpoint"]),
    ).fetchone()
    if cp is None:
        return "", ""
    rows = con.execute(
        "SELECT r.rank, r.rating, r.record_text, p.slug, p.canonical_name, p.mark_path "
        "FROM rankings r JOIN programs p ON p.program_id = r.program_id "
        "WHERE r.checkpoint_id = ? ORDER BY r.rank ASC LIMIT ?",
        (cp["checkpoint_id"], limit),
    ).fetchall()
    tr_html = "".join(
        f'<tr><td class="num">{r["rank"]}</td>'
        f'<th scope="row">{_team_label(r["canonical_name"], f"/{sport_slug}/teams/{r["slug"]}/{season["year"]}", r["mark_path"])}</th>'
        f'<td>{escape(r["record_text"])}</td><td class="num">{escape(r["rating"])}</td></tr>'
        for r in rows
    )
    edition = (
        "Final" if cp["code"] == "FINAL"
        else "Preseason" if cp["code"] == "PRESEASON"
        else f"Through {cp['label']}"
    )
    table = (
        f'<div class="table-scroll"><table><thead><tr>'
        '<th scope="col" class="num">Rank</th><th scope="col">Team</th>'
        '<th scope="col">Record</th><th scope="col" class="num">CORS rating</th>'
        f"</tr></thead><tbody>{tr_html}</tbody></table></div>"
    )
    return edition, table


def render_home(con: sqlite3.Connection) -> str:
    sports = {s["slug"]: s for s in _sports_nav(con)}
    cfb = sports.get("cfb")
    cbb = sports.get("cbb")
    sections = []

    if cfb:
        seasons = con.execute(
            "SELECT * FROM seasons WHERE sport_id = ? ORDER BY year DESC", (cfb["sport_id"],)
        ).fetchall()
        latest = seasons[0]
        edition, top_html = _top_table(con, "cfb", latest, limit=10)
        program_count = con.execute(
            "SELECT COUNT(*) FROM programs WHERE sport_id = ?", (cfb["sport_id"],)
        ).fetchone()[0]
        sections.append(
            f'<h1>College football</h1>'
            f'<p class="edition">{escape(latest["label"])} {escape(cfb["classification"])} &middot; {escape(edition)}</p>'
            f'<div class="home-columns">'
            f'<section><h2>CORS top 10</h2>{top_html}'
            f'<p><a href="/cfb/{latest["year"]}">{escape(latest["label"])} standings</a></p></section>'
            f'<section><h2>Record book</h2>'
            f'<p><a href="/cfb/teams">{program_count} program histories</a></p>'
            f'<p><a href="/cfb/history/champions">National champions</a></p>'
            f'<p><a href="/cfb/history/last-place">Last-place finishers</a></p>'
            f'<p><a href="/cfb/performance">Model performance</a></p>'
            + (
                f'<h2>Basketball</h2><p><a href="/cbb">Men&rsquo;s Division I rankings</a></p>'
                if cbb else ""
            )
            + "</section></div>"
            + _season_archive_html("cfb", seasons)
        )
    elif cbb:
        return render_sport_home(con, "cbb")

    return _layout(con, "SportsRank", "".join(sections), body_class="")


def render_sport_home(con: sqlite3.Connection, sport_slug: str) -> str | None:
    sport = con.execute("SELECT * FROM sports WHERE slug = ?", (sport_slug,)).fetchone()
    if sport is None:
        return None
    seasons = con.execute(
        "SELECT * FROM seasons WHERE sport_id = ? ORDER BY year DESC", (sport["sport_id"],)
    ).fetchall()
    if not seasons:
        return None
    latest = seasons[0]
    program_count = con.execute(
        "SELECT COUNT(*) FROM programs WHERE sport_id = ?", (sport["sport_id"],)
    ).fetchone()[0]

    if sport_slug == "cfb":
        edition, top_html = _top_table(con, "cfb", latest, limit=10)
        body = (
            f'<h1>{escape(sport["name"])}</h1>'
            f'<p class="edition">{escape(latest["label"])} {escape(sport["classification"])} &middot; {escape(edition)}</p>'
            f'<div class="home-columns">'
            f'<section><h2>CORS top 10</h2>{top_html}'
            f'<p><a href="/cfb/{latest["year"]}">{escape(latest["label"])} standings</a></p></section>'
            f'<section><h2>Record book</h2>'
            f'<p><a href="/cfb/teams">{program_count} program histories</a></p>'
            f'<p><a href="/cfb/history/champions">National champions</a></p>'
            f'<p><a href="/cfb/history/last-place">Last-place finishers</a></p>'
            f'<p><a href="/cfb/performance">Model performance</a></p></section></div>'
            + _season_archive_html("cfb", seasons)
        )
        return _layout(con, "College Football | SportsRank", body, active="cfb", body_class="")

    # Basketball (and future sports): show full latest rankings + editions + season archive
    cp = con.execute(
        "SELECT * FROM checkpoints WHERE season_id = ? AND code = ?",
        (latest["season_id"], latest["latest_checkpoint"]),
    ).fetchone()
    rows = (
        con.execute(
            "SELECT r.rank, r.rating, r.record_text, p.slug, p.canonical_name, p.mark_path "
            "FROM rankings r JOIN programs p ON p.program_id = r.program_id "
            "WHERE r.checkpoint_id = ? ORDER BY r.rank ASC",
            (cp["checkpoint_id"],),
        ).fetchall()
        if cp else []
    )
    tr_html = "".join(
        f'<tr><td class="num">{r["rank"]}</td>'
        f'<th scope="row">{_team_label(r["canonical_name"], f"/{sport_slug}/teams/{r["slug"]}/{latest["year"]}", r["mark_path"])}</th>'
        f'<td>{escape(r["record_text"])}</td><td class="num">{escape(r["rating"])}</td></tr>'
        for r in rows
    )
    editions = con.execute(
        "SELECT slug, title FROM editions WHERE sport_id = ? ORDER BY slug DESC", (sport["sport_id"],)
    ).fetchall()
    editions_html = ""
    if editions:
        ed_links = "".join(
            f'<li><a href="/{sport_slug}/editions/{escape(e["slug"])}">{escape(e["slug"])}</a></li>'
            for e in editions
        )
        editions_html = f'<section><h2>Dated editions</h2><ul class="week-links">{ed_links}</ul></section>'

    season_links = " · ".join(
        f'<a href="/{sport_slug}/{s["year"]}">{escape(s["label"])}</a>' for s in seasons
    )
    cutoff_note = f" &middot; {escape(cp['label'])}" if cp else ""
    body = (
        f'<h1>{escape(sport["name"])}</h1>'
        f'<p class="edition">{escape(latest["label"])} {escape(sport["classification"])}{cutoff_note}</p>'
        f'<p><a href="/{sport_slug}/{latest["year"]}">{escape(latest["label"])} checkpoints &amp; games</a>'
        f' &middot; <a href="/{sport_slug}/teams">{program_count} team histories</a>'
        f' &middot; <a href="/{sport_slug}/history/champions">Season champions</a></p>'
        f'<section><h2>CORS rankings</h2>'
        f'<div class="table-scroll"><table><thead><tr>'
        '<th scope="col" class="num">Rank</th><th scope="col">Team</th>'
        '<th scope="col">Record</th><th scope="col" class="num">CORS rating</th>'
        f"</tr></thead><tbody>{tr_html}</tbody></table></div></section>"
        f"{editions_html}"
        f'<section><h2>Seasons</h2><p>{season_links}</p></section>'
    )
    return _layout(con, f"{sport['name']} | SportsRank", body, active=sport_slug)


def render_teams_index(con: sqlite3.Connection, sport_slug: str) -> str | None:
    sport = con.execute("SELECT * FROM sports WHERE slug = ?", (sport_slug,)).fetchone()
    if sport is None:
        return None
    programs = con.execute(
        "SELECT slug, canonical_name, mark_path FROM programs WHERE sport_id = ? "
        "ORDER BY canonical_name COLLATE NOCASE ASC",
        (sport["sport_id"],),
    ).fetchall()
    items = "".join(
        f'<li>{_team_label(p["canonical_name"], f"/{sport_slug}/teams/{p["slug"]}", p["mark_path"])}</li>'
        for p in programs
    )
    heading = f'{sport["name"]} teams'
    body = f'<h1>{escape(heading)}</h1><ul class="program-list">{items}</ul>'
    footer = _updated_time(sport["updated_at"], "Rankings updated") if sport["updated_at"] else ""
    active = "teams" if sport_slug == "cfb" else sport_slug
    return _layout(con, f"{heading} | SportsRank", body, active=active, footer=footer)


def _rank_trend_svg(sport_slug: str, team_slug: str, final_ranks: list[tuple[int, int]]) -> str:
    if not final_ranks:
        return ""
    left, right, top, bottom = 39, 452, 13, 116
    first, last = final_ranks[0][0], final_ranks[-1][0]
    last_rank = max(5, max(rank for _, rank in final_ranks))

    def x(year: int) -> float:
        return (left + right) / 2 if first == last else left + (right - left) * (year - first) / (last - first)

    def y(rank: int) -> float:
        return top + (bottom - top) * (rank - 1) / (last_rank - 1)

    grid = []
    for rank in (1, (last_rank + 1) // 2, last_rank):
        h = y(rank)
        grid.append(
            f'<line x1="{left}" y1="{h:.2f}" x2="{right}" y2="{h:.2f}" stroke="currentColor" opacity=".2"/>'
            f'<text x="{left - 7}" y="{h + 4:.2f}" text-anchor="end">{rank}</text>'
        )
    years = [f'<text x="{x(yr):.2f}" y="136" text-anchor="middle">{yr}</text>' for yr in dict.fromkeys((first, last))]
    segments = [
        f'<line x1="{x(yr):.2f}" y1="{y(r):.2f}" x2="{x(nyr):.2f}" y2="{y(nr):.2f}"/>'
        for (yr, r), (nyr, nr) in zip(final_ranks, final_ranks[1:])
        if nyr == yr + 1
    ]
    dots = []
    for i, (yr, rank) in enumerate(final_ranks):
        cx, cy = x(yr), y(rank)
        start = max(cx - 12, (cx + x(final_ranks[i - 1][0])) / 2) if i else cx - 12
        end = min(cx + 12, (cx + x(final_ranks[i + 1][0])) / 2) if i + 1 < len(final_ranks) else cx + 12
        label = f"{yr} season, final rank {rank}"
        dots.append(
            f'<a href="/{sport_slug}/teams/{escape(team_slug)}/{yr}" aria-label="{label}">'
            f"<title>{label}</title>"
            f'<rect x="{start:.2f}" y="{cy - 12:.2f}" width="{end - start:.2f}" height="24" fill="transparent"/>'
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.5"/></a>'
        )
    return (
        '<figure class="rank-trend"><figcaption id="rank-trend-title">Final national rank</figcaption>'
        '<svg viewBox="0 0 480 142" width="100%" role="group" aria-labelledby="rank-trend-title" '
        'aria-describedby="rank-trend-description">'
        '<desc id="rank-trend-description">Gaps mark seasons without a final rank. See Season history for each rank.</desc>'
        '<g aria-hidden="true" fill="currentColor" font-size="12">'
        + "".join(grid + years)
        + '</g><g aria-hidden="true" stroke="currentColor" stroke-width="1.5">'
        + "".join(segments)
        + '</g><g fill="currentColor">'
        + "".join(dots)
        + "</g></svg></figure>\n"
    )


def render_program_history(con: sqlite3.Connection, sport_slug: str, team_slug: str) -> str | None:
    row = con.execute(
        "SELECT p.*, s.name AS sport_name FROM programs p "
        "JOIN sports s ON s.sport_id = p.sport_id WHERE s.slug = ? AND p.slug = ?",
        (sport_slug, team_slug),
    ).fetchone()
    if row is None:
        return None

    history_rows = con.execute(
        """
        SELECT
            sn.year, sn.label AS season_label, sn.updated_at, sn.latest_checkpoint,
            MIN(r.rank) AS best_rank,
            MAX(CASE WHEN cp.code = sn.latest_checkpoint THEN r.rank END) AS latest_rank,
            MAX(CASE WHEN cp.code = sn.latest_checkpoint THEN r.rating END) AS latest_rating,
            MAX(CASE WHEN cp.code = sn.latest_checkpoint THEN r.record_text END) AS latest_record,
            MAX(CASE WHEN cp.code = sn.latest_checkpoint THEN r.conference END) AS latest_conf,
            MAX(CASE WHEN cp.code = sn.latest_checkpoint THEN cp.label END) AS latest_cp_label,
            MAX(CASE WHEN cp.code = 'FINAL' THEN r.rank END) AS final_rank
        FROM rankings r
        JOIN checkpoints cp ON cp.checkpoint_id = r.checkpoint_id
        JOIN seasons sn ON sn.season_id = cp.season_id
        WHERE r.program_id = ?
        GROUP BY sn.season_id
        ORDER BY sn.year DESC
        """,
        (row["program_id"],),
    ).fetchall()

    final_ranks = [(int(h["year"]), int(h["final_rank"])) for h in reversed(history_rows) if h["final_rank"] is not None]
    svg = _rank_trend_svg(sport_slug, team_slug, final_ranks)

    best_cps_rows = con.execute(
        """
        SELECT sn.year, cp.code, cp.label, cp.sort_order, r.rank
        FROM rankings r
        JOIN checkpoints cp ON cp.checkpoint_id = r.checkpoint_id
        JOIN seasons sn ON sn.season_id = cp.season_id
        WHERE r.program_id = ?
        ORDER BY sn.year DESC, cp.sort_order ASC
        """,
        (row["program_id"],),
    ).fetchall()
    best_cps_by_year: dict[int, list[tuple[str, str]]] = defaultdict(list)
    best_rank_by_year = {int(h["year"]): int(h["best_rank"]) for h in history_rows}
    for b in best_cps_rows:
        yr = int(b["year"])
        if int(b["rank"]) == best_rank_by_year.get(yr):
            best_cps_by_year[yr].append((b["code"], b["label"]))

    conf_pages = _conference_paths_for_sport(con, sport_slug)

    tr_list = []
    for h in history_rows:
        yr = int(h["year"])
        cp_code = h["latest_checkpoint"] or ""
        cp_label = h["latest_cp_label"] or cp_code
        as_of = f"Through {cp_label}" if cp_code.startswith("W") else cp_label
        best_rank = h["best_rank"]
        best_cps = best_cps_by_year.get(yr, [])
        if best_cps:
            first_code, first_label = best_cps[0]
            best_href = f"/{sport_slug}/{yr}/{first_code.lower()}"
            rank_label = f'{h["season_label"]} best rank {best_rank}'
            if len(best_cps) == 1:
                b_title = first_label
                b_aria = f"{rank_label}, {first_label}"
            else:
                b_title = f"First reached {first_label}"
                b_aria = f"{rank_label}, first reached {first_label}"
            best_html = (
                f'<a href="{escape(best_href)}" title="{escape(b_title)}" '
                f'aria-label="{escape(b_aria)}">{best_rank}</a>'
            )
        else:
            best_html = str(best_rank)

        conf = h["latest_conf"] or ""
        if conf and conf != _MISSING:
            c_path = f"/{sport_slug}/conferences/{_conference_slug(conf)}/{yr}"
            conf_html = f'<a href="{escape(c_path)}">{escape(conf)}</a>' if c_path in conf_pages else escape(conf)
        else:
            conf_html = _MISSING

        tr_list.append(
            f'<tr><th scope="row"><a href="/{sport_slug}/teams/{escape(team_slug)}/{yr}">{escape(h["season_label"])}</a></th>'
            f"<td>{escape(as_of)}</td>"
            f'<td class="num">{h["latest_rank"] if h["latest_rank"] is not None else _MISSING}</td>'
            f'<td class="num">{escape(h["latest_rating"]) if h["latest_rating"] is not None else _MISSING}</td>'
            f'<td class="num">{best_html}</td>'
            f'<td class="num">{escape(h["latest_record"]) if h["latest_record"] is not None else _MISSING}</td>'
            f"<td>{conf_html}</td></tr>"
        )
    table = (
        '<div class="table-scroll" role="region" aria-label="Season history" tabindex="0">'
        "<table><caption>Season history</caption><thead><tr>"
        '<th scope="col">Season</th><th scope="col">As of</th><th scope="col">National rank</th>'
        '<th scope="col">CORS rating</th><th scope="col">Best rank</th>'
        '<th scope="col">Record</th><th scope="col">Conference</th>'
        f'</tr></thead><tbody>{"".join(tr_list)}</tbody></table></div>'
    )

    heading = f'<span data-program="{escape(row["canonical_name"])}">{_mark_img(row["mark_path"], large=True)}{escape(row["canonical_name"])}</span>'
    sport_noun = "football" if sport_slug == "cfb" else "basketball"
    footer = _updated_time(history_rows[0]["updated_at"], "Rankings updated") if history_rows and history_rows[0]["updated_at"] else ""
    active = "teams" if sport_slug == "cfb" else sport_slug
    return _layout(
        con,
        f'{row["canonical_name"]} {sport_noun} | SportsRank',
        f"<h1>{heading}</h1>{svg}{table}",
        active=active,
        footer=footer,
    )


def render_program_season(con: sqlite3.Connection, sport_slug: str, team_slug: str, year: int) -> str | None:
    row = con.execute(
        "SELECT p.*, s.sport_id, sn.season_id, sn.label AS season_label, sn.updated_at, "
        "sn.is_final, sn.latest_checkpoint, sn.home_advantage, sn.has_excitement "
        "FROM programs p "
        "JOIN sports s ON s.sport_id = p.sport_id "
        "JOIN seasons sn ON sn.sport_id = s.sport_id AND sn.year = ? "
        "WHERE s.slug = ? AND p.slug = ?",
        (year, sport_slug, team_slug),
    ).fetchone()
    if row is None:
        return None

    school = row["canonical_name"]
    season_id = row["season_id"]
    program_id = row["program_id"]
    sport_id = row["sport_id"]
    is_final = bool(row["is_final"])
    home_advantage = float(row["home_advantage"])

    # Checkpoints and this team's standings
    cps = con.execute(
        "SELECT cp.checkpoint_id, cp.code, cp.label, cp.sort_order, r.rank, r.rating, r.record_text, r.conference "
        "FROM checkpoints cp LEFT JOIN rankings r ON r.checkpoint_id = cp.checkpoint_id AND r.program_id = ? "
        "WHERE cp.season_id = ? ORDER BY cp.sort_order ASC",
        (program_id, season_id),
    ).fetchall()
    if not any(c["rank"] is not None for c in cps):
        return None

    # All programs ranked in this season -> clean season-scoped links
    ranked_slugs = {
        r["canonical_name"]: r["slug"]
        for r in con.execute(
            "SELECT DISTINCT p.canonical_name, p.slug FROM rankings r "
            "JOIN checkpoints cp ON cp.checkpoint_id = r.checkpoint_id "
            "JOIN programs p ON p.program_id = r.program_id WHERE cp.season_id = ?",
            (season_id,),
        ).fetchall()
    }
    all_slugs = {
        r["canonical_name"]: r["slug"]
        for r in con.execute("SELECT canonical_name, slug FROM programs WHERE sport_id = ?", (sport_id,)).fetchall()
    }
    marks = {
        r["school_name"]: r["mark_path"]
        for r in con.execute("SELECT school_name, mark_path FROM school_marks WHERE sport_id = ?", (sport_id,)).fetchall()
    }

    games = con.execute(
        "SELECT * FROM games WHERE season_id = ? AND (home_program_id = ? OR away_program_id = ?) "
        "ORDER BY week ASC, game_id ASC",
        (season_id, program_id, program_id),
    ).fetchall()
    games_by_week: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for g in games:
        games_by_week[int(g["week"])].append(g)

    cp_map = {c["code"]: c for c in cps}
    weeks = sorted({int(c["code"][1:]) for c in cps if c["code"].startswith("W")} | set(games_by_week.keys()))
    ordered_codes = []
    if "PRESEASON" in cp_map:
        ordered_codes.append("PRESEASON")
    ordered_codes.extend(f"W{w}" for w in weeks)
    if "FINAL" in cp_map:
        ordered_codes.append("FINAL")

    through_week: int | None = None
    if not is_final:
        w_nums = [int(c["code"][1:]) for c in cps if c["code"].startswith("W")]
        through_week = max(w_nums, default=-1)

    def _is_upcoming(g: sqlite3.Row) -> bool:
        return (
            through_week is not None
            and int(g["week"]) > through_week
            and g["home_score"] is None
            and g["away_score"] is None
        )

    has_upcoming = any(_is_upcoming(g) for g in games)
    has_excitement = bool(row["has_excitement"]) or any(g["bev_html"] or g["aev_html"] for g in games)
    has_estimate = False

    # Projected season record (expected wins & losses) for active seasons
    latest_cp = cp_map.get(row["latest_checkpoint"])
    if latest_cp is None or latest_cp["rank"] is None:
        latest_cp = next((c for c in reversed(cps) if c["rank"] is not None), None)
    projection: str | None = None
    if not is_final and latest_cp is not None:
        latest_ratings = {
            r["canonical_name"]: float(r["rating_num"])
            for r in con.execute(
                "SELECT p.canonical_name, r.rating_num FROM rankings r "
                "JOIN programs p ON p.program_id = r.program_id WHERE r.checkpoint_id = ?",
                (latest_cp["checkpoint_id"],),
            ).fetchall()
        }
        rem_probs: list[float] = []
        for g in games:
            if g["postseason"] or not _is_upcoming(g):
                continue
            if g["home_handicap"] is not None:
                home_margin = -float(g["home_handicap"])
            elif sport_slug == "cfb":
                h_rat = latest_ratings.get(
                    g["home_school"],
                    -26.0 if g["home_division"].lower() == "fcs" else -22.0,
                )
                a_rat = latest_ratings.get(
                    g["away_school"],
                    -26.0 if g["away_division"].lower() == "fcs" else -22.0,
                )
                venue = 0.0 if g["neutral_site"] else home_advantage
                home_margin = h_rat - a_rat + venue
            else:
                continue
            home_prob = _win_probability(home_margin, sport_slug=sport_slug)
            at_home = g["home_program_id"] == program_id
            rem_probs.append(home_prob if at_home else 1.0 - home_prob)
        projection = _project_record(latest_cp["record_text"], rem_probs)

    anchor = ' id="remaining-schedule"'

    def _row_start(upcoming: bool) -> str:
        nonlocal anchor
        if upcoming and anchor:
            start = f"<tr{anchor}>"
            anchor = ""
            return start
        return "<tr>"

    seen_marks: set[str] = set()
    groups = []
    for code in ordered_codes:
        cp = cp_map.get(code)
        wk_games = games_by_week.get(int(code[1:]), []) if code.startswith("W") else []
        span = f' rowspan="{len(wk_games)}"' if len(wk_games) > 1 else ""
        cp_label = cp["label"] if cp else (f"Week {code[1:]}" if code.startswith("W") else code.title())
        rank_s = str(cp["rank"]) if (cp and cp["rank"] is not None) else _MISSING
        rating_s = escape(cp["rating"]) if (cp and cp["rating"] is not None) else _MISSING
        rec_s = escape(cp["record_text"]) if (cp and cp["record_text"] is not None) else _MISSING

        if wk_games:
            sorted_games = sorted(((_is_upcoming(g), g) for g in wk_games), key=lambda item: item[0])
            game_rows = []
            for upcoming, g in sorted_games:
                at_home = g["home_program_id"] == program_id
                opp = g["away_school"] if at_home else g["home_school"]
                side = "Neutral" if g["neutral_site"] else ("Home" if at_home else "Away")
                ts, os_ = (g["home_score"], g["away_score"]) if at_home else (g["away_score"], g["home_score"])
                score_s = f"{ts}–{os_}" if (ts is not None and os_ is not None) else _MISSING
                handicap: Decimal | None = None
                spread_s = _MISSING
                if g["home_handicap"] is not None:
                    h = Decimal(g["home_handicap"])
                    handicap = h if at_home else h.copy_negate()
                    spread_s = format(handicap, "+f") if handicap else format(handicap.copy_abs(), "f")
                if opp in ranked_slugs:
                    opp_href = f"/{sport_slug}/teams/{ranked_slugs[opp]}/{year}"
                elif opp in all_slugs:
                    opp_href = f"/{sport_slug}/teams/{all_slugs[opp]}"
                else:
                    opp_href = None
                show_mark = opp not in seen_marks
                seen_marks.add(opp)
                opp_html = _team_label(opp, opp_href, marks.get(opp), show_mark=show_mark)
                if g["tipoff_label"]:
                    opp_html += f'<small>{escape(g["tipoff_label"])}</small>'
                cells = f'<td>{opp_html}</td><td>{side}</td><td class="num">{spread_s}</td>'
                if has_upcoming:
                    chance = _format_win_chance(handicap, sport_slug=sport_slug) if upcoming else _MISSING
                    cells += f'<td class="num">{chance}</td>'
                cells += f'<td class="num">{score_s}</td>'
                if has_excitement:
                    if g["aev_estimated"]:
                        has_estimate = True
                    bev_cell = g["bev_html"] or "&mdash;"
                    aev_cell = g["aev_html"] or "&mdash;"
                    cells += f'<td class="num">{bev_cell}</td><td class="num">{aev_cell}</td>'
                game_rows.append((_row_start(upcoming), cells))
        else:
            opp_label = "No game listed" if code.startswith("W") else _MISSING
            extra_dashes = 3 + (1 if has_upcoming else 0) + (2 if has_excitement else 0)
            game_rows = [("<tr>", f"<td>{opp_label}</td>" + f"<td>{_MISSING}</td>" * extra_dashes)]

        groups.append(
            f'<tbody>{game_rows[0][0]}<th scope="rowgroup"{span}>{escape(cp_label)}</th>'
            f'<td class="num"{span}>{rank_s}</td><td class="num"{span}>{rating_s}</td>'
            f'<td{span}>{rec_s}</td>{game_rows[0][1]}</tr>'
            + "".join(f"{start}{cell}</tr>" for start, cell in game_rows[1:])
            + "</tbody>"
        )

    headers = [
        '<th scope="col">Week</th>',
        '<th scope="col">National rank</th>',
        '<th scope="col">CORS rating</th>',
        '<th scope="col">Record</th>',
        '<th scope="col">Opponent</th>',
        '<th scope="col">Site</th>',
        '<th scope="col">CORS spread <small>Negative if favored</small></th>',
    ]
    if has_upcoming:
        headers.append('<th scope="col">Win chance</th>')
    headers.append(f'<th scope="col">Score <small>{escape(school)} first</small></th>')
    if has_excitement:
        headers.append('<th scope="col">BEV <small>Pregame appeal</small></th>')
        headers.append('<th scope="col">AEV <small>Postgame drama</small></th>')

    log = (
        '<div class="table-scroll" role="region" aria-label="Rankings and games" tabindex="0">'
        '<table aria-label="Rankings and games"><thead><tr>'
        + "".join(headers)
        + f'</tr></thead>{"".join(groups)}</table></div>'
    )
    if has_estimate:
        log += f"<p>{_ESTIMATE_NOTE}</p>"
    if has_upcoming:
        log = '<p><a href="#remaining-schedule">Remaining schedule</a></p><h2>Weekly rankings and games</h2>' + log
    if projection is not None or has_upcoming:
        log += f'<p id="projected-season-record">Projected season record <strong>{escape(projection or _MISSING)}</strong></p>'

    heading = f'{escape(row["season_label"])} <span data-program="{escape(school)}">{_mark_img(row["mark_path"], large=True)}{escape(school)}</span>'
    sport_noun = "football" if sport_slug == "cfb" else "basketball"
    footer = _updated_time(row["updated_at"], "Rankings updated") if row["updated_at"] else ""
    related = [
        (f"/{sport_slug}/teams/{team_slug}", f"{school} history"),
        (f"/{sport_slug}/{year}", f'{row["season_label"]} season'),
    ]
    latest_conf = latest_cp["conference"] if latest_cp else None
    conf_href = _conference_href(con, sport_slug, latest_conf, year)
    if conf_href and latest_conf:
        related.append((conf_href, f'{row["season_label"]} {latest_conf}'))
    if has_excitement:
        exc_path = f"/{sport_slug}/{year}/excitement"
        if con.execute("SELECT 1 FROM pages WHERE canonical_path = ?", (exc_path,)).fetchone():
            related.append((exc_path, "Game excitement"))

    return _layout(
        con,
        f'{row["season_label"]} {school} {sport_noun} | SportsRank',
        f"<h1>{heading}</h1>{log}",
        active="teams" if sport_slug == "cfb" else sport_slug,
        footer=footer,
        related_nav=related,
        sport_slug=sport_slug,
    )


def _upsets_html(
    sport_slug: str,
    year: int,
    games: list[sqlite3.Row],
    ranked_slugs: dict[str, str],
    marks: dict[str, str],
) -> str:
    qualifying = []
    has_scored_forecast = False
    for g in games:
        if g["home_handicap"] is None or g["home_score"] is None or g["away_score"] is None:
            continue
        handicap = Decimal(g["home_handicap"])
        if not handicap.is_finite():
            continue
        has_scored_forecast = True
        deficit = handicap.copy_abs()
        if deficit < 7:
            continue
        if handicap < 0 and g["away_score"] > g["home_score"]:
            qualifying.append((deficit, g["week"], g["home_school"], g["away_school"], g["away_score"], g["home_score"]))
        elif handicap > 0 and g["home_score"] > g["away_score"]:
            qualifying.append((deficit, g["week"], g["away_school"], g["home_school"], g["home_score"], g["away_score"]))

    qualifying.sort(key=lambda u: u[0], reverse=True)
    title = "Biggest CORS upsets" if qualifying else "CORS upsets"
    heading = f'<section aria-labelledby="cors-upsets"><h2 id="cors-upsets">{title}</h2>'
    if not qualifying:
        note = "No wins by underdogs of 7+ points." if has_scored_forecast else "No final scores with a CORS spread."
        return heading + f"<p>{note}</p></section>"

    rows = []
    for deficit, wk, fav, winner, ws, ls in qualifying[:3]:
        fav_href = f"/{sport_slug}/teams/{ranked_slugs[fav]}/{year}" if fav in ranked_slugs else None
        win_href = f"/{sport_slug}/teams/{ranked_slugs[winner]}/{year}" if winner in ranked_slugs else None
        rows.append(
            f'<tr><td class="num">{wk}</td>'
            f'<td>{_team_label(fav, fav_href, marks.get(fav))} by <span class="num">{deficit:.2f}</span></td>'
            f'<td>{_team_label(winner, win_href, marks.get(winner))} <span class="num">{ws}–{ls}</span> {_team_label(fav, fav_href, marks.get(fav), show_mark=False)}</td></tr>'
        )
    return (
        heading
        + '<div class="table-scroll" role="region" aria-labelledby="cors-upsets" tabindex="0">'
        '<table aria-labelledby="cors-upsets"><thead><tr>'
        '<th scope="col" class="num">Week</th><th scope="col">CORS favorite</th><th scope="col">Final score</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def render_season_or_checkpoint(
    con: sqlite3.Connection,
    sport_slug: str,
    year: int,
    checkpoint_code: str | None = None,
) -> tuple[str | None, bool]:
    """Return (html, is_final) for /{sport}/{year} or /{sport}/{year}/{checkpoint}."""
    season = con.execute(
        "SELECT sn.*, s.sport_id, s.name AS sport_name FROM seasons sn "
        "JOIN sports s ON s.sport_id = sn.sport_id WHERE s.slug = ? AND sn.year = ?",
        (sport_slug, year),
    ).fetchone()
    if season is None:
        return None, False

    cps = con.execute(
        "SELECT * FROM checkpoints WHERE season_id = ? ORDER BY sort_order ASC",
        (season["season_id"],),
    ).fetchall()
    if not cps:
        return None, bool(season["is_final"])

    if checkpoint_code is None:
        target = cps[-1]
        is_landing = True
    else:
        target_upper = checkpoint_code.upper()
        target = next((c for c in cps if c["code"].upper() == target_upper), None)
        if target is None:
            return None, bool(season["is_final"])
        is_landing = False

    idx = next(i for i, c in enumerate(cps) if c["checkpoint_id"] == target["checkpoint_id"])
    baseline = None
    if idx > 0:
        baseline = cps[0] if target["code"] == "FINAL" else cps[idx - 1]

    rows = con.execute(
        "SELECT r.rank, r.rating, r.rating_num, r.record_text, r.conference, "
        "p.program_id, p.slug, p.canonical_name, p.mark_path "
        "FROM rankings r JOIN programs p ON p.program_id = r.program_id "
        "WHERE r.checkpoint_id = ? ORDER BY r.rank ASC",
        (target["checkpoint_id"],),
    ).fetchall()

    prior_map: dict[int, sqlite3.Row] = {}
    if baseline is not None:
        prior_map = {
            r["program_id"]: r
            for r in con.execute(
                "SELECT program_id, rank, rating_num FROM rankings WHERE checkpoint_id = ?",
                (baseline["checkpoint_id"],),
            ).fetchall()
        }

    if baseline is None:
        delta_note = "Earlier rankings are unavailable." if target["code"] != "PRESEASON" else ""
    elif target["code"] == "FINAL" and baseline["code"] != "PRESEASON":
        delta_note = f'Change since {baseline["label"]}, the first archived ranking.'
    else:
        delta_note = f'Change since {baseline["label"]}.'

    conf_pages = _conference_paths_for_sport(con, sport_slug)
    tr_html = []
    for r in rows:
        prev = prior_map.get(r["program_id"])
        rc = ratc = _MISSING
        if prev is not None:
            r_delta = int(prev["rank"]) - int(r["rank"])
            rat_delta = float(r["rating_num"]) - float(prev["rating_num"])
            rc = f"{r_delta:+d}" if r_delta else "0"
            ratc = f"{rat_delta:+.2f}" if abs(rat_delta) >= 0.005 else "0.00"
        conf = r["conference"] or ""
        if conf and conf != _MISSING:
            c_path = f"/{sport_slug}/conferences/{_conference_slug(conf)}/{year}"
            conf_html = f'<a href="{escape(c_path)}">{escape(conf)}</a>' if c_path in conf_pages else escape(conf)
        else:
            conf_html = _MISSING
        tr_html.append(
            f'<tr><td class="num">{r["rank"]}</td>'
            f'<th scope="row">{_team_label(r["canonical_name"], f"/{sport_slug}/teams/{r["slug"]}/{year}", r["mark_path"])}</th>'
            f'<td class="num">{rc}</td><td class="num">{ratc}</td>'
            f'<td class="num">{float(r["rating_num"]):.2f}</td>'
            f'<td class="num">{escape(r["record_text"])}</td>'
            f"<td>{conf_html}</td></tr>"
        )

    rankings_table = (
        (f"<p>{escape(delta_note)}</p>" if delta_note else "")
        + '<div class="table-scroll" role="region" aria-labelledby="cors-ranking" tabindex="0">'
        '<table aria-labelledby="cors-ranking"><thead><tr>'
        '<th scope="col" class="num">Rank</th><th scope="col">Team</th>'
        '<th scope="col" class="num">Rank change</th><th scope="col" class="num">Rating change</th>'
        '<th scope="col" class="num">CORS rating</th><th scope="col" class="num">Record</th>'
        '<th scope="col">Conference</th>'
        f'</tr></thead><tbody>{"".join(tr_html)}</tbody></table></div>'
    )

    # Checkpoint navigation bar
    cp_nav_items = "".join(
        f'<li><a href="/{sport_slug}/{year}/{c["code"].lower()}"'
        + (' aria-current="page"' if (not is_landing and c["checkpoint_id"] == target["checkpoint_id"]) else "")
        + f'>{escape(c["label"])}</a></li>'
        for c in cps
    )
    cp_nav = f'<nav aria-label="Ranking checkpoints"><ul class="week-links">{cp_nav_items}</ul></nav>'

    # Weekly games/spreads nav
    available_weeks = {
        int(w[0])
        for w in con.execute("SELECT DISTINCT week FROM games WHERE season_id = ?", (season["season_id"],)).fetchall()
    }
    matchup_links = []
    if target["code"] != "FINAL":
        wk = int(target["code"][1:]) if target["code"].startswith("W") else -1
        if wk >= 0 and wk in available_weeks:
            matchup_links.append((f"/{sport_slug}/{year}/w{wk}/results", f"Week {wk} scores"))
        if (wk + 1) in available_weeks:
            matchup_links.append((f"/{sport_slug}/{year}/w{wk + 1}/schedule", f"Week {wk + 1} schedule"))
            matchup_links.append((f"/{sport_slug}/{year}/w{wk + 1}/spreads", f"Week {wk + 1} spreads"))
    else:
        if available_weeks:
            matchup_links.append((f"/{sport_slug}/{year}/results", "All season scores"))
    if sport_slug == "cfb":
        has_perf = con.execute(
            "SELECT 1 FROM performance_seasons WHERE sport_id = ? AND year = ?",
            (season["sport_id"], year),
        ).fetchone()
        if has_perf:
            matchup_links.append((f"/cfb/performance/{year}", f"{year} model performance"))

    matchup_nav = (
        '<nav aria-label="Games and forecasts"><ul class="week-links">'
        + "".join(f'<li><a href="{href}">{escape(lbl)}</a></li>' for href, lbl in matchup_links)
        + "</ul></nav>"
        if matchup_links else ""
    )

    # Upsets
    upsets_html = ""
    if target["code"] == "FINAL" or target["code"].startswith("W"):
        ranked_slugs = {r["canonical_name"]: r["slug"] for r in rows}
        marks = {
            m["school_name"]: m["mark_path"]
            for m in con.execute(
                "SELECT school_name, mark_path FROM school_marks WHERE sport_id = ?", (season["sport_id"],)
            ).fetchall()
        }
        if target["code"] == "FINAL":
            upset_games = con.execute(
                "SELECT * FROM games WHERE season_id = ? ORDER BY week ASC, game_id ASC",
                (season["season_id"],),
            ).fetchall()
        else:
            wk = int(target["code"][1:])
            upset_games = con.execute(
                "SELECT * FROM games WHERE season_id = ? AND week = ? ORDER BY game_id ASC",
                (season["season_id"], wk),
            ).fetchall()
        upsets_html = _upsets_html(sport_slug, year, upset_games, ranked_slugs, marks)

    rank_heading = f'{target["label"]} CORS rankings' if is_landing else "CORS rankings"
    standings_block = (
        f'<div class="season-standings">'
        f'<section aria-labelledby="cors-ranking"><h2 id="cors-ranking">{escape(rank_heading)}</h2>{rankings_table}</section>'
        f"{upsets_html}</div>"
    )

    # Compact weekly links section on landing page
    week_archive_html = ""
    if is_landing and available_weeks:
        wk_items = "".join(
            f'<li><strong>Week {w}:</strong> '
            f'<a href="/{sport_slug}/{year}/w{w}/results">Scores</a> · '
            f'<a href="/{sport_slug}/{year}/w{w}/spreads">Spreads</a> · '
            f'<a href="/{sport_slug}/{year}/w{w}/schedule">Schedule</a></li>'
            for w in sorted(available_weeks)
        )
        week_archive_html = f'<section id="season-archive"><h2>Weekly sheets</h2><ul class="week-links">{wk_items}</ul></section>'

    jump_links = []
    if upsets_html:
        jump_links.append('<a href="#cors-upsets">Upsets</a>')
    if week_archive_html:
        jump_links.append('<a href="#season-archive">Season archive</a>')
    exc_path = f"/{sport_slug}/{year}/excitement"
    if season["has_excitement"] and con.execute("SELECT 1 FROM pages WHERE canonical_path = ?", (exc_path,)).fetchone():
        jump_links.append(f'<a href="{exc_path}">Game excitement</a>')
    jump_html = f'<p>{" · ".join(jump_links)}</p>' if jump_links else ""

    stamp = _updated_time(season["updated_at"]) if season["updated_at"] else ""
    meta_html = f'<div id="season-notes"><p>{stamp}</p></div>' if stamp else ""
    if is_landing:
        title = f'{season["label"]} {season["sport_name"].lower()} | SportsRank'
        heading = f'{season["label"]} {season["sport_name"].lower()}'
        related = None
    else:
        title = f'{season["label"]} {sport_slug.upper()} · {target["label"]} | SportsRank'
        heading = f'{season["label"]} {sport_slug.upper()} · {target["label"]}'
        related = [(f"/{sport_slug}/{year}", f'{season["label"]} season')]

    body = f"<header><h1>{escape(heading)}</h1>{meta_html}</header>{cp_nav}{matchup_nav}{jump_html}{standings_block}{week_archive_html}"
    html = _layout(
        con,
        title,
        body,
        active=sport_slug,
        body_class="record-book season-page",
        related_nav=related,
    )
    return html, bool(season["is_final"])


def render_games_view(
    con: sqlite3.Connection,
    sport_slug: str,
    year: int,
    view: str,
    week: int | None = None,
) -> tuple[str | None, bool]:
    """Render results, schedule, or spreads for one week or a full season."""
    season = con.execute(
        "SELECT sn.*, s.sport_id, s.name AS sport_name FROM seasons sn "
        "JOIN sports s ON s.sport_id = sn.sport_id WHERE s.slug = ? AND sn.year = ?",
        (sport_slug, year),
    ).fetchone()
    if season is None:
        return None, False

    if week is not None:
        games = con.execute(
            "SELECT * FROM games WHERE season_id = ? AND week = ? ORDER BY game_id ASC",
            (season["season_id"], week),
        ).fetchall()
    else:
        games = con.execute(
            "SELECT * FROM games WHERE season_id = ? ORDER BY week ASC, game_id ASC",
            (season["season_id"],),
        ).fetchall()

    ranked_slugs = {
        r["canonical_name"]: r["slug"]
        for r in con.execute(
            "SELECT DISTINCT p.canonical_name, p.slug FROM rankings r "
            "JOIN checkpoints cp ON cp.checkpoint_id = r.checkpoint_id "
            "JOIN programs p ON p.program_id = r.program_id WHERE cp.season_id = ?",
            (season["season_id"],),
        ).fetchall()
    }
    marks = {
        m["school_name"]: m["mark_path"]
        for m in con.execute(
            "SELECT school_name, mark_path FROM school_marks WHERE sport_id = ?", (season["sport_id"],)
        ).fetchall()
    }

    def team_cell(name: str) -> str:
        href = f"/{sport_slug}/teams/{ranked_slugs[name]}/{year}" if name in ranked_slugs else None
        return _team_label(name, href, marks.get(name))

    show_excitement = view in ("spreads", "results") and (
        bool(season["has_excitement"]) or any(g["bev_html"] or g["aev_html"] for g in games)
    )
    has_estimate = False
    has_picks = view == "spreads" and any(g["pick_path"] for g in games)

    rows_html = []
    for g in games:
        home = team_cell(g["home_school"])
        away = team_cell(g["away_school"])
        site = "Neutral" if g["neutral_site"] else "Home"
        hs = str(g["home_score"]) if g["home_score"] is not None else _MISSING
        as_ = str(g["away_score"]) if g["away_score"] is not None else _MISSING
        pick = _MISSING
        margin_s = _MISSING
        chance_s = _MISSING
        if g["home_handicap"] is not None:
            h = Decimal(g["home_handicap"])
            if h == 0:
                pick = "Pick’em"
                margin_s = "0.00"
                chance_s = _format_win_chance(Decimal(0), sport_slug=sport_slug)
            elif h < 0:
                pick = escape(g["home_school"])
                margin_s = f"{abs(h):.2f}"
                chance_s = _format_win_chance(h, sport_slug=sport_slug)
            else:
                pick = escape(g["away_school"])
                margin_s = f"{abs(h):.2f}"
                chance_s = _format_win_chance(h.copy_negate(), sport_slug=sport_slug)

        if view == "schedule":
            rows_html.append(f'<tr><td class="num">{g["week"]}</td><td>{away}</td><td>{home}</td><td>{site}</td></tr>')
        elif view == "spreads":
            score_s = f"{g['away_score']}–{g['home_score']}" if (g["home_score"] is not None and g["away_score"] is not None) else _MISSING
            pick_cell = f'<a class="share-spread" href="{escape(g["pick_path"])}">{pick}</a>' if g["pick_path"] else pick
            extra = f'<td class="num">{g["bev_html"] or "&mdash;"}</td>' if show_excitement else ""
            rows_html.append(
                f'<tr><td class="num">{g["week"]}</td><td>{away}</td><td>{home}</td>'
                f'<td>{pick_cell}</td><td class="num">{margin_s}</td>'
                f'<td class="num">{chance_s}</td><td class="num">{score_s}</td>{extra}</tr>'
            )
        else:  # results
            extra = ""
            if show_excitement:
                if g["aev_estimated"]:
                    has_estimate = True
                extra = f'<td class="num">{g["bev_html"] or "&mdash;"}</td><td class="num">{g["aev_html"] or "&mdash;"}</td>'
            rows_html.append(
                f'<tr><td class="num">{g["week"]}</td><td>{away}</td><td class="num">{as_}</td>'
                f'<td>{home}</td><td class="num">{hs}</td><td>{site}</td>{extra}</tr>'
            )

    if view == "schedule":
        headers = '<th scope="col" class="num">Week</th><th scope="col">Away</th><th scope="col">Home</th><th scope="col">Site</th>'
        kind_label = "schedule"
    elif view == "spreads":
        headers = (
            '<th scope="col" class="num">Week</th><th scope="col">Away</th><th scope="col">Home</th>'
            '<th scope="col">CORS pick</th><th scope="col" class="num">Margin</th>'
            '<th scope="col" class="num">Win chance</th><th scope="col" class="num">Score (A–H)</th>'
            + ('<th scope="col"><abbr title="Before Excitement Value">BEV</abbr></th>' if show_excitement else "")
        )
        kind_label = "CORS spreads"
    else:
        headers = (
            '<th scope="col" class="num">Week</th><th scope="col">Away</th><th scope="col" class="num">Score</th>'
            '<th scope="col">Home</th><th scope="col" class="num">Score</th><th scope="col">Site</th>'
            + (
                '<th scope="col"><abbr title="Before Excitement Value">BEV</abbr></th>'
                '<th scope="col"><abbr title="After Excitement Value">AEV</abbr></th>'
                if show_excitement else ""
            )
        )
        kind_label = "scores"

    scope_label = f'Week {week} {kind_label}' if week is not None else f'All season {kind_label}'
    heading = f'{season["label"]} {sport_slug.upper()} · {scope_label}'
    intro = ""
    if has_picks:
        intro += '<p class="spread-hint">Select a CORS pick to open its shareable card.</p>'
    if show_excitement:
        exc_path = f"/{sport_slug}/{year}/excitement"
        exc_link = (
            f' <a href="{exc_path}">{year} game excitement</a>'
            if con.execute("SELECT 1 FROM pages WHERE canonical_path = ?", (exc_path,)).fetchone()
            else ""
        )
        after = "" if view == "spreads" else " AEV measures game drama."
        if has_estimate:
            after += " " + _ESTIMATE_NOTE
        intro += f"<p>BEV rates pregame appeal on a 0&ndash;100 scale.{after}{exc_link}</p>"

    if rows_html:
        content = f'{intro}<div class="table-scroll"><table><thead><tr>{headers}</tr></thead><tbody>{"".join(rows_html)}</tbody></table></div>'
    else:
        content = "<p>No games recorded for this selection.</p>"

    related = [(f"/{sport_slug}/{year}", f'{season["label"]} season')]
    if week is not None:
        related.append((f"/{sport_slug}/{year}/w{week}", f"Week {week} rankings"))
        for alt_view, alt_lbl in (("results", "Scores"), ("spreads", "Spreads"), ("schedule", "Schedule")):
            if alt_view != view:
                related.append((f"/{sport_slug}/{year}/w{week}/{alt_view}", f"Week {week} {alt_lbl.lower()}"))

    return (
        _layout(con, f"{heading} | SportsRank", f"<h1>{escape(heading)}</h1>{content}", active=sport_slug, related_nav=related),
        bool(season["is_final"]),
    )


def render_honors(con: sqlite3.Connection, sport_slug: str, kind: str) -> str | None:
    """Render champions (rank=1) or last-place finishers (max rank) from FINAL rankings."""
    sport = con.execute("SELECT * FROM sports WHERE slug = ?", (sport_slug,)).fetchone()
    if sport is None:
        return None
    if kind == "champions":
        title = "National champions" if sport_slug == "cfb" else "Season champions"
        rows = con.execute(
            """
            SELECT sn.year, sn.label AS season_label, p.slug, p.canonical_name, p.mark_path,
                   r.conference, r.record_text, r.rating
            FROM rankings r
            JOIN checkpoints cp ON cp.checkpoint_id = r.checkpoint_id AND cp.code = 'FINAL'
            JOIN seasons sn ON sn.season_id = cp.season_id AND sn.sport_id = ?
            JOIN programs p ON p.program_id = r.program_id
            WHERE r.rank = 1
            ORDER BY sn.year DESC
            """,
            (sport["sport_id"],),
        ).fetchall()
    else:
        title = "Last-place finishers"
        rows = con.execute(
            """
            SELECT sn.year, sn.label AS season_label, p.slug, p.canonical_name, p.mark_path,
                   r.conference, r.record_text, r.rating
            FROM rankings r
            JOIN checkpoints cp ON cp.checkpoint_id = r.checkpoint_id AND cp.code = 'FINAL'
            JOIN seasons sn ON sn.season_id = cp.season_id AND sn.sport_id = ?
            JOIN programs p ON p.program_id = r.program_id
            WHERE r.rank = (SELECT MAX(r2.rank) FROM rankings r2 WHERE r2.checkpoint_id = r.checkpoint_id)
            ORDER BY sn.year DESC
            """,
            (sport["sport_id"],),
        ).fetchall()

    conf_pages = _conference_paths_for_sport(con, sport_slug)
    tr_html = []
    for r in rows:
        conf = r["conference"] or ""
        if conf and conf != _MISSING:
            c_path = f"/{sport_slug}/conferences/{_conference_slug(conf)}/{r['year']}"
            conf_html = f'<a href="{escape(c_path)}">{escape(conf)}</a>' if c_path in conf_pages else escape(conf)
        else:
            conf_html = _MISSING
        tr_html.append(
            f'<tr><th scope="row"><a href="/{sport_slug}/{r["year"]}">{escape(r["season_label"])}</a></th>'
            f'<td>{_team_label(r["canonical_name"], f"/{sport_slug}/teams/{r["slug"]}/{r["year"]}", r["mark_path"])}</td>'
            f"<td>{conf_html}</td>"
            f'<td class="num">{escape(r["record_text"])}</td>'
            f'<td class="num">{escape(r["rating"])}</td></tr>'
        )
    table = (
        '<div class="table-scroll"><table><thead><tr>'
        '<th scope="col">Season</th><th scope="col">Team</th><th scope="col">Conference</th>'
        '<th scope="col" class="num">Record</th><th scope="col" class="num">CORS rating</th>'
        f'</tr></thead><tbody>{"".join(tr_html)}</tbody></table></div>'
    )
    other = ("last-place", "Last-place finishers") if kind == "champions" else ("champions", "National champions")
    related = [(f"/{sport_slug}", sport["name"]), (f"/{sport_slug}/history/{other[0]}", other[1])]
    conf_honors = f"/{sport_slug}/history/conference-honors"
    if con.execute("SELECT 1 FROM pages WHERE canonical_path = ?", (conf_honors,)).fetchone():
        related.append((conf_honors, "Best and worst conferences by year"))
    return _layout(con, f"{title} | SportsRank", f"<h1>{escape(title)}</h1>{table}", active=sport_slug, related_nav=related)


def _fmt_pct(value) -> str:
    if value in (None, ""):
        return _MISSING
    return f"{float(value) * 100:.1f}%"


def _fmt_num(value) -> str:
    if value in (None, ""):
        return _MISSING
    return f"{float(value):.2f}"


def render_performance_index(con: sqlite3.Connection) -> str | None:
    sport = con.execute("SELECT * FROM sports WHERE slug = 'cfb'").fetchone()
    if sport is None:
        return None
    rows = con.execute(
        "SELECT year, summary_json FROM performance_seasons WHERE sport_id = ? ORDER BY year DESC",
        (sport["sport_id"],),
    ).fetchall()
    if not rows:
        return None

    tot_games = tot_su_w = tot_su_l = tot_su_t = 0
    tot_cov = tot_nocov = tot_push = 0
    tot_mkt_games = tot_mkt_w = tot_mkt_l = tot_mkt_p = tot_mkt_np = tot_missing_mkt = 0
    weighted_mae = weighted_sq = 0.0
    paired_cors_mae_sum = paired_mkt_mae_sum = 0.0

    tr_html = []
    for r in rows:
        s = json.loads(r["summary_json"])
        g = int(s.get("game_count", 0))
        su_w = int(s.get("straight_up_wins", 0))
        su_l = int(s.get("straight_up_losses", 0))
        su_t = int(s.get("straight_up_ties", 0))
        cov = int(s.get("covers", 0))
        nocov = int(s.get("no_covers", 0))
        push = int(s.get("pushes", 0))
        mg = int(s.get("market_games", 0))
        mw = int(s.get("market_wins", 0))
        ml = int(s.get("market_losses", 0))
        mp = int(s.get("market_pushes", 0))
        mnp = int(s.get("market_no_picks", 0))

        tot_games += g
        tot_su_w += su_w
        tot_su_l += su_l
        tot_su_t += su_t
        tot_cov += cov
        tot_nocov += nocov
        tot_push += push
        tot_mkt_games += mg
        tot_mkt_w += mw
        tot_mkt_l += ml
        tot_mkt_p += mp
        tot_mkt_np += mnp
        tot_missing_mkt += int(s.get("missing_market_lines", 0))
        if s.get("mae") is not None:
            weighted_mae += float(s["mae"]) * g
        if s.get("rmse") is not None:
            weighted_sq += (float(s["rmse"]) ** 2) * g
        if mg and s.get("paired_cors_mae") is not None:
            paired_cors_mae_sum += float(s["paired_cors_mae"]) * mg
        if mg and s.get("market_mae") is not None:
            paired_mkt_mae_sum += float(s["market_mae"]) * mg

        tr_html.append(
            f'<tr><td><a href="/cfb/performance/{r["year"]}">{r["year"]}</a></td>'
            f"<td>{g}</td><td>{su_w}</td><td>{su_l}</td><td>{su_t}</td>"
            f"<td>{cov}</td><td>{nocov}</td><td>{push}</td>"
            f'<td>{_fmt_num(s.get("mae"))}</td><td>{_fmt_num(s.get("rmse"))}</td>'
            f"<td>{mg}</td><td>{mw}</td><td>{ml}</td>"
            f'<td>{_fmt_pct(s.get("market_ats_percentage"))}</td>'
            f'<td>{_fmt_num(s.get("paired_cors_mae"))}</td><td>{_fmt_num(s.get("market_mae"))}</td></tr>'
        )

    su_pct = (100.0 * tot_su_w / (tot_su_w + tot_su_l)) if (tot_su_w + tot_su_l) else 0.0
    cov_pct = (100.0 * tot_cov / (tot_cov + tot_nocov)) if (tot_cov + tot_nocov) else 0.0
    mae_all = (weighted_mae / tot_games) if tot_games else 0.0
    rmse_all = ((weighted_sq / tot_games) ** 0.5) if tot_games else 0.0
    mkt_pct = (100.0 * tot_mkt_w / (tot_mkt_w + tot_mkt_l)) if (tot_mkt_w + tot_mkt_l) else 0.0
    p_cors_mae = (paired_cors_mae_sum / tot_mkt_games) if tot_mkt_games else 0.0
    p_mkt_mae = (paired_mkt_mae_sum / tot_mkt_games) if tot_mkt_games else 0.0

    latest_year = rows[0]["year"]
    body = (
        '<div class="sr-report sr-report-index">'
        "<h1>CORS model performance</h1>"
        '<div class="sr-report-comparison"><section><h2>Forecast accuracy</h2>'
        f'<p class="metrics"><strong>{tot_games:,} FBS matchups</strong> · '
        f"Winner accuracy {su_pct:.1f}% ({tot_su_w:,}–{tot_su_l:,}; {tot_su_t:,} ties)<br>"
        f"CORS line coverage {cov_pct:.1f}% ({tot_cov:,}–{tot_nocov:,}; {tot_push:,} pushes)<br>"
        f"MAE {mae_all:.2f} points · RMSE {rmse_all:.2f} points</p></section>"
        "<section><h2>Market comparison</h2>"
        f'<p class="metrics">Market ATS {mkt_pct:.1f}% ({tot_mkt_w:,}–{tot_mkt_l:,}; {tot_mkt_p:,} pushes; {tot_mkt_np:,} no picks)<br>'
        f"{tot_mkt_games:,} paired games · CORS MAE {p_cors_mae:.2f} / market MAE {p_mkt_mae:.2f} points<br>"
        f"{tot_missing_mkt:,} missing lines</p></section></div>"
        f'<p><a href="/cfb/performance/{latest_year}">Current forecasts and results</a> · '
        '<a href="/cfb/performance/seasons.csv">Seasons (CSV)</a> · '
        '<a href="/cfb/performance/market-lines.json">Market lines (JSON)</a></p>'
        f"<h2>Season summaries ({len(rows)} seasons)</h2>"
        '<div class="scroll"><table><thead><tr>'
        "<th>Season</th><th>Games</th><th>Wins</th><th>Losses</th><th>Ties</th>"
        "<th>Covers</th><th>No covers</th><th>Pushes</th><th>MAE</th><th>RMSE</th>"
        "<th>Paired</th><th>Mkt W</th><th>Mkt L</th><th>ATS %</th><th>CORS MAE</th><th>Mkt MAE</th>"
        f'</tr></thead><tbody>{"".join(tr_html)}</tbody></table></div></div>'
    )
    return _layout(
        con,
        "CORS model performance | SportsRank",
        body,
        active="performance",
        extra_css=("/cfb/assets/report-pages.css",),
    )


def render_performance_season(con: sqlite3.Connection, year: int) -> tuple[str | None, bool]:
    sport = con.execute("SELECT * FROM sports WHERE slug = 'cfb'").fetchone()
    if sport is None:
        return None, False
    row = con.execute(
        "SELECT report_json_zlib FROM performance_seasons WHERE sport_id = ? AND year = ?",
        (sport["sport_id"], year),
    ).fetchone()
    if row is None:
        return None, False

    season = con.execute(
        "SELECT is_final FROM seasons WHERE sport_id = ? AND year = ?", (sport["sport_id"], year)
    ).fetchone()
    is_final = bool(season["is_final"]) if season else True

    report = json.loads(zlib.decompress(row["report_json_zlib"]).decode("utf-8"))
    s = report.get("summary", {})
    weekly = report.get("weekly", [])
    games = report.get("games", [])
    upcoming = report.get("upcoming", [])

    upcoming_html = ""
    if upcoming:
        up_rows = "".join(
            f'<tr><td>W{u.get("week", "")}</td>'
            f'<td>{escape(str(u.get("away", "")))}</td>'
            f'<td>{escape(str(u.get("home", "")))}</td>'
            f'<td>{escape(str(u.get("home_handicap", _MISSING)))}</td>'
            f'<td>{escape(str(u.get("market_provider") or _MISSING))}</td>'
            f'<td>{escape(str(u.get("market_home_handicap") if u.get("market_home_handicap") is not None else _MISSING))}</td>'
            f'<td>{escape(str(u.get("market_pick") or _MISSING))}</td>'
            f'<td>{escape(str(u.get("status") or "Pending result"))}</td></tr>'
            for u in upcoming
        )
        upcoming_html = (
            f'<h2>Ungraded forecasts ({len(upcoming)})</h2><div class="scroll"><table><thead><tr>'
            "<th>Week</th><th>Away</th><th>Home</th><th>CORS spread (home)</th>"
            "<th>Provider</th><th>Provider spread (home)</th><th>Market pick</th><th>Status</th>"
            f"</tr></thead><tbody>{up_rows}</tbody></table></div>"
        )

    weekly_rows = "".join(
        f'<tr><td>Week {w["week"]}</td><td>{w.get("game_count", 0)}</td>'
        f'<td>{w.get("straight_up_wins", 0)}–{w.get("straight_up_losses", 0)}</td>'
        f'<td>{w.get("covers", 0)}–{w.get("no_covers", 0)}</td>'
        f'<td>{_fmt_num(w.get("mae"))}</td><td>{_fmt_num(w.get("rmse"))}</td>'
        f'<td>{w.get("market_wins", 0)}–{w.get("market_losses", 0)}</td>'
        f'<td>{_fmt_pct(w.get("market_ats_percentage"))}</td></tr>'
        for w in weekly
    )

    game_rows = "".join(
        f'<tr><td>W{g["week"]}</td><td>{escape(str(g.get("away", "")))}</td>'
        f'<td>{escape(str(g.get("home", "")))}</td>'
        f'<td>{escape(str(g.get("home_handicap", _MISSING)))}</td>'
        f'<td>{escape(str(g.get("market_home_handicap") or _MISSING))}</td>'
        f'<td>{g.get("away_score", _MISSING)}–{g.get("home_score", _MISSING)}</td>'
        f'<td>{escape(str(g.get("straight_up") or _MISSING))}</td>'
        f'<td>{escape(str(g.get("market_ats") or _MISSING))}</td></tr>'
        for g in games
    )

    body = (
        '<div class="sr-report sr-report-matchups">'
        f"<h1>{year} CORS model performance</h1>"
        f'<p class="metrics"><strong>{s.get("game_count", 0)} FBS matchups</strong> · '
        f'Winner accuracy {_fmt_pct(s.get("straight_up_percentage"))} '
        f'({s.get("straight_up_wins", 0)}–{s.get("straight_up_losses", 0)}; {s.get("straight_up_ties", 0)} ties)<br>'
        f'MAE {_fmt_num(s.get("mae"))} · RMSE {_fmt_num(s.get("rmse"))}'
        + (
            f'<br>Market ATS {_fmt_pct(s.get("market_ats_percentage"))} '
            f'({s.get("market_wins", 0)}–{s.get("market_losses", 0)}; {s.get("market_games", 0)} paired games)'
            if s.get("market_games") else ""
        )
        + "</p>"
        f'<p><a href="/cfb/performance/{year}/report.json">Report (JSON)</a> · '
        f'<a href="/cfb/performance/{year}/games.csv">Games (CSV)</a> · '
        f'<a href="/cfb/performance/{year}/weekly.csv">Weekly (CSV)</a></p>'
        f"{upcoming_html}"
        '<h2>By week</h2><div class="scroll"><table><thead><tr>'
        "<th>Week</th><th>Games</th><th>Winner W–L</th><th>Cover W–L</th>"
        "<th>MAE</th><th>RMSE</th><th>Market ATS</th><th>ATS %</th>"
        f'</tr></thead><tbody>{weekly_rows}</tbody></table></div>'
        f'<h2>Matchups ({len(games)})</h2><div class="scroll"><table><thead><tr>'
        "<th>Week</th><th>Away</th><th>Home</th><th>CORS line</th><th>Market line</th>"
        "<th>Score (A–H)</th><th>Winner</th><th>Market ATS</th>"
        f'</tr></thead><tbody>{game_rows}</tbody></table></div></div>'
    )
    related = [("/cfb/performance", "All seasons"), (f"/cfb/{year}", f"{year} standings")]
    return (
        _layout(
            con,
            f"{year} CORS model performance | SportsRank",
            body,
            active="performance",
            extra_css=("/cfb/assets/report-pages.css",),
            related_nav=related,
        ),
        is_final,
    )


def render_edition(con: sqlite3.Connection, sport_slug: str, edition_slug: str) -> str | None:
    row = con.execute(
        "SELECT e.*, s.name AS sport_name FROM editions e "
        "JOIN sports s ON s.sport_id = e.sport_id WHERE s.slug = ? AND e.slug = ?",
        (sport_slug, edition_slug),
    ).fetchone()
    if row is None:
        return None
    rankings = json.loads(zlib.decompress(row["rankings_json_zlib"]).decode("utf-8"))
    forecasts = json.loads(zlib.decompress(row["forecasts_json_zlib"]).decode("utf-8"))
    rank_rows = "".join(
        f'<tr><td class="num">{r["rank"]}</td><th scope="row">{escape(str(r["team"]))}</th>'
        f'<td>{escape(str(r["record"]))}</td><td class="num">{escape(str(r["rating"]))}</td></tr>'
        for r in rankings
    )
    forecast_html = ""
    if forecasts:
        f_rows = "".join(
            f'<tr><td>{escape(str(f["tipoff"]))}</td><td>{escape(str(f["away"]))}</td>'
            f'<td>{escape(str(f["home"]))}</td><td>{escape(str(f["pick"]))}</td>'
            f'<td class="num">{escape(str(f["margin"]))}</td></tr>'
            for f in forecasts
        )
        prep = f'<p>{escape(row["prepared_at"])}</p>' if row["prepared_at"] else ""
        forecast_html = (
            f'<section><h2>Game forecasts</h2>{prep}<div class="table-scroll"><table>'
            "<thead><tr><th>Tipoff (Eastern)</th><th>Away</th><th>Home</th><th>CORS pick</th><th>Margin</th></tr></thead>"
            f"<tbody>{f_rows}</tbody></table></div></section>"
        )
    body = (
        f'<h1>{escape(row["sport_name"])}</h1>'
        f'<p class="edition">{escape(row["cutoff_utc"])}</p>'
        '<section><h2>CORS rankings</h2><div class="table-scroll"><table>'
        '<thead><tr><th scope="col" class="num">Rank</th><th scope="col">Team</th>'
        '<th scope="col">Record</th><th scope="col" class="num">CORS rating</th></tr></thead>'
        f"<tbody>{rank_rows}</tbody></table></div></section>{forecast_html}"
    )
    return _layout(
        con,
        row["title"],
        body,
        active=sport_slug,
        related_nav=[(f"/{sport_slug}", f'Current {row["sport_name"].lower()}')],
    )


def render_not_found(con: sqlite3.Connection) -> str:
    cfb = con.execute("SELECT current_season FROM sports WHERE slug = 'cfb'").fetchone()
    year = cfb["current_season"] if cfb else 2026
    body = (
        '<div class="missing-page"><h1>Page not found</h1>'
        '<ul class="recovery-links">'
        f'<li><a href="/cfb/{year}">{year} college football standings</a></li>'
        '<li><a href="/cfb/teams">Find a team</a></li>'
        '<li><a href="/cfb">Season archive</a></li>'
        '<li><a href="/cbb">Men’s college basketball</a></li>'
        "</ul></div>"
    )
    return _layout(con, "404 · Page not found | SportsRank", body)
