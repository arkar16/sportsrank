"""Build the static program record book from the published college football archive."""
import argparse
from collections import defaultdict
from html import escape
import os
from pathlib import Path
import tempfile

from .conference_pages import build_conference_pages
from .history_sheets import render_history_sheet
from .program_marks import program_logo_paths
from .program_pages import build_program_pages
from .reference_archive import Game, Season, final_history_rows, program_routes, read_archive, season_team_routes
from .reference_markup import PagePresentation, school_label
from .release import _page, _row_table
from .season_pages import build_season_pages


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


def _last_week_game(school: str, game: Game, ranks: dict[str, int], routes: dict[str, str]) -> str:
    if school == game.home:
        opponent, site = game.away, "vs"
        scored, allowed = game.home_score, game.away_score
    else:
        opponent, site = game.home, "@"
        scored, allowed = game.away_score, game.home_score
    if game.neutral_site:
        site = "vs"
    rank = f"#{ranks[opponent]} " if opponent in ranks else ""
    neutral = " (neutral)" if game.neutral_site else ""
    label = school_label(opponent, routes)
    matchup = f'<span class="home-opponent">{rank}{label}{neutral}</span>'
    outcome = ""
    if scored is None or allowed is None:
        score = '<span class="home-game-score">No final score</span>'
    else:
        letter, meaning = ("W", "Win") if scored > allowed else ("L", "Loss") if scored < allowed else ("T", "Tie")
        outcome = f'<abbr title="{meaning}">{letter}</abbr> '
        score = f'<strong class="home-game-score">{scored}–{allowed}</strong>'
    return f'<div class="home-game"><span>{outcome}{site}</span> {matchup} {score}</div>'


def _home(seasons: list[Season], routes: dict[str, str], *, is_root: bool) -> str:
    latest = seasons[-1]
    program_count = len(routes)
    routes = season_team_routes(latest, routes)
    key = next(reversed(latest.rankings))
    edition = "Final" if key == "FINAL" else "Preseason" if key == "PRESEASON" else f"Through Week {key[1:]}"
    year_link = f"/cfb/years/{latest.year}/{latest.year}_CFB.html"
    week = None
    if key == "FINAL":
        week = max((game.week for game in latest.games
                    if game.home_score is not None and game.away_score is not None), default=None)
    elif key != "PRESEASON":
        week = int(key[1:])
    pregame_ranks = {}
    games_by_school = defaultdict(list)
    if week is not None:
        # Rankings are in checkpoint order. Use the last edition before kickoff week.
        prior = next((rows for checkpoint, rows in reversed(latest.rankings.items())
                      if checkpoint == "PRESEASON" or (checkpoint.startswith("W") and int(checkpoint[1:]) < week)), ())
        pregame_ranks = {row.school: row.rank for row in prior}
        for game in latest.games:
            if game.week == week:
                games_by_school[game.home].append(game)
                games_by_school[game.away].append(game)
    empty_result = "No games yet" if key == "PRESEASON" else "No game listed"
    week_label = f"<small>Week {week}</small>" if week is not None else ""
    top = []
    for entry in sorted(latest.rankings[key], key=lambda row: row.rank)[:10]:
        games = games_by_school.get(entry.school, ())
        result = "".join(_last_week_game(entry.school, game, pregame_ranks, routes) for game in games)
        top.append(
            f'<tr><td class="num">{entry.rank}</td><th scope="row"><a href="{routes[entry.school]}">'
            f'{escape(entry.school)}</a></th><td>{escape(entry.record)}</td>'
            f'<td class="num">{entry.rating:.2f}</td>'
            f'<td class="home-result">{result or empty_result}</td></tr>'
        )
    decades = defaultdict(list)
    for season in reversed(seasons):
        year = season.year
        decades[year // 10 * 10].append(f'<a href="/cfb/years/{year}/{year}_CFB.html">{year}</a>')
    archive = "".join(f'<div><h3>{decade}s</h3><p>{" ".join(links)}</p></div>' for decade, links in decades.items())
    title = "SportsRank" if is_root else "College Football | SportsRank"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><link rel="stylesheet" href="/cfb/assets/record-book.css"></head>
<body><main><h1>College football</h1>
<p class="edition">{latest.year} FBS &middot; {edition}</p>
<div class="home-columns"><section><h2>CORS top {len(top)}</h2><div class="table-scroll">
<table><thead><tr><th scope="col" class="num">Rank</th><th scope="col">Team</th>
<th scope="col">Record</th><th scope="col" class="num">CORS rating</th>
<th scope="col" class="home-result">Last Week{week_label}</th></tr></thead>
<tbody>{''.join(top)}</tbody></table></div><p><a href="{year_link}">{latest.year} standings</a></p>
</section><section><h2>Record book</h2><p><a href="/cfb/teams/index.html">{program_count} program histories</a></p>
<p><a href="/cfb/conferences/index.html">Conference histories</a></p>
<p><a href="/cfb/years/history/nc_FBS_CFB_output.html">National champions</a></p>
<p><a href="/cfb/years/history/wt_FBS_CFB_output.html">Last-place finishers</a></p></section></div>
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
    for relative in ("index.html", "cfb/cfb.html"):
        markup = _home(seasons, routes, is_root=relative == "index.html")
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
