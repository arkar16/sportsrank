"""Build the basketball record book from frozen weekly replays."""
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape

from cfb.archive_presentation import updated_time
from cfb.conference_pages import build_conference_pages, conference_route, projected_overall_records
from cfb.program_pages import build_program_pages
from cfb.reference_archive import Game, Season, Standing, season_team_routes
from cfb.reference_markup import PagePresentation
from cfb.season_pages import _document, _standings, _rankings as standings_table, _upsets

from site_navigation import masthead
from spread_cards import complete_basketball_card
from .source import visible_final
from .cors_v1 import _available
from .grade import grade_margins
from .game_excitement import (BaselineLedger, after_game, checkpoint_prior, guide as excitement_guide, local_date,
                              read_measurements, weekly_scores)
from cfb.excitement_pages import ESTIMATE_NOTE, aev_label, bev_label
from .performance import write_performance
from .progression import write_progression
from .team_schedule import EASTERN, conference_tipoff, projection_key

SECTIONS = (
    '<a href="/cbb/teams/index.html">Teams</a>'
    '<a href="/cbb/performance/index.html">Model performance</a>'
)


def season_name(year):
    return f"{year - 1}–{str(year)[-2:]}"


def team_label(name, team_id, logos, *, linked=True, year=None):
    label = escape(name)
    if team_id in logos:
        label = (f'<img class="school-mark" src="/cbb/assets/team-logos/{team_id}.png" '
                 f'alt="" width="20" height="20" loading="lazy">{label}')
    if linked and team_id is not None:
        dest = f"/cbb/teams/{team_id}/{year}.html" if year is not None else f"/cbb/teams/{team_id}/index.html"
        return f'<a href="{dest}">{label}</a>'
    return label


def _page(title, body, *, index=True):
    cls = "sr-report sr-report-index" if index else "sr-report"
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title} | SportsRank basketball</title>'
            '<link rel="stylesheet" href="/cfb/assets/report-pages.css">'
            '<link rel="stylesheet" href="/cfb/assets/record-book.css"></head><body>'
            f'{masthead(SECTIONS)}<main class="archive-content"><h1>{title}</h1>'
            f'<div class="{cls}">{body}</div></main></body></html>\n')


def _write(root, path, title, body, *, index=True):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_page(title, body, index=index), encoding="utf-8")


