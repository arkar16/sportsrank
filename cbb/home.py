"""Basketball inputs for the shared football landing-page layout."""
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from cfb.record_book import home_document, home_rankings
from cfb.reference_archive import season_team_routes
from .record_book import EASTERN, season_name


def landing_page(archives, routes, presentation, checkpoint, edition_path, tournaments, *, model="CORS v0.4.0"):
    season = archives[-1]
    week = checkpoint["week"]
    previous = week - 1
    prior = {r.school: r.rank for r in season.rankings.get(f"W{previous}", ())}
    games = defaultdict(list)
    for game in season.games:
        if game.week == week or (game.week == previous and game.home_score is not None):
            for school in (game.home, game.away):
                games[game.week, school].append(game)
    # Basketball ratings are issued at the start of a slate. Last week's opponents
    # use last week's ranks, and this week's spreads use the displayed checkpoint.
    rankings = home_rankings(
        season.rankings[f"W{week}"], season_team_routes(season, routes), games,
        previous=f"W{previous}" if week else None, previous_ranks=prior, pregame_ranks=prior,
        week=previous if week else None, next_week=week,
        empty_result="No game listed" if week else "No games yet", empty_forecast="No game listed",
        forecast_link=f'<p><a href="/cbb/current.html#forecasts">Week {week} forecasts</a></p>',
    )
    decades = defaultdict(list)
    for s in reversed(archives):
        decades[s.year // 10 * 10].append(
            f'<a href="/cbb/seasons/{s.year}/index.html">{season_name(s.year)}</a>')
    archive = "".join(f'<div><h3>{d}s</h3><p>{" ".join(links)}</p></div>' for d, links in decades.items())
    links = (f'<p><a href="/cbb/teams/index.html">{len(routes)} program histories</a></p>'
             '<p><a href="/cbb/seasons/index.html">Season summaries</a></p>'
             f'<p id="forecasts"><a href="current.html#forecasts">Game forecasts</a></p>'
             f'<p><a href="editions/{edition_path.name}">Dated edition</a></p>')
    if tournaments:
        links += '<p><a href="/cbb/tournaments/index.html">March Madness picks and results</a></p>'
    date = datetime.fromisoformat(checkpoint["cutoff_utc"]).astimezone(EASTERN)
    label = season_name(season.year)
    stylesheet = Path(__file__).resolve().parents[1] / "cfb/assets/record-book.css"
    revision = sha256(stylesheet.read_bytes()).hexdigest()[:12]
    page = home_document("College Basketball | SportsRank", "Men’s college basketball",
                         f"{label} Division I &middot; {model} &middot; Rankings for {date:%B %d, %Y}", rankings,
                         f"/cbb/seasons/{season.year}/index.html", f"{label} standings", links, archive,
                         stylesheet=f"/cfb/assets/record-book.css?v={revision}", layout_class="basketball-home")
    return presentation.render(page.encode(), archive=False)
