"""Season game logs for the opponent buckets in conference OOC records."""
from datetime import datetime, timezone
from html import escape

from .reference_markup import school_label
from .week_calendar import EASTERN


def conference_pair(first, second):
    return (first, second) if first <= second else (second, first)


def matchup_route(first, second, year, route):
    left, right = conference_pair(first, second)
    directory, left_slug, _ = route(left, year).rsplit("/", 2)
    right_slug = route(right, year).rsplit("/", 2)[1]
    return f"{directory}/matchups/{year}/{left_slug}--{right_slug}.html"


def _start(game):
    if not game.start_utc:
        return None
    start = datetime.fromisoformat(game.start_utc)
    if start.tzinfo is None:
        zone = EASTERN if len(game.start_utc) == 10 else timezone.utc
        start = start.replace(tzinfo=zone)
    return start.astimezone(timezone.utc)


def _fixture_key(game, sport):
    home, away = conference_pair(game.home, game.away) if game.neutral_site else (game.home, game.away)
    start = _start(game) if sport == "basketball" else None
    day_or_week = start.astimezone(EASTERN).date() if start is not None else game.week
    return day_or_week, home, away, game.neutral_site


def upcoming_matchup_games(games, completed, checkpoint, sport):
    if checkpoint == "FINAL":
        return []
    week = int(checkpoint[1:]) if checkpoint.startswith("W") else -1
    played = {_fixture_key(game, sport) for game in completed}
    scheduled = {}
    for game in games:
        if game.home_score is not None or game.away_score is not None:
            continue
        if sport == "football" and game.week <= week:
            continue
        key = _fixture_key(game, sport)
        if key in played:
            continue
        previous = scheduled.get(key)
        if previous is None or (game.start_utc and not previous.start_utc):
            scheduled[key] = game
    return scheduled.values()


def _chronology(game, sport):
    start = _start(game)
    if sport == "football":
        return game.week, start is None, start
    return start is None, start, game.week


def _row(game, team_routes, sport):
    start = _start(game)
    week = f"Week {game.week}"
    when = week
    if start is not None:
        day = start.astimezone(EASTERN).date()
        label = game.tipoff_label if sport == "basketball" and game.tipoff_label else f"{day:%b %d, %Y}"
        when = f'<time datetime="{day.isoformat()}">{escape(label)}</time>'
        if sport == "football":
            when += f" · {week}"
    away = school_label(game.away, team_routes)
    home = school_label(game.home, team_routes)
    site = "Neutral" if game.neutral_site else "Home"
    score = "Scheduled" if game.home_score is None else f"{game.away_score}–{game.home_score}"
    return (f'<tr><td>{when}</td><td>{away}</td><td>{home}</td>'
            f'<td>{site}</td><td>{score}</td></tr>')


def build_matchup_pages(matchups, groups, route, year, year_label, team_routes, *, page, sport, edition):
    for (left, right), games in sorted(matchups.items()):
        links = [f'<a href="{escape(route(name, year))}">{escape(name)} standings</a>'
                 for name in dict.fromkeys((left, right)) if name in groups]
        if not links:
            continue
        nav = f'<nav aria-label="Conference seasons">{" · ".join(links)}</nav>'
        coverage = f'<p class="edition">{escape(edition)}</p>'
        if left == right:
            coverage += '<p>Each completed game appears once and counts toward both teams’ records.</p>'
        content = (
            f'{nav}{coverage}'
            '<div class="table-scroll" role="region" aria-label="Out-of-conference games" tabindex="0">'
            '<table><thead><tr><th scope="col">Date / week</th><th scope="col">Away team</th>'
            '<th scope="col">Home team</th><th scope="col">Site</th><th scope="col">Score (away–home)</th>'
            '</tr></thead><tbody>'
            + "".join(_row(game, team_routes, sport) for game in sorted(games, key=lambda g: _chronology(g, sport)))
            + "</tbody></table></div>"
        )
        yield matchup_route(left, right, year, route).lstrip("/"), page(f"{year_label(year)} {left} vs. {right}", content)
