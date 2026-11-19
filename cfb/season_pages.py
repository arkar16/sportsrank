"""Render season summaries and permanent checkpoint pages from the CORS archive."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from html import escape
from pathlib import Path
import re
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Tag
from .archive_presentation import updated_time
from .reference_archive import season_team_routes
from .reference_markup import school_label, source_bytes

if TYPE_CHECKING:
    from .reference_archive import Game, Season, Standing


_STYLESHEET = "/cfb/assets/record-book.css"
_WEEKLY_LABELS = {
    "cors": "Rankings", "records": "Records", "results": "Scores", "slate": "Schedule",
    "spread": "Spreads", "spread_results": "Forecast results",
}
_WEEKLY_ORDER = tuple(_WEEKLY_LABELS)


def _label(checkpoint: str) -> str:
    if checkpoint.startswith("W"):
        return f"Week {int(checkpoint[1:])}"
    return checkpoint.title()


def _checkpoint_order(checkpoint: str) -> tuple[int, int]:
    if checkpoint == "PRESEASON":
        return (0, 0)
    if checkpoint == "FINAL":
        return (2, 0)
    return (1, int(checkpoint[1:]))


def _anchor(href: str, label: str) -> str:
    return f'<a href="{escape(href)}">{escape(label)}</a>'


def _archive_link(site: Path, directory: Path, href: str, label: str, original: str, week: str = "") -> str:
    target = urlsplit(href)
    attrs = f' data-archive-label="{escape(original)}"' if original != label else ""
    if week and original != label:
        attrs += f' aria-label="{escape(label)}, Week {week}"'
    if not target.scheme and not target.netloc and target.path:
        path = unquote(target.path)
        destination = site / path.lstrip("/") if path.startswith("/") else directory / path
        if not (destination.is_file() or (destination / "index.html").is_file()):
            return (f'<span data-unavailable-href="{escape(href)}" data-archive-label="{escape(original)}">'
                    f'{escape(label)} (unavailable)</span>')
    return f'<a href="{escape(href)}"{attrs}>{escape(label)}</a>'


def _archive_navigation(site: Path, directory: Path, source: Tag, year: int) -> str:
    pattern = re.compile(fr"{year}_W(\d+)_FBS_(\w+)\.html$")
    general = []
    weekly = defaultdict(list)
    seen = set()
    for entry in source.select("a[href], [data-unavailable-href]"):
        href = entry.get("href", entry.get("data-unavailable-href", ""))
        label = entry.get("data-archive-label", entry.get_text(" ", strip=True))
        if (href, label) in seen:
            continue
        seen.add((href, label))
        match = pattern.search(urlsplit(href).path)
        if match:
            week = match[1]
            kind = match[2]
            visible = label
            if re.fullmatch(rf"(?:Link to {year} W{week} FBS CFB |W{week} )(?:rankings?|records|results|slate|spread(?: results)?)", label):
                visible = _WEEKLY_LABELS.get(kind, label)
                if kind == "spread" and "reconstructed/" in href:
                    visible = "Reconstructed spreads"
            link = f"<li>{_archive_link(site, directory, href, visible, label, week)}</li>"
            priority = _WEEKLY_ORDER.index(kind) if kind in _WEEKLY_ORDER else len(_WEEKLY_ORDER)
            weekly[int(match[1])].append((priority, link))
        else:
            visible = {
                f"Link to {year} FBS CFB final rankings": "Final rankings", "Final": "Final rankings",
                "Preseason ranking": "Preseason rankings", "CORS home": "College football",
            }.get(label, label)
            general.append(f"<li>{_archive_link(site, directory, href, visible, label)}</li>")
    if not (general or weekly):
        return ""
    groups = [f'<ul class="week-links">{"".join(general)}</ul>'] if general else []
    for week, entries in sorted(weekly.items()):
        links = "".join(link for _, link in sorted(entries, key=lambda entry: entry[0]))
        groups.append(f'<h3>Week {week}</h3><ul class="week-links">{links}</ul>')
    return '<section id="season-archive"><h2>Season archive</h2>' + "".join(groups) + "</section>"


def _checkpoint_navigation(checkpoints: list[str], current: str | None = None) -> str:
    prefix = "checkpoints/" if current is None else ""
    links = []
    for key in checkpoints:
        selected = ' aria-current="page"' if key == current else ""
        links.append(f'<li><a href="{prefix}{key}.html"{selected}>{_label(key)}</a></li>')
    return '<nav aria-label="Ranking checkpoints"><ul class="week-links">' + "".join(links) + "</ul></nav>"


def _matchup_navigation(directory: Path, year: int, checkpoint: str, prefix: str) -> str:
    if checkpoint == "FINAL":
        return ""
    week = int(checkpoint[1:]) if checkpoint.startswith("W") else -1
    following = f"{year}_W{week + 1}_FBS"
    candidates = []
    if week >= 0:
        candidates.append((f"data/results/weekly_results/{year}_W{week}_FBS_results.html", f"Week {week} scores"))
    candidates.append((f"data/slate/weekly_slate/{following}_slate.html", f"Week {week + 1} schedule"))
    forecast = f"spread/{following}_spread.html"
    label = f"Week {week + 1} spreads"
    if not (directory / forecast).is_file():
        forecast = f"reconstructed/{following}_spread.html"
        label = f"Week {week + 1} reconstructed spreads"
    candidates.append((forecast, label))
    links = [
        f"<li>{_anchor(prefix + path, label)}</li>"
        for path, label in candidates
        if (directory / path).is_file()
    ]
    if not links:
        return ""
    return '<nav aria-label="Games and forecasts"><ul class="week-links">' + "".join(links) + "</ul></nav>"


def _rankings(
    rows: list[Standing],
    comparison: list[Standing],
    baseline: str | None,
    checkpoint: str,
    routes: dict[str, str],
) -> str:
    prior = {row.school: row for row in comparison}
    if baseline is None:
        note = "Earlier rankings are unavailable." if checkpoint != "PRESEASON" else ""
    elif checkpoint == "FINAL" and baseline != "PRESEASON":
        note = f"Change since {_label(baseline)}, the first archived ranking."
    else:
        note = f"Change since {_label(baseline)}."

    body = []
    for row in sorted(rows, key=lambda row: row.rank):
        previous = prior.get(row.school)
        rank_change = rating_change = "—"
        if previous is not None:
            rank_delta = previous.rank - row.rank
            rating_delta = row.rating - previous.rating
            rank_change = f"{rank_delta:+d}" if rank_delta else "0"
            rating_change = f"{rating_delta:+.2f}" if rating_delta else "0.00"
        body.append(
            f'<tr><td class="num">{row.rank}</td><th scope="row">{school_label(row.school, routes)}</th>'
            f'<td class="num">{rank_change}</td><td class="num">{rating_change}</td>'
            f'<td class="num">{row.rating:.2f}</td><td class="num">{escape(row.record)}</td>'
            f'<td>{escape(row.conference)}</td></tr>'
        )
    return (
        (f"<p>{note}</p>" if note else "")
        + '<div class="table-scroll" role="region" aria-labelledby="cors-ranking" tabindex="0">'
        '<table aria-labelledby="cors-ranking"><thead><tr>'
        '<th scope="col" class="num">Rank</th><th scope="col">Team</th>'
        '<th scope="col" class="num">Rank change</th><th scope="col" class="num">Rating change</th>'
        '<th scope="col" class="num">CORS rating</th><th scope="col" class="num">Record</th>'
        '<th scope="col">Conference</th>'
        f'</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
    )


def _upsets(games: Iterable[Game], routes: dict[str, str]) -> str:
    qualifying = []
    has_scored_forecast = False
    for game in games:
        handicap = game.home_handicap
        if handicap is None or not handicap.is_finite():
            continue
        deficit = handicap.copy_abs()
        if game.home_score is None or game.away_score is None:
            continue
        has_scored_forecast = True
        if deficit < 7:
            continue
        if handicap < 0 and game.away_score > game.home_score:
            qualifying.append((deficit, game.week, game.home, game.away, game.away_score, game.home_score))
        elif handicap > 0 and game.home_score > game.away_score:
            qualifying.append((deficit, game.week, game.away, game.home, game.home_score, game.away_score))
    # Preserve archive game order when two Model Spreads have equal magnitude.
    qualifying.sort(key=lambda upset: upset[0], reverse=True)
    title = "Biggest CORS upsets" if qualifying else "CORS upsets"
    heading = f'<section aria-labelledby="cors-upsets"><h2 id="cors-upsets">{title}</h2>'
    if not qualifying:
        note = "No wins by underdogs of 7+ points." if has_scored_forecast else "No final scores with a CORS spread."
        return heading + f"<p>{note}</p></section>"
    rows = []
    for deficit, week, favorite, winner, winner_score, loser_score in qualifying[:3]:
        favorite_link = school_label(favorite, routes)
        winner_link = school_label(winner, routes)
        rows.append(
            f'<tr><td class="num">{week}</td><td>{favorite_link} by <span class="num">{deficit:.2f}</span></td>'
            f'<td>{winner_link} <span class="num">{winner_score}–{loser_score}</span> {favorite_link}</td></tr>'
        )
    return (
        heading + '<div class="table-scroll" role="region" aria-labelledby="cors-upsets" tabindex="0">'
        '<table aria-labelledby="cors-upsets"><thead><tr>'
        '<th scope="col" class="num">Week</th><th scope="col">CORS favorite</th><th scope="col">Final score</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def _standings(title: str, table: str, upsets: str) -> str:
    rankings = f'<section aria-labelledby="cors-ranking"><h2 id="cors-ranking">{title}</h2>{table}</section>'
    return f'<div class="season-standings">{rankings}{upsets}</div>' if upsets else rankings


def _document(title: str, heading: str, body: str, *, metadata: str = "", head: str = "") -> str:
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(title)}</title><link rel="stylesheet" href="{_STYLESHEET}">{head}</head>'
        f'<body class="record-book season-page"><header><h1>{escape(heading)}</h1>{metadata}</header>'
        f"<main>{body}</main></body></html>\n"
    )


def build_season_pages(site: Path, season: Season, routes: dict[str, str]) -> Iterator[tuple[str, str]]:
    """Yield the existing season landing and one stable HTML route per ranking."""
    year = season.year
    routes = season_team_routes(season, routes)
    base = f"cfb/years/{year}"
    directory = site / base
    filename = f"{year}_CFB.html"
    landing_path = directory / filename
    raw = source_bytes(landing_path.read_bytes()) if landing_path.exists() else b""
    original = BeautifulSoup(raw.decode("utf-8"), "html.parser")
    default_title = f"{year} CFB Season"
    title = original.title.get_text(strip=True) if original.title else default_title
    heading = original.h1.get_text(strip=True) if original.h1 else default_title
    if version := re.fullmatch(rf"{year} CORS (v[\d.]+) CFB Results", title):
        title = f"{year} college football · CORS {version[1]}"
    if re.fullmatch(rf"{year} (?:CORS v[\d.]+ CFB Results|College Football|CFB Season)", heading):
        heading = f"{year} college football"
    # Legacy archives can place links after </html>. On reruns, read only the
    # archive section so generated shortcuts and team links stay out of it.
    generated = original.select_one("body.season-page") is not None
    if generated:
        notes = original.select_one("#season-notes")
        metadata = str(notes) if notes is not None else ""
        archive_source = original.select_one("#season-archive")
    else:
        source = original.body or original
        notes_html = "".join(str(p) for p in source.find_all("p") if not p.find("a"))
        metadata = f'<div id="season-notes">{notes_html}</div>' if notes_html else ""
        archive_source = original
    metadata = re.sub(
        r"<p>(?:Last updated: |Ranking data updated <time>)([0-9][0-9T:.+ Z-]*)(?:</time>)?</p>",
        lambda m: f"<p>{stamp}</p>" if (stamp := updated_time(m[1])) else m[0], metadata,
    )
    if not metadata and season.updated_at:
        stamp = updated_time(season.updated_at, "Rankings updated") or escape(season.updated_at)
        metadata = f'<div id="season-notes"><p>{stamp}</p></div>'
    head = "".join(
        str(tag) for tag in original.select("head meta")
        if not tag.has_attr("charset") and tag.get("name") != "viewport"
    )
    archive = _archive_navigation(site, directory, archive_source, year) if archive_source is not None else ""
    checkpoints = sorted((key for key, rows in season.rankings.items() if rows), key=_checkpoint_order)
    if not checkpoints:
        yield f"{base}/{filename}", _document(
            title, heading, "<p>No CORS rankings for this season.</p>" + archive, metadata=metadata, head=head
        )
        return

    games_by_week = defaultdict(list)
    for game in season.games:
        games_by_week[game.week].append(game)
    landing = checkpoints[-1]
    landing_content = ""
    for index, checkpoint in enumerate(checkpoints):
        baseline = None
        if index:
            baseline = checkpoints[0] if checkpoint == "FINAL" else checkpoints[index - 1]
        standings = _rankings(
            season.rankings[checkpoint], season.rankings[baseline] if baseline else [], baseline, checkpoint, routes
        )
        upsets = ""
        if checkpoint == "FINAL":
            upsets = _upsets(season.games, routes)
        elif checkpoint.startswith("W"):
            upsets = _upsets(games_by_week[int(checkpoint[1:])], routes)
        content = _standings("CORS rankings", standings, upsets)
        if checkpoint == landing:
            landing_content = _standings(f"{_label(checkpoint)} CORS rankings", standings, upsets)

        links = [_anchor("../" + filename, f"{year} season")]
        if checkpoint != "PRESEASON":
            links.append(_anchor("#cors-upsets", "Upsets"))
        navigation = _checkpoint_navigation(checkpoints, checkpoint)
        matchups = _matchup_navigation(directory, year, checkpoint, "../")
        checkpoint_title = f"{year} CFB · {_label(checkpoint)}"
        yield f"{base}/checkpoints/{checkpoint}.html", _document(
            checkpoint_title, checkpoint_title, f'<p>{" · ".join(links)}</p>' + navigation + matchups + content
        )

    links = []
    if landing != "PRESEASON":
        links.append(_anchor("#cors-upsets", "Upsets"))
    if archive:
        links.append(_anchor("#season-archive", "Season archive"))
    jumps = f'<p>{" · ".join(links)}</p>' if links else ""
    navigation = _checkpoint_navigation(checkpoints)
    matchups = _matchup_navigation(directory, year, landing, "")
    yield f"{base}/{filename}", _document(
        title, heading, navigation + matchups + jumps + landing_content + archive, metadata=metadata, head=head
    )
