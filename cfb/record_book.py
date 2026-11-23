"""Build the static program record book from the published college football archive."""
import argparse
from collections import defaultdict
from hashlib import sha256
from html import escape
import os
from pathlib import Path
import tempfile

from .conference_pages import build_conference_pages, conference_route
from .history_sheets import render_history_sheet
from .program_marks import program_logo_paths
from .program_pages import build_program_pages
from .reference_archive import Game, Season, final_history_rows, program_routes, read_archive, season_team_routes
from .reference_markup import PagePresentation, school_label
from .release import _page, _row_table
from .season_pages import build_season_pages, weekly_forecast_path


def _write_changed(path: Path, content: bytes) -> bool:
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _home_game(
    school: str, game: Game, ranks: dict[str, int], routes: dict[str, str], *, forecast: bool = False,
) -> str:
    at_home = school == game.home
    opponent = game.away if at_home else game.home
    site = "vs" if at_home or game.neutral_site else "@"
    division = game.away_division if at_home else game.home_division
    opponent_prefix = f"#{ranks[opponent]} " if opponent in ranks else ""
    if division.strip().casefold() == "fcs":
        opponent_prefix = "(FCS) "
    neutral = " (neutral)" if game.neutral_site else ""
    label = school_label(opponent, routes)
    matchup = f'<span class="home-opponent">{opponent_prefix}{label}{neutral}</span>'
    outcome = ""
    if forecast:
        handicap = game.home_handicap
        if handicap is None or not handicap.is_finite():
            value = '<span class="home-game-score" title="Spread unavailable">--</span>'
        else:
            spread = handicap if at_home else handicap.copy_negate()
            line = format(spread.normalize(), "+f") if spread else "Pick’em"
            value = f'<strong class="home-game-score">{line}</strong>'
    else:
        scored, allowed = (game.home_score, game.away_score) if at_home else (game.away_score, game.home_score)
        if scored is None or allowed is None:
            value = '<span class="home-game-score">No final score</span>'
        else:
            letter, meaning = ("W", "Win") if scored > allowed else ("L", "Loss") if scored < allowed else ("T", "Tie")
            outcome = f'<abbr title="{meaning}">{letter}</abbr> '
            value = f'<strong class="home-game-score">{scored}–{allowed}</strong>'
    return f'<div class="home-game"><span>{outcome}{site}</span> {matchup} {value}</div>'


def _last_week_game(school: str, game: Game, ranks: dict[str, int], routes: dict[str, str]) -> str:
    return _home_game(school, game, ranks, routes)