def _table(headers, rows):
    head = "".join(f'<th scope="col">{header}</th>' for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="table-scroll" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _season_link(year):
    return f'<a href="/cbb/seasons/{year}/index.html">{season_name(year)}</a>'


def _week_route(year, week):
    slug = "final" if week == "FINAL" else week
    return f"/cbb/seasons/{year}/weeks/{slug}.html"


def _week_link(year, week, label, *, current=False):
    selected = ' aria-current="page"' if current else ""
    return f'<a href="{_week_route(year, week)}"{selected}>{label}</a>'


def _week_label(week):
    if week == "FINAL":
        return "Final"
    return "Preseason" if week == 0 else f"Week {week}"


def _checkpoint_week(checkpoint):
    if checkpoint == "PRESEASON":
        return 0
    if checkpoint == "FINAL":
        return checkpoint
    if checkpoint.startswith("W") and checkpoint[1:].isdigit():
        return int(checkpoint[1:])
    return None


def _best_checkpoint_label(checkpoint):
    week = _checkpoint_week(checkpoint)
    return _week_label(week) if week is not None else checkpoint.title()


def _checkpoint_nav(year, week_numbers, *, finals=False, current=None):
    items = [f'<li>{_week_link(year, w, _week_label(w), current=(current == w))}</li>' for w in week_numbers]
    if finals:
        items.append(f'<li>{_week_link(year, "FINAL", "Final", current=(current == "FINAL"))}</li>')
    return f'<nav aria-label="Ranking checkpoints"><ul class="week-links">{"".join(items)}</ul></nav>'


def _record(row):
    return f'{row["wins"]}–{row["losses"]}'


def _tipoff(game):
    return datetime.fromisoformat(game["start_utc"]).astimezone(EASTERN)


def _has_score(game, as_of):
    # Historical data gives tipoff but no completion time. Match the replay buffer.
    return visible_final(game, as_of)


def _pick(game):
    margin = game["home_margin"]
    if margin == 0:
        return "Pick’em"
    return f'{escape(game["home_team"] if margin > 0 else game["away_team"])} by {abs(margin):.2f}'


def _games(games, logos, as_of, *, year=None, date_only=False, excitement=None):
    if not games:
        return "<p>No Division I games in this period.</p>"
    rows = []
    has_estimate = False
    for g in games:
        score = f'{g["away_points"]}–{g["home_points"]}' if _has_score(g, as_of) else "—"
        when = _tipoff(g).strftime("%b %d, %Y") if (date_only or g.get("date_only")) else _tipoff(g).strftime("%b %d, %Y · %I:%M %p")
        cells = (
            when,
            team_label(g["away_team"], g["away_team_id"], logos, year=year),
            team_label(g["home_team"], g["home_team_id"], logos, year=year),
            "Neutral" if g["neutral_site"] else "Home", _pick(g), score,
        )
        if excitement is not None:
            appeal = excitement.get(g["game_id"])
            cells += (bev_label(appeal), aev_label(appeal))
            if appeal and appeal.estimated:
                has_estimate = True
        rows.append(cells)
    first_col = "Date" if date_only else "Tipoff (Eastern)"
    headers = (first_col, "Away", "Home", "Court", "Reconstructed pick", "Final (away–home)")
    if excitement is not None:
        headers += ("BEV", "AEV")
    return _table(headers, rows) + (f"<p>{ESTIMATE_NOTE}</p>" if has_estimate else "")


def _grade_row(label, pairs):
    if not pairs:
        return (label, 0, "—", "—", "—", 0)
    grade = grade_margins(pairs)
    accuracy = "—" if not grade.picks else f"{grade.winner_accuracy:.1%}"
    return (label, grade.games, f"{grade.rmse:.2f}", f"{grade.correct_picks}/{grade.picks}",
            accuracy, grade.pick_ems)


def _standings_table(rankings, key, prior, routes, rows, names, year):
    values = {names[r["team_id"]]: (f'{r["mov"]:+.2f}', f'{r["sos"]:.2f}') for r in rows}
    conferences = {r.conference: conference_route(r.conference, year, sport="basketball")
                   for r in rankings[key] if r.conference and r.conference != "—"}
    display_key = "PRESEASON" if key == "W0" else key
    display_prior = "PRESEASON" if prior == "W0" else prior
    return standings_table(rankings[key], rankings.get(prior, []), display_prior, display_key, routes,
                           conference_links=conferences, extra_columns=("Average margin", "Schedule strength"), extra_values=values)


def _season_notes(cutoff_utc):
    stamp = updated_time(cutoff_utc, "Rankings updated") or escape(cutoff_utc)
    return f'<div id="season-notes"><p>{stamp}</p></div>' if stamp else ""


def conference_game(game, week, names):
    """Adapt a replayed game to the shared conference record book."""
    return Game(
        week, names[game["home_team_id"]], names[game["away_team_id"]],
        game["home_points"], game["away_points"], -Decimal(str(game["home_margin"])),
        game["neutral_site"], conference_game=game.get("conference_game"),
        postseason=bool(game.get("season_type") == "postseason" or game.get("tournament")
                        or game.get("conference_tournament") or game.get("ncaa_round")),
        home_cors_delta=game.get("home_cors_delta"), away_cors_delta=game.get("away_cors_delta"),
        **conference_tipoff(_tipoff(game), time_known=not game.get("date_only")),
    )


def conference_slate(forecasts, checkpoint, names):
    """Use the known weekly slate when no dated schedule is available."""
    games = {}
    for row in forecasts:
        if row["cutoff_utc"] != checkpoint["cutoff_utc"]:
            continue
        game = conference_game(row, checkpoint["week"], names)
        key = projection_key(_tipoff(row).date(), game.home, game.away, game.neutral_site)
        games.setdefault(key, replace(game, home_score=None, away_score=None, home_handicap=None,
                                      home_cors_delta=None, away_cors_delta=None))
    return list(games.values())


def build(
    root,
    seasons,
    as_of,
    logos,
    *,
    tournament_years=(),
    model="CORS v0.4.0",
    schedules=None,
    upcoming_ids=(),
    conference_schedule=(),
    current_excitement=None,
    supplemental=None,
):
    """Export only checkpoints available by as_of and their corresponding slates."""
    archives, names = [], {}
    program_archives = list(archives)
    conference_archives = list(archives)
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

    def publish(path, markup, *, sortable=False):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(presentation.render(markup.encode(), archive=False, sortable=sortable))

    performance_records = []
    fixtures = defaultdict(lambda: defaultdict(list))
    season_index = []
    latest_season_year = seasons[-1]["season"]
    projections = None
    weekly_years: set[int] = set()
    excitement_logs = {}
    card_dates = {p.name for p in (root / "picks").glob("*") if p.is_dir()}
    for season in seasons:
        year = season["season"]
        date_only = bool(season.get("date_only"))
        checkpoints = [c for c in season["rankings"] if datetime.fromisoformat(c["cutoff_utc"]) <= as_of]
        if not checkpoints:
            continue
        last = checkpoints[-1]
        weeks = {c["cutoff_utc"]: c["week"] for c in checkpoints}
        week_numbers = list(weeks.values())
        rankings = {}
        for c in checkpoints:
            rankings[f'W{c["week"]}'] = [
                Standing(names[r["team_id"]], r["rank"], Decimal(f'{r["cors"]:.2f}'),
                         _record(r), r["conference"] or "—") for r in c["rows"]]
        finals = year < latest_season_year and season["final_cutoff_utc"] in weeks
        if finals:
            rankings["FINAL"] = rankings[f'W{weeks[season["final_cutoff_utc"]]}']
        games = []
        logged_games = []
        conference_games = []
        slates = defaultdict(list)
        weekly_games = defaultdict(list)
        evaluated = []
        pending = []
        scores = None
        guide_rows = []
        if model == "CORS v1.0.0":
            scores, future = weekly_scores(root, season, checkpoints, as_of, schedules)
            excitement_logs[year] = dict(future)
            guide_rows.extend((local_date(g.start_utc), g.away, g.home, g.week, score) for g, score in future.items())
            if current_excitement is not None and year == latest_season_year:
                current_excitement.update(scores)
        for g in season["forecasts"]:
            if g["cutoff_utc"] not in weeks:
                continue
            week = weeks[g["cutoff_utc"]]
            slates[week].append(g)
            visible = _has_score(g, as_of)
            if not date_only and not g.get("date_only"):
                fixture = (_tipoff(g).strftime("%b %d, %I:%M %p"), g["away_team"], g["home_team"])
                fixtures[year][fixture].append((g, visible))
            if visible:
                evaluated.append((week, g))
            elif g["cutoff_utc"] == last["cutoff_utc"]:
                pending.append((week, g))
            tip_text = _tipoff(g).strftime("%b %d") if date_only or g.get("date_only") else _tipoff(g).strftime("%b %d · %I:%M %p ET")
            game = Game(week, names[g["home_team_id"]], names[g["away_team_id"]],
                        g["home_points"] if visible else None, g["away_points"] if visible else None,
                        -Decimal(str(g["home_margin"])), g["neutral_site"], tip_text, start_utc=g["start_utc"])
            if scores is not None and (score := scores.get(g["game_id"])):
                excitement_logs[year][game] = score
                day = local_date(g["start_utc"])
                if score.final and day in card_dates:
                    complete_basketball_card(root, day, game, score)
                if visible or year != latest_season_year or g["game_id"] not in upcoming_ids:
                    guide_rows.append((day, game.away, game.home, week, score))
            games.append(game)
            if visible:
                result_cutoff = g.get("result_cutoff_utc") or _available(datetime.fromisoformat(g["start_utc"]))
                if result_cutoff in weeks:
                    conference_games.append(conference_game(g, week, names))
            if year != latest_season_year or g["game_id"] not in upcoming_ids:
                logged_games.append(game)
            weekly_games[week].append(game)

        if scores is not None and supplemental and year in supplemental:
            baselines = BaselineLedger(root, year)
            evidence = read_measurements(year)
            covered_fixtures = {str(g["game_id"]) for g in season["forecasts"] if g["cutoff_utc"] in weeks}
            if year == latest_season_year:
                covered_fixtures.update(map(str,upcoming_ids))
            directory_by_cutoff = {c["cutoff_utc"]: {r["team_id"]: r for r in c["rows"]} for c in checkpoints}
            for g in supplemental[year]:
                stamp = datetime.fromisoformat(g["start_utc"])
                # Previously issued weeks cannot see results or fixtures from a later checkpoint.
                checkpoint = next((c for c in reversed(checkpoints) if datetime.fromisoformat(c["cutoff_utc"]) < stamp), None)
                if checkpoint is None:
                    checkpoint = {"week": 0, "cutoff_utc": (stamp - timedelta(microseconds=1)).isoformat()}
                week = checkpoint["week"]
                day = local_date(g["start_utc"])
                home, away = g["home_team"], g["away_team"]
                if str(g["game_id"]) in covered_fixtures:
                    continue
                basis = checkpoint_prior(directory_by_cutoff.get(checkpoint["cutoff_utc"], {}), g["home_team_id"],
                                         g["away_team_id"], g["neutral_site"], checkpoint,
                                         field_size=len(checkpoints[0]["rows"]))
                bev = baselines.capture(checkpoint["cutoff_utc"], g["game_id"], g["home_team_id"], g["away_team_id"], day, basis)
                score = after_game(g, bev, {}, evidence, as_of, prior=basis)
                h, a = score.final or (None, None)
                game = Game(week, home, away, h, a, None, g["neutral_site"], start_utc=g["start_utc"])
                if score.final:
                    logged_games.append(game)
                guide_rows.append((day, away, home, week, score))
                excitement_logs[year][game] = score
            baselines.write()
        season_obj = Season(year, rankings, games, last["cutoff_utc"])
        season_routes = season_team_routes(season_obj, routes)
        archives.append(season_obj)
        if year == latest_season_year and not finals:
            conference_games.extend(list(conference_schedule) or conference_slate(season["forecasts"], last, names))
        conference_archive = replace(season_obj, games=conference_games)
        conference_archives.append(conference_archive)
        if year == latest_season_year and schedules is not None:
            projections = projected_overall_records(conference_archive, sport="basketball")
        program_archives.append(replace(season_obj, games=logged_games))
        performance_records.append({
            "year": year,
            "evaluated": evaluated,
            "pending": pending,
            "excitement": scores,
            "date_only": date_only,
        })
        season_index.append((
            _season_link(year),
            len(last["rows"]),
            len(checkpoints),
            len(evaluated),
            f'<a href="/cbb/performance/{year}/index.html">Report</a>',
        ))

        metadata = _season_notes(last["cutoff_utc"])
        if scores is not None and guide_rows:
            guide_title = f"{season_name(year)} basketball game excitement"
            body = excitement_guide(year, guide_rows, season_name, lambda n: team_label(n, ids.get(n), logos, year=year),
                                    _week_link, _table, as_of)
            _write(root, f"seasons/{year}/excitement/index.html", guide_title, body)
        for c in checkpoints:
            week = c["week"]
            key = f"W{week}"
            previous = f"W{week - 1}" if week else None
            label = _week_label(week)
            title = f"{season_name(year)} basketball · {label}"
            table = _standings_table(rankings, key, previous, season_routes, c["rows"], names, year)
            upsets = _upsets(weekly_games[week], season_routes)
            standings = _standings("CORS rankings", table, upsets)
            nav = _checkpoint_nav(year, week_numbers, finals=finals, current=week)
            week_jumps = [
                f'{_season_link(year)} season',
                '<a href="#cors-upsets">Upsets</a>',
                '<a href="#games">Scores and forecasts</a>',
            ]
            body = (
                f'<p>{" · ".join(week_jumps)}</p>'
                + nav + '<div id="rankings">' + standings + "</div>"
                + '<h2 id="games">Scores and reconstructed forecasts</h2>'
                + _games(slates[week], logos, as_of, year=year, date_only=date_only, excitement=scores)
            )
            if scores is not None and guide_rows:
                body += '<p><a href="../excitement/index.html">Game excitement and scoring method</a></p>'
            publish(f"seasons/{year}/weeks/{week}.html", _document(title, title, body, metadata=_season_notes(c["cutoff_utc"])))
        weekly_years.add(year)

        if finals:
            final_title = f"{season_name(year)} basketball · Final"
            final_table = _standings_table(rankings, "FINAL", "W0", season_routes, season["final_rankings"], names, year)
            final_standings = _standings("Final CORS rankings", final_table, _upsets(games, season_routes))
            final_nav = _checkpoint_nav(year, week_numbers, finals=True, current="FINAL")
            final_jumps = [
                f'{_season_link(year)} season',
                '<a href="#cors-upsets">Upsets</a>',
                f'<a href="/cbb/seasons/{year}/progression.html">Ranking progression</a>',
                f'<a href="/cbb/performance/{year}/index.html">Model performance</a>',
            ]
            publish(
                f"seasons/{year}/weeks/final.html",
                _document(final_title, final_title, f'<p>{" · ".join(final_jumps)}</p>' + final_nav + final_standings, metadata=metadata),
            )

        key = next(reversed(rankings))
        prior = "W0" if finals else (f"W{week_numbers[-2]}" if len(week_numbers) > 1 else None)
        title = f"{season_name(year)} Division I basketball"
        table = _standings_table(rankings, key, prior, season_routes, season["final_rankings"] if finals else last["rows"], names, year)
        last_week = week_numbers[-1]
        standings = _standings(
            f'{"Final" if finals else _week_label(last_week)} CORS rankings',
            table,
            _upsets(games if finals else weekly_games[last_week], season_routes),
        )
        nav = _checkpoint_nav(year, week_numbers, finals=finals)
        matchups_nav = ""
        if not finals:
            matchup_links = []
            if last_week > 0:
                matchup_links.append(f'<li><a href="/cbb/seasons/{year}/weeks/{last_week - 1}.html#games">Week {last_week - 1} scores</a></li>')
            matchup_links.append(f'<li><a href="/cbb/seasons/{year}/weeks/{last_week}.html#games">{_week_label(last_week)} games</a></li>')
            if year == latest_season_year:
                matchup_links.append(f'<li><a href="/cbb/current.html#forecasts">Week {last_week} forecasts</a></li>')
            matchups_nav = '<nav aria-label="Games and forecasts"><ul class="week-links">' + "".join(matchup_links) + "</ul></nav>"

        jumps = [
            '<a href="#cors-upsets">Upsets</a>',
            '<a href="#season-archive">Season archive</a>',
            f'<a href="/cbb/seasons/{year}/progression.html">Ranking progression</a>',
            f'<a href="/cbb/performance/{year}/index.html">Model performance</a>',
        ]
        if year in tournament_years:
            jumps.append(f'<a href="/cbb/tournaments/{year}.html">March Madness</a>')

        general_archive = [
            f'<li><a href="/cbb/seasons/{year}/progression.html">Ranking progression</a></li>',
            f'<li><a href="/cbb/performance/{year}/index.html">Model performance</a></li>',
        ]
        if finals:
            general_archive.append(f'<li>{_week_link(year, "FINAL", "Final rankings")}</li>')
        if 0 in week_numbers:
            general_archive.append(f'<li>{_week_link(year, 0, "Preseason rankings")}</li>')
        archive = (
            '<section id="season-archive"><h2>Season archive</h2>'
            f'<ul class="week-links">{"".join(general_archive)}</ul>'
            + "".join(
                f'<h3>{_week_label(w)}</h3><ul class="week-links"><li>{_week_link(year, w, "Rankings")}</li>'
                f'<li><a href="/cbb/seasons/{year}/weeks/{w}.html#games">Scores and forecasts</a></li></ul>'
                for w in reversed(week_numbers)
            )
            + "</section>"
        )
        if scores is not None and guide_rows:
            jumps.append('<a href="excitement/index.html">Game excitement</a>')
        publish(
            f"seasons/{year}/index.html",
            _document(
                title, title,
                nav + matchups_nav + f'<p>{" · ".join(jumps)}</p>' + standings + archive,
                metadata=metadata,
            ),
        )
        write_progression(
            root, year, season_name(year), checkpoints, rankings, season_routes, presentation,
            model=model, finals=finals,
        )

    if not archives:
        raise ValueError("No basketball checkpoints available at this publication cutoff")

    history_note = ('<p>Seasons and game results come from retained CollegeBasketballData API responses. '
                    'Each season lists the games available in the archive. '
                    '<a href="https://api.collegebasketballdata.com/data-availability">Provider coverage</a></p>')
    _write(
        root, "seasons/index.html", "Basketball season archive",
        history_note + _table(("Season", "Teams", "Weekly checkpoints", "Completed games", "Performance"), reversed(season_index)),
    )

    def checkpoint_route(year, checkpoint):
        week = _checkpoint_week(checkpoint)
        if year in weekly_years and week is not None:
            return _week_route(year, week)
        return f"/cbb/seasons/{year}/index.html"

    for path, markup in build_program_pages(
        program_archives, routes, sport="basketball", remaining_schedules=schedules, projections=projections,
        directory_route="/cbb/teams/index.html",
        season_route=lambda y: f"/cbb/seasons/{y}/index.html",
        year_label=season_name,
        directory_label=lambda school: team_label(school, ids[school], logos),
        checkpoint_route=checkpoint_route,
        best_checkpoint_label=_best_checkpoint_label,
        excitement_by_year=excitement_logs,
    ):
        sortable = path.endswith("/index.html") and path != "cbb/teams/index.html"
        publish(path.removeprefix("cbb/"), markup, sortable=sortable)
    for path, markup in build_conference_pages(
        conference_archives, routes, sport="basketball", year_label=season_name,
        season_route=lambda y: f"/cbb/seasons/{y}/index.html",
    ):
        publish(path.removeprefix("cbb/"), markup, sortable="data-sortable" in markup)

    editions = []
    for path in sorted((root / "editions").glob("*.html"), reverse=True):
        stamp = datetime.strptime(path.stem.removesuffix("-cbbd"), "%Y-%m-%dT%H%M%SZ").replace(tzinfo=UTC)
        if stamp <= as_of:
            editions.append((stamp, path))
    write_performance(
        root, performance_records, editions, fixtures, as_of, tournament_years, model, logos,
        season_name=season_name, team_label=team_label, write_page=_write,
    )
    return archives, routes, presentation
