"""Static directory, history, and season pages for ranked CFB programs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

from .archive_presentation import updated_time
from .conference_pages import conference_route
from .reference_archive import program_season_route, season_team_routes
from .reference_markup import school_label

if TYPE_CHECKING:
    from cfb.reference_archive import Game, Season, Standing


_DIRECTORY = "/cfb/teams/index.html"
_MISSING = "—"


@dataclass
class _ProgramSeason:
    season: Season
    standings: dict[str, Standing]
    games: dict[int, list[Game]]

    @property
    def latest_checkpoint(self) -> str:
        return next(key for key, rows in reversed(self.season.rankings.items()) if rows)


def build_program_pages(
    seasons: list[Season], routes: dict[str, str]
) -> Iterator[tuple[str, str]]:
    """Yield the program index and one history and season page per ranked program."""
    histories: dict[str, list[_ProgramSeason]] = defaultdict(list)
    opponent_routes = {season.year: season_team_routes(season, routes) for season in seasons}
    for season in sorted(seasons, key=lambda season: season.year, reverse=True):
        standings: dict[str, dict[str, Standing]] = defaultdict(dict)
        for checkpoint, rows in season.rankings.items():
            for row in rows:
                standings[row.school][checkpoint] = row

        games: dict[str, dict[int, list[Game]]] = defaultdict(lambda: defaultdict(list))
        for game in season.games:
            for school in (game.home, game.away):
                if school in standings:
                    games[school][game.week].append(game)

        for school, checkpoints in standings.items():
            histories[school].append(_ProgramSeason(season, checkpoints, games.get(school, {})))

    schools = sorted(histories, key=lambda school: (school.casefold(), school))
    directory = '<ul class="program-list">' + "".join(
        f"<li>{_link(routes[school], school)}</li>" for school in schools
    ) + "</ul>"
    yield _DIRECTORY.lstrip("/"), _page(
        "College football teams", "College football teams", [], directory, seasons[-1].updated_at if seasons else ""
    )

    for school in schools:
        route = routes[school]
        history = histories[school]
        name = escape(school)
        heading = f'<span data-program="{name}">{name}</span>'
        yield route.lstrip("/"), _page(
            f"{school} football", heading, [],
            _rank_trend(route, history) + _history_table(route, history), history[0].season.updated_at,
        )
        for entry in history:
            year = entry.season.year
            links = [(route, f"{school} history"), (f"/cfb/years/{year}/{year}_CFB.html", f"{year} season")]
            standing = entry.standings.get(entry.latest_checkpoint)
            if standing and standing.conference:
                links.append((conference_route(standing.conference, year), f"{year} {standing.conference}"))
            yield program_season_route(route, year).lstrip("/"), _page(
                f"{year} {school} football", f"{year} {heading}",
                links,
                _season_table(school, entry, opponent_routes[year]), entry.season.updated_at,
            )


def _link(route: str, text: str) -> str:
    return f'<a href="{escape(route)}">{escape(text)}</a>'


def _page(title: str, heading: str, links: list[tuple[str, str]], content: str, updated_at: str) -> str:
    links_html = " · ".join(_link(route, label) for route, label in links)
    navigation = f'<nav class="archive-nav" aria-label="Related records">{links_html}</nav>' if links else ""
    timestamp = updated_time(updated_at, "Rankings updated") or escape(updated_at)
    stamp = f"<footer>{timestamp}</footer>" if timestamp else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} | SportsRank</title>
<link rel="stylesheet" href="/cfb/assets/record-book.css">
</head>
<body class="record-book">
{navigation}
<main>
<h1>{heading}</h1>
{content}
</main>
{stamp}
</body>
</html>
"""


def _checkpoint_label(checkpoint: str) -> str:
    if checkpoint.startswith("W"):
        return f"Week {checkpoint[1:]}"
    return checkpoint.title()


def _table(label: str, headers: tuple[str, ...], body: str, *, caption: str = "") -> str:
    columns = "".join(f'<th scope="col">{header}</th>' for header in headers)
    table = f"<table><caption>{caption}</caption>" if caption else f'<table aria-label="{label}">'
    return (
        f'<div class="table-scroll" role="region" aria-label="{label}" tabindex="0">'
        f"{table}<thead><tr>{columns}</tr></thead>{body}</table></div>\n"
    )


def _history_table(route: str, history: list[_ProgramSeason]) -> str:
    rows = []
    for entry in history:
        year = entry.season.year
        checkpoint = entry.latest_checkpoint
        standing = entry.standings.get(checkpoint)
        through = "Through " if checkpoint.startswith("W") else ""
        best_rank = min(row.rank for row in entry.standings.values())
        conference = _link(conference_route(standing.conference, year), standing.conference) if standing and standing.conference else _MISSING
        rows.append(
            f'<tr><th scope="row">{_link(program_season_route(route, year), str(year))}</th>'
            f"<td>{through}{_checkpoint_label(checkpoint)}</td>"
            f'<td class="num">{standing.rank if standing else _MISSING}</td>'
            f'<td class="num">{format(standing.rating, "f") if standing else _MISSING}</td>'
            f'<td class="num">{best_rank}</td>'
            f'<td class="num">{escape(standing.record) if standing else _MISSING}</td>'
            f"<td>{conference}</td></tr>\n"
        )
    return _table(
        "Season history",
        ("Season", "As of", "National rank", "CORS rating", "Best rank", "Record", "Conference"),
        "<tbody>" + "".join(rows) + "</tbody>",
        caption="Season history",
    )


