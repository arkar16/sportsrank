"""Static directory, history, and season pages for ranked CFB programs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import TYPE_CHECKING

from .archive_presentation import updated_time
from .conference_pages import conference_route, projected_overall_records
from .excitement_pages import ESTIMATE_NOTE, aev_label, bev_label
from .game_excitement import Matchup, score_for
from .reference_archive import _checkpoint_order, program_season_route, season_team_routes
from .reference_markup import school_label
from .win_probability import format_win_chance

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


@dataclass(frozen=True)
class ScheduledGame:
    game: Game
    date_label: str
    opponent_rank: int | None
    forecast_id: str = ""


@dataclass(frozen=True)
class TeamSchedule:
    fixtures: tuple[ScheduledGame, ...]
    snapshot_date: date
    rating_date: date


def _is_upcoming(game: Game, through_week: int | None) -> bool:
    return (through_week is not None and game.week > through_week
            and game.home_score is None and game.away_score is None)


def _checkpoint_label(checkpoint: str) -> str:
    if checkpoint.startswith("W"):
        return f"Week {checkpoint[1:]}"
    return checkpoint.title()


def build_program_pages(
    seasons: list[Season], routes: dict[str, str], *, sport="football", directory_route=_DIRECTORY,
    season_route=lambda year: f"/cfb/years/{year}/{year}_CFB.html",
    year_label=str, directory_label=None, remaining_schedules=None, projections=None,
    checkpoint_route=lambda year, checkpoint: f"/cfb/years/{year}/checkpoints/{checkpoint}.html",
    best_checkpoint_label=_checkpoint_label, excitement_by_year=None,
) -> Iterator[tuple[str, str]]:
    """Yield the program index and one history and season page per ranked program."""
    histories: dict[str, list[_ProgramSeason]] = defaultdict(list)
    opponent_routes = {season.year: season_team_routes(season, routes) for season in seasons}
    schedules = remaining_schedules or {}
    latest_year = max(opponent_routes, default=None)
    football_week = None
    if sport == "football":
        latest = next((season for season in seasons if season.year == latest_year), None)
        if latest is not None and not latest.rankings.get("FINAL"):
            football_week = max((int(key[1:]) for key, rows in latest.rankings.items()
                                 if key.startswith("W") and rows), default=-1)
            projections = projected_overall_records(latest, sport=sport)
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
        f"<li>{directory_label(school) if directory_label else _link(routes[school], school)}</li>" for school in schools
    ) + "</ul>"
    yield directory_route.lstrip("/"), _page(
        f"College {sport} teams", f"College {sport} teams", [], directory, seasons[-1].updated_at if seasons else ""
    )

    for school in schools:
        route = routes[school]
        history = histories[school]
        name = escape(school)
        heading = f'<span data-program="{name}">{name}</span>'
        yield route.lstrip("/"), _page(
            f"{school} {sport}", heading, [],
            _rank_trend(route, history, year_label)
            + _history_table(route, history, sport, year_label, checkpoint_route, best_checkpoint_label),
            history[0].season.updated_at,
        )
        for entry in history:
            year = entry.season.year
            links = [(route, f"{school} history"), (season_route(year), f"{year_label(year)} season")]
            standing = entry.standings.get(entry.latest_checkpoint)
            if standing and standing.conference and standing.conference != "—":
                links.append((conference_route(standing.conference, year, sport=sport), f"{year_label(year)} {standing.conference}"))
            excitement = (excitement_by_year or {}).get(year)
            if excitement:
                base = f"/cbb/seasons/{year}" if sport == "basketball" else f"/cfb/years/{year}"
                links.append((f"{base}/excitement/index.html", "Game excitement"))
            log = _season_table(
                school, entry, opponent_routes[year], sport=sport,
                schedule=schedules.get((year, school)), excitement=excitement,
                through_week=football_week if year == latest_year else None,
                projection=projections.get(school) if projections is not None and year == latest_year else None,
            )
            yield program_season_route(route, year).lstrip("/"), _page(
                f"{year_label(year)} {school} {sport}", f"{year_label(year)} {heading}",
                links,
                log, entry.season.updated_at,
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


def _table(label: str, headers: tuple[str, ...], body: str, *, caption: str = "") -> str:
    columns = "".join(f'<th scope="col">{header}</th>' for header in headers)
    table = f"<table><caption>{caption}</caption>" if caption else f'<table aria-label="{label}">'
    return (
        f'<div class="table-scroll" role="region" aria-label="{label}" tabindex="0">'
        f"{table}<thead><tr>{columns}</tr></thead>{body}</table></div>\n"
    )


def _best_rank_content(entry: _ProgramSeason, season_label: str, checkpoint_route, checkpoint_label) -> str:
    best_rank = min(row.rank for row in entry.standings.values())
    best_checkpoints = sorted(
        (checkpoint for checkpoint, row in entry.standings.items() if row.rank == best_rank),
        key=_checkpoint_order,
    )
    # Basketball aliases FINAL to the final weekly ranking list; avoid
    # treating that alias as a second distinct checkpoint in tie wording.
    if len(best_checkpoints) > 1 and best_checkpoints[-1] == "FINAL":
        rankings = entry.season.rankings
        season_checkpoints = sorted((key for key, rows in rankings.items() if rows), key=_checkpoint_order)
        if (
            len(season_checkpoints) >= 2
            and season_checkpoints[-1] == "FINAL"
            and season_checkpoints[-2] in best_checkpoints
            and rankings["FINAL"] is rankings[season_checkpoints[-2]]
        ):
            best_checkpoints = best_checkpoints[:-1]

    first_checkpoint = best_checkpoints[0]
    href = checkpoint_route(entry.season.year, first_checkpoint) if checkpoint_route is not None else None
    if not href:
        return str(best_rank)

    labeler = checkpoint_label or _checkpoint_label
    first = labeler(first_checkpoint)
    rank_label = f"{season_label} best rank {best_rank}"
    if len(best_checkpoints) == 1:
        title = first
        aria_label = f"{rank_label}, {first}"
    else:
        title = f"First reached {first}"
        aria_label = f"{rank_label}, first reached {first}"
    return f'<a href="{escape(href)}" title="{escape(title)}" aria-label="{escape(aria_label)}">{best_rank}</a>'


def _history_table(
    route: str, history: list[_ProgramSeason], sport: str, year_label, checkpoint_route, checkpoint_label,
) -> str:
    rows = []
    for entry in history:
        year = entry.season.year
        season_label = str(year_label(year))
        checkpoint = entry.latest_checkpoint
        standing = entry.standings.get(checkpoint)
        through = "Through " if checkpoint.startswith("W") else ""
        best_rank_html = _best_rank_content(entry, season_label, checkpoint_route, checkpoint_label)
        conference = escape(standing.conference) if standing else _MISSING
        if standing and standing.conference and standing.conference != "—":
            conference = _link(conference_route(standing.conference, year, sport=sport), standing.conference)
        rows.append(
            f'<tr><th scope="row">{_link(program_season_route(route, year), season_label)}</th>'
            f"<td>{through}{_checkpoint_label(checkpoint)}</td>"
            f'<td class="num">{standing.rank if standing else _MISSING}</td>'
            f'<td class="num">{format(standing.rating, "f") if standing else _MISSING}</td>'
            f'<td class="num">{best_rank_html}</td>'
            f'<td class="num">{escape(standing.record) if standing else _MISSING}</td>'
            f"<td>{conference}</td></tr>\n"
        )
    return _table(
        "Season history",
        ("Season", "As of", "National rank", "CORS rating", "Best rank", "Record", "Conference"),
        "<tbody>" + "".join(rows) + "</tbody>",
        caption="Season history",
    )


def _rank_trend(route: str, history: list[_ProgramSeason], year_label=str) -> str:
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
        label = f"{year_label(year)} season, final rank {rank}"
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


def _season_table(
    school: str, entry: _ProgramSeason, routes: dict[str, str], *, sport: str,
    schedule: TeamSchedule | None, through_week: int | None, projection: str | None, excitement=None,
) -> str:
    rankings = entry.season.rankings
    weeks = {int(key[1:]) for key, rows in rankings.items() if rows and key.startswith("W")}
    checkpoints = ["PRESEASON"] if rankings.get("PRESEASON") else []
    checkpoints.extend(f"W{week}" for week in sorted(weeks | entry.games.keys()))
    if rankings.get("FINAL"):
        checkpoints.append("FINAL")

    has_upcoming = bool(schedule) or any(
        _is_upcoming(game, through_week) for games in entry.games.values() for game in games
    )
    anchor = ' id="remaining-schedule"'

    def row_start(upcoming):
        nonlocal anchor
        if upcoming and anchor:
            start = f"<tr{anchor}>"
            anchor = ""
            return start
        return "<tr>"

    has_estimate = False

    def excitement_cells(game):
        nonlocal has_estimate
        if not excitement:
            return ""
        if sport == "basketball":
            score = excitement.get(game)
        else:
            key = Matchup(game.week, game.home, game.away, game.neutral_site)
            score = score_for(excitement, key, (game.home_score, game.away_score))
        if score and score.estimated:
            has_estimate = True
        return f'<td class="num">{bev_label(score)}</td><td class="num">{aev_label(score)}</td>'

    groups = []
    for checkpoint in checkpoints:
        games = entry.games.get(int(checkpoint[1:]), []) if checkpoint.startswith("W") else []
        standing = entry.standings.get(checkpoint)
        span = f' rowspan="{len(games)}"' if len(games) > 1 else ""
        if games:
            game_rows = [(_is_upcoming(game, through_week), game) for game in games]
            game_rows.sort(key=lambda row: row[0])
            rows = [
                (row_start(upcoming), _game_cells(school, game, routes, sport=sport,
                                                  upcoming=upcoming, show_chance=has_upcoming) + excitement_cells(game))
                for upcoming, game in game_rows
            ]
        else:
            opponent = "No game listed" if checkpoint.startswith("W") else _MISSING
            if schedule and checkpoint == entry.latest_checkpoint:
                opponent = "No result yet"
            rows = [("<tr>", f"<td>{opponent}</td>" + f"<td>{_MISSING}</td>" * (3 + has_upcoming + (2 if excitement else 0)))]
        groups.append(
            f'<tbody>{rows[0][0]}<th scope="rowgroup"{span}>{_checkpoint_label(checkpoint)}</th>'
            f'<td class="num"{span}>{standing.rank if standing else _MISSING}</td>'
            f'<td class="num"{span}>{format(standing.rating, "f") if standing else _MISSING}</td>'
            f"<td{span}>{escape(standing.record) if standing else _MISSING}</td>"
            + rows[0][1]
            + "</tr>"
            + "".join(f"{start}{cells}</tr>" for start, cells in rows[1:])
            + "</tbody>\n"
        )

    note = ""
    if schedule:
        note = (f'<p>Upcoming CORS spreads and opponent ranks use the {schedule.rating_date:%B %d} ratings. '
                f'Schedule as of {schedule.snapshot_date:%B %d, %Y}.</p>')
        for fixture in schedule.fixtures:
            groups.append(
                f'<tbody>{row_start(True)}<th scope="rowgroup">{escape(fixture.date_label)}'
                f'<br><small>{escape(fixture.game.tipoff_label)}</small></th>'
                + f"<td>{_MISSING}</td>" * 3
                + _game_cells(school, fixture.game, routes, sport=sport, upcoming=True,
                              show_chance=True, opponent_rank=fixture.opponent_rank)
                + excitement_cells(fixture.game)
                + "</tr></tbody>\n"
            )

    headers = ("Week / Date" if schedule else "Week", "National rank", "CORS rating", "Record", "Opponent",
               "Site", "CORS spread <small>Negative if favored</small>")
    if has_upcoming:
        headers += ("Win chance",)
    headers += (f"Score <small>{escape(school)} first</small>",)
    if excitement:
        headers += ("BEV <small>Pregame appeal</small>", "AEV <small>Postgame drama</small>")
    log = _table("Rankings and games", headers, "".join(groups))
    if has_estimate:
        log += f"<p>{ESTIMATE_NOTE}</p>"
    if has_upcoming:
        log = ('<p><a href="#remaining-schedule">Remaining schedule</a></p>'
               '<h2>Weekly rankings and games</h2>' + note + log)
    if projection is not None or has_upcoming:
        log += f'<p id="projected-season-record">Projected season record <strong>{projection or _MISSING}</strong></p>'
    return log


def _game_cells(
    school: str, game: Game, routes: dict[str, str], *, sport: str, upcoming: bool,
    show_chance: bool, opponent_rank: int | None = None,
) -> str:
    at_home = game.home == school
    if at_home:
        opponent, side = game.away, "Home"
        team_score, opponent_score = game.home_score, game.away_score
    else:
        opponent, side = game.home, "Away"
        team_score, opponent_score = game.away_score, game.home_score

    if game.neutral_site:
        side = "Neutral"

    handicap = game.home_handicap
    spread = _MISSING
    basketball_fixture = upcoming and sport == "basketball"
    if handicap is not None:
        if not at_home:
            handicap = handicap.copy_negate()
        spread = format(handicap, "+f") if handicap else format(handicap.copy_abs(), "f")
        if basketball_fixture:
            spread = f"{handicap:+.2f}" if handicap else "Pick’em"

    name = school_label(opponent, routes)
    if basketball_fixture:
        name = f"#{opponent_rank} {name}" if opponent_rank is not None else escape(opponent)
    elif game.tipoff_label:
        name += f'<small>{escape(game.tipoff_label)}</small>'
    outcome = f"{team_score}–{opponent_score}" if team_score is not None and opponent_score is not None else _MISSING
    cells = f'<td>{name}</td><td>{side}</td><td class="num">{spread}</td>'
    if show_chance:
        chance = format_win_chance(handicap, sport=sport) if upcoming else _MISSING
        cells += f'<td class="num">{chance}</td>'
    return cells + f'<td class="num">{outcome}</td>'
