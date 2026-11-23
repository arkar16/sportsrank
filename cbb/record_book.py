"""Build the basketball record book from frozen weekly replays."""
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from html import escape
from decimal import Decimal

from cfb.conference_pages import build_conference_pages, conference_route
from cfb.program_pages import build_program_pages
from cfb.reference_archive import Game, Season, Standing, season_team_routes
from cfb.reference_markup import PagePresentation
from cfb.season_pages import _document, _standings, _rankings as standings_table, _upsets
from zoneinfo import ZoneInfo

from site_navigation import masthead
from .archive_finals import read_archive
from .grade import grade_margins
from .issued import VERIFIED_EDITIONS, grade_edition

EASTERN = ZoneInfo("America/New_York")
SECTIONS = (
    '<a href="/cbb/teams/index.html">Teams</a>'
    '<a href="/cbb/performance/index.html">Model performance</a>'
)


def season_name(year):
    return f"{year - 1}–{str(year)[-2:]}"


def team_label(name, team_id, logos, *, linked=True):
    label = escape(name)
    if team_id in logos:
        label = (f'<img class="school-mark" src="/cbb/assets/team-logos/{team_id}.png" '
                 f'alt="" width="20" height="20" loading="lazy">{label}')
    if linked and team_id is not None:
        return f'<a href="/cbb/teams/{team_id}/index.html">{label}</a>'
    return label


def _page(title, body):
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title} | SportsRank basketball</title>'
            '<link rel="stylesheet" href="/cfb/assets/report-pages.css">'
            '<link rel="stylesheet" href="/cfb/assets/record-book.css"></head><body>'
            f'{masthead(SECTIONS)}<main class="archive-content"><h1>{title}</h1>'
            f'<div class="sr-report sr-report-index">{body}</div></main></body></html>\n')


def _write(root, path, title, body):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_page(title, body), encoding="utf-8")