def _rank_trend(route: str, history: list[_ProgramSeason]) -> str:
    ranks = [
        (entry.season.year, entry.standings["FINAL"].rank)
        for entry in reversed(history)
        if "FINAL" in entry.standings
    ]
    if not ranks:
        return ""

    left, right, top, bottom = 39, 452, 13, 116
    first, last = ranks[0][0], ranks[-1][0]
    last_rank = max(5, max(rank for _, rank in ranks))

    def x(year: int) -> float:
        if first == last:
            return (left + right) / 2
        return left + (right - left) * (year - first) / (last - first)

    def y(rank: int) -> float:
        return top + (bottom - top) * (rank - 1) / (last_rank - 1)

    grid = []
    for rank in (1, (last_rank + 1) // 2, last_rank):
        height = y(rank)
        grid.append(
            f'<line x1="{left}" y1="{height:.2f}" x2="{right}" y2="{height:.2f}" '
            'stroke="currentColor" opacity=".2"/>'
            f'<text x="{left - 7}" y="{height + 4:.2f}" text-anchor="end">{rank}</text>'
        )

    years = [
        f'<text x="{x(year):.2f}" y="136" text-anchor="middle">{year}</text>'
        for year in dict.fromkeys((first, last))
    ]
    segments = [
        f'<line x1="{x(year):.2f}" y1="{y(rank):.2f}" '
        f'x2="{x(next_year):.2f}" y2="{y(next_rank):.2f}"/>'
        for (year, rank), (next_year, next_rank) in zip(ranks, ranks[1:])
        if next_year == year + 1
    ]
    dots = []
    for i, (year, rank) in enumerate(ranks):
        cx, cy = x(year), y(rank)
        # Keep the clickable area within this season's share of the time axis.
        start = max(cx - 12, (cx + x(ranks[i - 1][0])) / 2) if i else cx - 12
        end = min(cx + 12, (cx + x(ranks[i + 1][0])) / 2) if i + 1 < len(ranks) else cx + 12
        label = f"{year} season, final rank {rank}"
        dots.append(
            f'<a href="{escape(program_season_route(route, year))}" aria-label="{label}">'
            f'<title>{label}</title>'
            f'<rect x="{start:.2f}" y="{cy - 12:.2f}" width="{end - start:.2f}" '
            f'height="24" fill="transparent"/>'
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.5"/></a>'
        )
    return (
        '<figure class="rank-trend"><figcaption id="rank-trend-title">'
        "Final national rank</figcaption>"
        '<svg viewBox="0 0 480 142" width="100%" role="group" aria-labelledby="rank-trend-title" '
        'aria-describedby="rank-trend-description">'
        '<desc id="rank-trend-description">'
        "Gaps mark seasons without a final rank. See Season history for each rank."
        '</desc><g aria-hidden="true" fill="currentColor" font-size="12">'
        + "".join(grid + years)
        + '</g><g aria-hidden="true" stroke="currentColor" stroke-width="1.5">'
        + "".join(segments)
        + '</g><g fill="currentColor">'
        + "".join(dots)
        + "</g></svg></figure>\n"
    )


def _season_table(school: str, entry: _ProgramSeason, routes: dict[str, str]) -> str:
    rankings = entry.season.rankings
    weeks = {int(key[1:]) for key, rows in rankings.items() if rows and key.startswith("W")}
    checkpoints = ["PRESEASON"] if rankings.get("PRESEASON") else []
    checkpoints.extend(f"W{week}" for week in sorted(weeks | entry.games.keys()))
    if rankings.get("FINAL"):
        checkpoints.append("FINAL")

    groups = []
    for checkpoint in checkpoints:
        games = entry.games.get(int(checkpoint[1:]), []) if checkpoint.startswith("W") else []
        standing = entry.standings.get(checkpoint)
        span = f' rowspan="{len(games)}"' if len(games) > 1 else ""
        if games:
            game_rows = [_game_cells(school, game, routes) for game in games]
        else:
            opponent = "No game listed" if checkpoint.startswith("W") else _MISSING
            game_rows = [f"<td>{opponent}</td>" + f"<td>{_MISSING}</td>" * 3]
        groups.append(
            f'<tbody><tr><th scope="rowgroup"{span}>{_checkpoint_label(checkpoint)}</th>'
            f'<td class="num"{span}>{standing.rank if standing else _MISSING}</td>'
            f'<td class="num"{span}>{format(standing.rating, "f") if standing else _MISSING}</td>'
            f"<td{span}>{escape(standing.record) if standing else _MISSING}</td>"
            + game_rows[0]
            + "</tr>"
            + "".join(f"<tr>{row}</tr>" for row in game_rows[1:])
            + "</tbody>\n"
        )

    return _table(
        "Rankings and games",
        ("Week", "National rank", "CORS rating", "Record", "Opponent", "Site",
         "CORS spread <small>Negative if favored</small>", f"Score <small>{escape(school)} first</small>"),
        "".join(groups),
    )


def _game_cells(school: str, game: Game, routes: dict[str, str]) -> str:
    at_home = game.home == school
    if at_home:
        opponent, side = game.away, "Home"
        team_score, opponent_score = game.home_score, game.away_score
    else:
        opponent, side = game.home, "Away"
        team_score, opponent_score = game.away_score, game.home_score

    if game.neutral_site:
        side = "Neutral"

    score = _MISSING
    if team_score is not None and opponent_score is not None:
        score = f"{team_score}–{opponent_score}"

    spread = _MISSING
    if game.home_handicap is not None:
        handicap = game.home_handicap if at_home else game.home_handicap.copy_negate()
        spread = format(handicap, "+f") if handicap else format(handicap.copy_abs(), "f")

    name = school_label(opponent, routes)
    return f'<td>{name}</td><td>{side}</td><td class="num">{spread}</td><td class="num">{score}</td>'
