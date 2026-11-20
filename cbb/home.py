"""Basketball inputs for the shared football landing-page layout."""
from collections import defaultdict
from datetime import datetime
from html import escape

from cfb.record_book import _last_week_game, home_document
from .record_book import EASTERN, season_name


def landing_page(archives, routes, presentation, checkpoint, edition_path, tournaments):
    season = archives[-1]
    week = checkpoint["week"]
    previous = week - 1
    prior = {r.school: r.rank for r in season.rankings.get(f"W{previous}", ())}
    results = defaultdict(list)
    for game in season.games:
        if game.week == previous and game.home_score is not None:
            for school in (game.home, game.away):
                results[school].append(_last_week_game(school, game, prior, routes))
    rows = []
    for r in season.rankings[f"W{week}"][:10]:
        result = "".join(results[r.school]) or ("No games yet" if not week else "No game listed")
        rows.append(f'<tr><td class="num">{r.rank}</td><th scope="row"><a href="{routes[r.school]}">'
                    f'{escape(r.school)}</a></th><td>{r.record}</td><td class="num">{r.rating:.2f}</td>'
                    f'<td class="home-result">{result}</td></tr>')
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
    page = home_document("College Basketball | SportsRank", "Men’s college basketball",
                         f"{label} Division I &middot; Rankings for {date:%B %d, %Y}", rows,
                         f"<small>Week {previous}</small>" if week else "",
                         f"/cbb/seasons/{season.year}/index.html", f"{label} standings", links, archive)
    return presentation.render(page.encode(), archive=False)