def _table(headers, rows):
    head = ''.join(f'<th scope="col">{header}</th>' for header in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
    return f'<div class="table-scroll" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _season_link(year):
    return f'<a href="/cbb/seasons/{year}/index.html">{season_name(year)}</a>'


def _week_link(year, week, label):
    return f'<a href="/cbb/seasons/{year}/weeks/{week}.html">{label}</a>'


def _record(row):
    return f'{row["wins"]}–{row["losses"]}'


def _tipoff(game):
    return datetime.fromisoformat(game["start_utc"]).astimezone(EASTERN)


def _has_score(game, as_of):
    # Historical data gives tipoff but no completion time. Match the replay buffer.
    return game["status"] == "final" and _tipoff(game) + timedelta(hours=6) < as_of


def _pick(game):
    margin = game["home_margin"]
    if margin == 0:
        return "Pick’em"
    return f'{escape(game["home_team"] if margin > 0 else game["away_team"])} by {abs(margin):.2f}'


def _games(games, logos, as_of):
    if not games:
        return '<p>No Division I games in this period.</p>'
    rows = []
    for g in games:
        score = f'{g["away_points"]}–{g["home_points"]}' if _has_score(g, as_of) else '—'
        rows.append((
            _tipoff(g).strftime("%b %d, %Y · %I:%M %p"),
            team_label(g["away_team"], g["away_team_id"], logos),
            team_label(g["home_team"], g["home_team_id"], logos),
            "Neutral" if g["neutral_site"] else "Home", _pick(g), score,
        ))
    return _table(("Tipoff (Eastern)", "Away", "Home", "Court", "Reconstructed pick", "Final (away–home)"), rows)


def _grade_row(label, pairs):
    if not pairs:
        return (label, 0, "—", "—", "—", 0)
    grade = grade_margins(pairs)
    accuracy = "—" if not grade.picks else f'{grade.winner_accuracy:.1%}'
    return (label, grade.games, f'{grade.rmse:.2f}', f'{grade.correct_picks}/{grade.picks}',
            accuracy, grade.pick_ems)


def _performance(root, by_season, editions, fixtures, as_of, tournament_years, model):
    headers = ("Season", "Graded games", "Margin RMSE (points)", "Correct / picks", "Winner accuracy", "Pick’ems")
    pairs = [pair for _, season_pairs in by_season for pair in season_pairs]
    rows = [_grade_row(_season_link(year), season_pairs) for year, season_pairs in reversed(by_season)]
    rows.append(_grade_row("All seasons", pairs))
    body = ('<nav class="sr-report-nav" aria-label="Performance reports"><a href="#reconstructed">Season summaries</a>'
            '<a href="#issued">Dated pregame forecasts</a></nav>' + f'<p>Scores through {as_of.astimezone(EASTERN):%b %d, %Y at %H:%M %Z}.</p>'
            f'<h2 id="reconstructed">Reconstructed forecasts</h2><p>{model}, evaluated against final Division I scores. '
            'Each margin uses the Monday ratings preceding tipoff. RMSE includes pick’ems. Winner accuracy '
            'counts games with a predicted winner.</p>' + _table(headers, rows))
    if model == "CORS v0.6.0":
        body += ('<p>November 19–22 comparison on 153 games: the blend picked 100 winners '
                 '(65.36%) with 13.48 points RMSE. The original v0.4.0 picked 90 (58.82%) '
                 'with 16.77 points RMSE. <a href="https://github.com/arkar16/sportsrank/issues/100#issuecomment-7104093506">Experiment results</a></p>')
    if tournament_years:
        body += '<p><a href="/cbb/tournaments/index.html">March Madness picks and model comparisons</a></p>'
    body += ('<h2 id="issued">Dated pregame forecasts</h2><p>Each edition is graded separately using its printed '
             'pick and margin. A game qualifies only if the edition was verified public before tipoff. '
             'Unmatched games and games awaiting a score stay ungraded.</p>')
    issued_rows = []
    for stamp, path in editions:
        label = f'<a href="/cbb/editions/{path.name}">{stamp:%b %d, %Y · %H:%M UTC}</a>'
        evidence = VERIFIED_EDITIONS.get(path.stem)
        if evidence is None:
            issued_rows.append((label, "Awaiting publication verification", "—", "—", "—", "—", "—"))
            continue
        verified_at, url = evidence
        verified = datetime.fromisoformat(verified_at)
        season = stamp.year + int(stamp.month >= 7)
        graded, pending, late = grade_edition(path, verified, fixtures[season])
        grade = _grade_row(label, graded)
        issued_rows.append((label, f'<a href="{url}">{verified:%b %d, %H:%M UTC}</a>',
                            grade[1], grade[2], grade[4], pending, late))
    body += _table(("Edition", "Public verification", "Graded", "Margin RMSE", "Winner accuracy",
                    "Pending or unmatched", "Already tipped"), issued_rows)
    _write(root, "performance/index.html", "Basketball model performance", body)


def _standings_table(rankings, key, prior, routes, rows, names, year):
    values = {names[r["team_id"]]: (f'{r["mov"]:+.2f}', f'{r["sos"]:.2f}') for r in rows}
    conferences = {r.conference: conference_route(r.conference, year, sport="basketball")
                   for r in rankings[key] if r.conference and r.conference != "—"}
    return standings_table(rankings[key], rankings.get(prior, []), prior, key, routes,
                           conference_links=conferences, extra_columns=("Average margin", "Schedule strength"), extra_values=values)


def build(root, seasons, as_of, logos, *, tournament_years=(), model="CORS v0.4.0", schedules=None, upcoming_ids=(), include_early_finals=False):
    """Export only checkpoints available by as_of and their corresponding slates."""
    archives, names = read_archive(min(as_of.year + (as_of.month >= 7), seasons[0]["season"])) if include_early_finals else ([], {})
    program_archives = list(archives)
    for season in seasons:
        for c in season["rankings"]:
            if datetime.fromisoformat(c["cutoff_utc"]) <= as_of:
                names.update((r["team_id"], r["school"]) for r in c["rows"])
    routes = {name: f"/cbb/teams/{i}/index.html" for i, name in names.items()}
    if len(routes) != len(names):
        raise ValueError("Ambiguous basketball school name in the program directory")
    ids = {name: i for i, name in names.items()}
    marks = {name: f"/cbb/assets/team-logos/{i}.png" for i, name in names.items() if i in logos}
    presentation = PagePresentation(routes, marks, header=masthead(SECTIONS))

    def publish(path, markup):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(presentation.render(markup.encode(), archive=False))

    performance = []
    fixtures = defaultdict(lambda: defaultdict(list))
    season_index = []
    for season in archives:
        year = season.year
        final = season.rankings["FINAL"]
        conferences = {r.conference: conference_route(r.conference, year, sport="basketball") for r in final}
        table = standings_table(final, [], None, "FINAL", season_team_routes(season, routes),
                                conference_links=conferences)
        title = f"{season_name(year)} Division I basketball"
        context = ('<p>Final CORS v0.6.0 standings. Records count games between Division I teams. '
                   '<a href="/cbb/seasons/index.html">Season archive</a></p>')
        body = '<div class="season-standings">' + _standings("Final CORS rankings", table, "") + "</div>"
        publish(f"seasons/{year}/index.html", _document(title, title, context + body))
        completed = sum(int(r.record.split("–")[0]) for r in final)
        season_index.append((_season_link(year), len(final), "Final only", completed))
    for season in seasons:
        year = season["season"]
        checkpoints = [c for c in season["rankings"] if datetime.fromisoformat(c["cutoff_utc"]) <= as_of]
        if not checkpoints:
            continue
        last = checkpoints[-1]
        weeks = {c["cutoff_utc"]: c["week"] for c in checkpoints}
        rankings = {}
        for c in checkpoints:
            rankings[f'W{c["week"]}'] = [
                Standing(names[r["team_id"]], r["rank"], Decimal(f'{r["cors"]:.2f}'),
                         _record(r), r["conference"] or "—") for r in c["rows"]]
        finals = year < seasons[-1]["season"] and season["final_cutoff_utc"] in weeks
        if finals:
            rankings["FINAL"] = rankings[f'W{weeks[season["final_cutoff_utc"]]}']
        games = []
        logged_games = []
        slates = defaultdict(list)
        weekly_games = defaultdict(list)
        pairs = []
        for g in season["forecasts"]:
            if g["cutoff_utc"] not in weeks:
                continue
            week = weeks[g["cutoff_utc"]]
            slates[week].append(g)
            visible = _has_score(g, as_of)
            fixture = (_tipoff(g).strftime("%b %d, %I:%M %p"), g["away_team"], g["home_team"])
            fixtures[year][fixture].append((g, visible))
            if visible:
                pairs.append((g["home_margin"], g["actual_home_margin"]))
            game = Game(week, names[g["home_team_id"]], names[g["away_team_id"]],
                        g["home_points"] if visible else None, g["away_points"] if visible else None,
                        -Decimal(str(g["home_margin"])), g["neutral_site"],
                        _tipoff(g).strftime("%b %d · %I:%M %p ET"))
            games.append(game)
            if year != seasons[-1]["season"] or g["game_id"] not in upcoming_ids:
                logged_games.append(game)
            weekly_games[week].append(game)
        archives.append(Season(year, rankings, games, last["cutoff_utc"]))
        program_archives.append(replace(archives[-1], games=logged_games))
        performance.append((year, pairs))
        season_index.append((_season_link(year), len(last["rows"]), len(checkpoints), len(pairs)))
        week_links = "".join(f'<li>{_week_link(year, w, "Preseason" if w == 0 else f"Week {w}")}</li>'
                             for w in weeks.values())
        nav = f'<nav aria-label="Ranking checkpoints"><ul class="week-links">{week_links}</ul></nav>'
        for c in checkpoints:
            week = c["week"]
            key = f"W{week}"
            previous = f"W{week-1}" if week else None
            label = "Preseason" if week == 0 else f"Week {week}"
            title = f"{season_name(year)} basketball · {label}"
            table = _standings_table(rankings, key, previous, routes, c["rows"], names, year)
            standings = _standings("CORS rankings", table, _upsets(weekly_games[week], routes))
            body = (f'<p>{_season_link(year)} season · <a href="#games">Scores and forecasts</a></p>'
                    + nav + '<div id="rankings">' + standings + "</div>" + '<h2 id="games">Scores and reconstructed forecasts</h2>'
                    + _games(slates[week], logos, as_of))
            publish(f"seasons/{year}/weeks/{week}.html", _document(title, title, body))
        key = next(reversed(rankings))
        prior = "W0" if finals else previous
        title = f"{season_name(year)} Division I basketball"
        table = _standings_table(rankings, key, prior, routes, season["final_rankings"] if finals else last["rows"], names, year)
        standings = _standings(f'{"Final" if finals else label} CORS rankings', table,
                               _upsets(games if finals else weekly_games[week], routes))
        jumps = '<a href="#season-archive">Season archive</a>'
        if year in tournament_years:
            jumps += f' · <a href="/cbb/tournaments/{year}.html">March Madness</a>'
        archive = '<section id="season-archive"><h2>Season archive</h2>' + "".join(
            f'<h3>Week {w}</h3><ul class="week-links"><li>{_week_link(year, w, "Rankings")}</li>'
            f'<li><a href="/cbb/seasons/{year}/weeks/{w}.html#games">Scores and forecasts</a></li></ul>'
            for w in reversed(weeks.values())) + '</section>'
        publish(f"seasons/{year}/index.html", _document(title, title, nav + f"<p>{jumps}</p>" + standings + archive))
    if not archives:
        raise ValueError("No basketball checkpoints available at this publication cutoff")
    history_note = ""
    if include_early_finals:
        history_note = ('<p>Final standings for 1984–85 through 2018–19 use Division I results and the '
                        'CORS v0.6.0 model. Teams start level in 1984–85. The weekly archive begins in '
                        '2019–20 with its original preseason ratings. '
                        '<a href="https://github.com/Nate-Cheney/March-Machine-Learning-Mania-2026/tree/'
                        'b12a81c1201bb88b1d2010210116559d1fa03851/raw_data">Historical game data</a></p>')
    _write(root, "seasons/index.html", "Basketball season archive",
           history_note + _table(("Season", "Teams", "Weekly checkpoints", "Completed games"), reversed(season_index)))
    for path, markup in build_program_pages(program_archives, routes, sport="basketball", remaining_schedules=schedules, directory_route="/cbb/teams/index.html",
                                            season_route=lambda y: f"/cbb/seasons/{y}/index.html", year_label=season_name,
                                            directory_label=lambda school: team_label(school, ids[school], logos)):
        publish(path.removeprefix("cbb/"), markup)
    for path, markup in build_conference_pages(archives, routes, sport="basketball", year_label=season_name,
                                               season_route=lambda y: f"/cbb/seasons/{y}/index.html"):
        publish(path.removeprefix("cbb/"), markup)
    editions = []
    for path in sorted((root / "editions").glob("*.html"), reverse=True):
        stamp = datetime.strptime(path.stem, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
        if stamp <= as_of:
            editions.append((stamp, path))
    _performance(root, performance, editions, fixtures, as_of, tournament_years, model)
    return archives, routes, presentation