def _home_table(rows: list[str], headings: str, label: str) -> str:
    return (
        f'<div class="table-scroll" role="region" aria-label="{label}" tabindex="0">'
        '<table class="home-rankings"><colgroup><col class="rank"><col class="team">'
        '<col><col class="change"><col class="record"><col class="rating"><col span="2"></colgroup>'
        f'<thead><tr>{headings}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _home_rankings(directory: Path, season: Season, routes: dict[str, str], key: str) -> str:
    if key == "FINAL":
        week = max((game.week for game in season.games
                    if game.home_score is not None and game.away_score is not None), default=None)
        next_week = None
    elif key == "PRESEASON":
        week = None
        next_week = min((game.week for game in season.games), default=None)
    else:
        week = int(key[1:])
        next_week = week + 1

    checkpoints = list(season.rankings)
    previous = checkpoints[-2] if len(checkpoints) > 1 else None
    previous_ranks = {row.school: row.rank for row in season.rankings.get(previous, ())}
    earlier = (
        next((rows for checkpoint, rows in reversed(season.rankings.items())
              if checkpoint == "PRESEASON" or (checkpoint.startswith("W") and int(checkpoint[1:]) < week)), ())
        if week is not None else ()
    )
    pregame_ranks = {row.school: row.rank for row in earlier}
    entries = sorted(season.rankings[key], key=lambda row: row.rank)
    games = defaultdict(list)
    for game in season.games:
        upcoming = game.week == next_week and (game.home_score is None or game.away_score is None)
        if game.week == week or upcoming:
            games[game.week, game.home].append(game)
            games[game.week, game.away].append(game)

    empty_result = "No games yet" if key == "PRESEASON" else "Bye"
    empty_forecast = "Season complete" if key == "FINAL" else "Bye"
    forecast_link = ""
    if next_week is not None:
        path = weekly_forecast_path(directory, season.year, next_week)
        if path:
            kind = "reconstructed forecasts" if path.startswith("reconstructed/") else "forecasts"
            forecast_link = f'<p><a href="/cfb/years/{season.year}/{path}">Week {next_week} {kind}</a></p>'
    return home_rankings(
        entries, routes, games, previous=previous, previous_ranks=previous_ranks, pregame_ranks=pregame_ranks,
        week=week, next_week=next_week, empty_result=empty_result, empty_forecast=empty_forecast,
        forecast_link=forecast_link, conference_year=season.year,
    )


def home_rankings(entries, routes, games, *, previous, previous_ranks, pregame_ranks, week, next_week,
                  empty_result, empty_forecast, forecast_link, conference_year=None):
    """Render both sports' Top 25, with the rest of the standings available on demand."""
    current_ranks = {row.school: row.rank for row in entries}
    rows = []
    for entry in entries:
        school = entry.school
        conference = escape(entry.conference or "—")
        if conference_year is not None and entry.conference:
            href = conference_route(entry.conference, conference_year)
            conference = f'<a href="{href}">{escape(entry.conference)}</a>'
        previous_rank = previous_ranks.get(school)
        change = "—"
        movement = ""
        if previous_rank is not None:
            delta = previous_rank - entry.rank
            change = f"{delta:+d}" if delta else "0"
            movement = " rise" if delta > 0 else " fall" if delta < 0 else ""
        last = "".join(_last_week_game(school, game, pregame_ranks, routes) for game in games[week, school])
        upcoming = "".join(
            _home_game(school, game, current_ranks, routes, forecast=True) for game in games[next_week, school]
        )
        rows.append(
            f'<tr><td class="num">{entry.rank}</td><th scope="row">{school_label(school, routes)}</th>'
            f'<td>{conference}</td><td class="num{movement}">{change}</td><td class="num">{escape(entry.record)}</td>'
            f'<td class="num">{entry.rating:.2f}</td><td class="home-result">{last or empty_result}</td>'
            f'<td class="home-result">{upcoming or empty_forecast}</td></tr>'
        )

    prior_label = ""
    if previous is not None:
        baseline = "Preseason" if previous == "PRESEASON" else f"Week {int(previous[1:])}"
        prior_label = f"<small>vs {baseline}</small>"
    last_label = f"<small>Week {week}</small>" if week is not None else ""
    next_label = f"Week {next_week} · CORS spread" if next_week is not None else "CORS spread"
    headings = (
        '<th scope="col" class="num">Rank</th><th scope="col">Team</th>'
        '<th scope="col">Conference</th>'
        f'<th scope="col" class="num rank-change">Rank change{prior_label}</th>'
        '<th scope="col" class="num">Record</th><th scope="col" class="num">CORS rating</th>'
        f'<th scope="col" class="home-result">Last Week{last_label}</th>'
        f'<th scope="col" class="home-result">Next Week<small>{next_label}</small></th>'
    )
    top = rows[:25]
    top_label = f"CORS top {len(top)}"
    tables = (
        f'<h2>{top_label}</h2>{forecast_link}<p class="home-scroll-hint">Scroll across for scores and spreads.</p>'
        + _home_table(top, headings, top_label)
    )
    if len(rows) > len(top):
        remaining = _home_table(rows[25:], headings, "Remaining CORS rankings")
        tables += f'<details class="home-all"><summary>Full rankings ({len(rows)} teams)</summary>{remaining}</details>'
    return tables


def _home(site: Path, seasons: list[Season], routes: dict[str, str], stylesheet_revision: str, *, is_root: bool) -> str:
    latest = seasons[-1]
    program_count = len(routes)
    routes = season_team_routes(latest, routes)
    key = next(reversed(latest.rankings))
    edition = "Final" if key == "FINAL" else "Preseason" if key == "PRESEASON" else f"Through Week {key[1:]}"
    year_link = f"/cfb/years/{latest.year}/{latest.year}_CFB.html"
    rankings = _home_rankings(site / f"cfb/years/{latest.year}", latest, routes, key)
    decades = defaultdict(list)
    for season in reversed(seasons):
        year = season.year
        decades[year // 10 * 10].append(f'<a href="/cfb/years/{year}/{year}_CFB.html">{year}</a>')
    archive = "".join(f'<div><h3>{decade}s</h3><p>{" ".join(links)}</p></div>' for decade, links in decades.items())
    title = "SportsRank" if is_root else "College Football | SportsRank"
    record_links = f"""<p><a href="/cfb/teams/index.html">{program_count} program histories</a></p>
<p><a href="/cfb/conferences/index.html">Conference histories</a></p>
<p><a href="/cfb/years/history/nc_FBS_CFB_output.html">National champions</a></p>
<p><a href="/cfb/years/history/wt_FBS_CFB_output.html">Last-place finishers</a></p>"""
    return home_document(
        title, "College football", f"{latest.year} FBS &middot; {edition}", rankings,
        year_link, f"{latest.year} standings", record_links, archive,
        stylesheet=f"/cfb/assets/record-book.css?v={stylesheet_revision}",
    )


def home_document(
    title: str, heading: str, edition: str, rankings: str, year_link: str, standings_label: str,
    record_links: str, archive: str, *, stylesheet: str, layout_class: str = "",
) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><link rel="stylesheet" href="{stylesheet}"></head>
<body><main><h1>{heading}</h1>
<p class="edition">{edition}</p>
<div class="home-columns{(" " + escape(layout_class)) if layout_class else ""}"><section>{rankings}<p><a href="{year_link}">{standings_label}</a></p>
</section><section><h2>Record book</h2>{record_links}</section></div>
<section><h2>Season archive</h2><div class="year-archive">{archive}</div></section>
</main></body></html>
"""


def _not_found(year: int) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>404 · Page not found | SportsRank</title><link rel="stylesheet" href="/cfb/assets/record-book.css"></head>
<body><main class="missing-page"><h1>Page not found</h1>
<ul class="recovery-links">
<li><a href="/cfb/years/{year}/{year}_CFB.html">{year} standings</a></li>
<li><a href="/cfb/teams/index.html">Find a team</a></li>
<li><a href="/cfb/cfb.html">Season archive</a></li>
</ul></main></body></html>
"""


def _history_sheet(path: Path, routes: dict[str, str], year_routes: dict[int, dict[str, str]], presentation: PagePresentation) -> int:
    # After the one-time conversion, the public original also supplies the next
    # offline build. A freshly exported table fragment takes precedence.
    source = path.with_suffix(".source.txt")
    current = path.read_bytes()
    if current.lstrip().startswith(b"<table"):
        original = current
    elif b'<h1 id="history-title">' in current:
        original = source.read_bytes()
    else:
        raise ValueError(f"unrecognized history sheet at {path}")
    markup = render_history_sheet(path.name, original, routes, set(year_routes), year_routes)
    changed = _write_changed(source, original)
    return changed + _write_changed(path, presentation.render(markup.encode(), archive=False, sortable=True))


def _honors_pages(site: Path, seasons: list[Season]) -> dict[str, bytes]:
    finals = [season for season in seasons if "FINAL" in season.rankings]
    standings = {season.year: final_history_rows(site, season.year) for season in finals}
    latest = finals[-1] if finals else seasons[-1]
    links = (("Season", f"/cfb/years/{latest.year}/{latest.year}_CFB.html"),)
    fields = ("school", "conference", "record", "win_pct", "cors")
    columns = ("year", *fields, "kind")
    pages = {}
    for filename, title, kind, place in (
        ("nc_FBS_CFB_output.html", "National Champions", "National champion", 0),
        ("wt_FBS_CFB_output.html", "Worst Teams", "Worst team", 1),
    ):
        rows = [
            {"year": year, "kind": kind, **{key: finishers[place][key] for key in fields}}
            for year, finishers in standings.items()
        ]
        markup = _page(title, latest.updated_at, _row_table(rows, columns), links).encode("utf-8")
        for directory in ("cfb/history", "cfb/years/history"):
            pages[f"{directory}/{filename}"] = markup
    return pages


def build(site: Path) -> tuple[int, int, int]:
    site = site.resolve()
    seasons = read_archive(site)
    if not seasons:
        raise ValueError(f"no college football rankings at {site}")
    honors = _honors_pages(site, seasons)
    routes = program_routes(seasons)
    marks = program_logo_paths(site)
    presentation = PagePresentation(routes, marks)
    year_routes = {s.year: season_team_routes(s, routes) for s in seasons}
    views = {str(year): PagePresentation(mapping, marks) for year, mapping in year_routes.items()}
    changed = 0
    for asset in (Path(__file__).parent / "assets").iterdir():
        changed += _write_changed(site / "cfb/assets" / asset.name, asset.read_bytes())
    for relative, markup in build_program_pages(seasons, routes):
        changed += _write_changed(site / relative, presentation.render(markup.encode(), archive=False))
    for relative, markup in build_conference_pages(seasons, routes):
        changed += _write_changed(site / relative, presentation.render(markup.encode(), archive=False))
    season_pages = set()
    for season in seasons:
        for relative, markup in build_season_pages(site, season, routes):
            path = site / relative
            changed += _write_changed(path, presentation.render(markup.encode(), archive=False))
            season_pages.add(path)
    stylesheet_revision = sha256((site / "cfb/assets/record-book.css").read_bytes()).hexdigest()[:12]
    for relative in ("index.html", "cfb/cfb.html"):
        markup = _home(site, seasons, routes, stylesheet_revision, is_root=relative == "index.html")
        changed += _write_changed(site / relative, presentation.render(markup.encode(), archive=False))
    changed += _write_changed(site / "404.html", presentation.render(_not_found(seasons[-1].year).encode(), archive=False))
    targets = {"/" + path.relative_to(site).as_posix() for path in site.rglob("*.html")}
    for relative, raw in honors.items():
        markup = presentation.render(raw, archive=True, relative=relative, targets=targets, sortable=True)
        changed += _write_changed(site / relative, markup)
    # Decorate source pages last. School text, scoring rows, existing links and
    # the exact input bytes used by forecast evaluation can all be recovered.
    for directory in ("years", "performance", "history"):
        for path in (site / "cfb" / directory).rglob("*.html"):
            if path in season_pages:
                continue
            relative = path.relative_to(site).as_posix()
            if relative in honors:
                continue
            if relative in {"cfb/years/history/CORS_NC_FBS_CFB.html", "cfb/years/history/CORS_WT_FBS_CFB.html"}:
                changed += _history_sheet(path, routes, year_routes, presentation)
                continue
            raw = path.read_bytes()
            view = views.get(path.relative_to(site / "cfb" / directory).parts[0], presentation)
            markup = view.render(raw, archive=True, relative=relative, targets=targets)
            if raw != markup:
                changed += _write_changed(path, markup)
    return len(seasons), len(routes), changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path("website"), help="generated static site root")
    args = parser.parse_args(argv)
    seasons, programs, changed = build(args.site)
    print(f"{seasons} seasons, {programs} programs, {changed} changed files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
