"""Build the basketball record book from frozen weekly replays."""
from collections import defaultdict
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from site_navigation import masthead
from .grade import grade_margins
from .issued import VERIFIED_EDITIONS, grade_edition

EASTERN = ZoneInfo("America/New_York")
SECTIONS = (
    '<a href="/cbb/teams/index.html">Teams</a>'
    '<a href="/cbb/seasons/index.html">Seasons</a>'
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
            '<link rel="stylesheet" href="/cfb/assets/record-book.css"></head><body>'
            f'{masthead(SECTIONS)}<main><h1>{title}</h1>{body}</main></body></html>\n')


def _write(root, path, title, body):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_page(title, body), encoding="utf-8")


def _table(headers, rows):
    head = ''.join(f'<th scope="col">{header}</th>' for header in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _season_link(year):
    return f'<a href="/cbb/seasons/{year}/index.html">{season_name(year)}</a>'


def _week_link(year, week, label):
    return f'<a href="/cbb/seasons/{year}/weeks/{week}.html">{label}</a>'


def _record(row):
    return f'{row["wins"]}–{row["losses"]}'


def _rankings(rows, logos):
    return _table(("Rank", "Team", "Record", "CORS", "Average margin", "Schedule strength"), (
        (r["rank"], team_label(r["school"], r["team_id"], logos), _record(r),
         f'{r["cors"]:.2f}', f'{r["mov"]:+.2f}', f'{r["sos"]:.2f}') for r in rows
    ))


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


def _performance(root, by_season, editions, fixtures, as_of):
    headers = ("Season", "Graded games", "Margin RMSE (points)", "Correct / picks", "Winner accuracy", "Pick’ems")
    pairs = [pair for _, season_pairs in by_season for pair in season_pairs]
    rows = [_grade_row(_season_link(year), season_pairs) for year, season_pairs in reversed(by_season)]
    rows.append(_grade_row("All seasons", pairs))
    body = (f'<p>Scores through {as_of.astimezone(EASTERN):%b %d, %Y at %H:%M %Z}.</p>'
            '<h2>Reconstructed forecasts</h2><p>CORS v0.4.0, evaluated against final Division I scores. '
            'Each margin uses the Monday ratings preceding tipoff. RMSE includes pick’ems. Winner accuracy '
            'counts games with a predicted winner.</p>' + _table(headers, rows))
    body += ('<h2>Dated pregame forecasts</h2><p>Each edition is graded separately using its printed '
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


def build(root, seasons, as_of, logos):
    """Export only checkpoints available by as_of and their corresponding slates."""
    history = defaultdict(list)
    latest_names = {}
    performance = []
    fixtures = defaultdict(lambda: defaultdict(list))
    season_index = []
    for season in seasons:
        year = season["season"]
        checkpoints = [c for c in season["rankings"] if datetime.fromisoformat(c["cutoff_utc"]) <= as_of]
        if not checkpoints:
            continue
        last = checkpoints[-1]
        slates = defaultdict(list)
        logs = defaultdict(list)
        pairs = []
        for g in season["forecasts"]:
            if g["cutoff_utc"] > last["cutoff_utc"]:
                continue
            slates[g["cutoff_utc"]].append(g)
            for key in ("away_team_id", "home_team_id"):
                logs[g[key]].append(g)
            key = (_tipoff(g).strftime("%b %d, %I:%M %p"), g["away_team"], g["home_team"])
            score_available = _has_score(g, as_of)
            fixtures[year][key].append((g, score_available))
            if score_available:
                pairs.append((g["home_margin"], g["actual_home_margin"]))
        performance.append((year, pairs))
        weeks = []
        trajectories = defaultdict(list)
        for c in checkpoints:
            date = datetime.fromisoformat(c["cutoff_utc"]).astimezone(EASTERN).strftime("%b %d, %Y")
            label = "Preseason" if c["week"] == 0 else f'Week {c["week"]}'
            weekly_games = slates[c["cutoff_utc"]]
            for row in c["rows"]:
                trajectories[row["team_id"]].append((_week_link(year, c["week"], label), date,
                                                     row["rank"], _record(row), f'{row["cors"]:.2f}'))
            games_link = f'<a href="/cbb/seasons/{year}/weeks/{c["week"]}.html#games">Scores and forecasts</a>'
            weeks.append((_week_link(year, c["week"], label), date, len(weekly_games), games_link))
            body = (f'<p>{_season_link(year)} · Ratings at {date}, 6 a.m. Eastern</p>'
                    '<p><a href="#rankings">Rankings</a> · <a href="#games">Scores and forecasts</a></p>'
                    '<h2 id="rankings">CORS rankings</h2>' + _rankings(c["rows"], logos)
                    + '<h2 id="games">Scores and reconstructed forecasts</h2>' + _games(weekly_games, logos, as_of))
            _write(root, f'seasons/{year}/weeks/{c["week"]}.html', f'{season_name(year)} basketball · {label}', body)
        # Use the checkpoint containing the last final for completed seasons. The active
        # season's latest eligible Monday can precede games in the current week.
        standings, rating_cutoff = last["rows"], last["cutoff_utc"]
        if year < seasons[-1]["season"] and season["final_cutoff_utc"] <= rating_cutoff:
            standings, rating_cutoff = season["final_rankings"], season["final_cutoff_utc"]
        rating_date = datetime.fromisoformat(rating_cutoff).astimezone(EASTERN).strftime("%b %d, %Y")
        season_index.append((_season_link(year), len(standings), len(checkpoints), len(pairs)))
        body = ('<h2>Weekly archive</h2>' + _table(("Checkpoint", "Monday", "Games on slate", "Game log"), reversed(weeks))
                + f'<h2>Season standings</h2><p>Records and ratings at {rating_date}.</p>' + _rankings(standings, logos))
        _write(root, f'seasons/{year}/index.html', f'{season_name(year)} basketball', body)
        for row in standings:
            team_id = row["team_id"]
            name = row["school"]
            latest_names[team_id] = name
            season_page = f'/cbb/teams/{team_id}/{year}.html'
            history[team_id].append((f'<a href="{season_page}">{season_name(year)}</a>', row["rank"],
                                     _record(row), f'{row["cors"]:.2f}'))
            body = (f'<p>{team_label(name, team_id, logos)} · {_season_link(year)}</p>'
                    f'<p>{rating_date} · Rank {row["rank"]} · {_record(row)} · CORS {row["cors"]:.2f}</p>'
                    '<h2>Game log</h2>' + _games(logs[team_id], logos, as_of)
                    + '<h2>Weekly rankings</h2>' + _table(("Checkpoint", "Monday", "Rank", "Record", "CORS"),
                                                          reversed(trajectories[team_id])))
            _write(root, f'teams/{team_id}/{year}.html', f'{escape(name)} · {season_name(year)}', body)
    if not history:
        raise ValueError("No basketball checkpoints available at this publication cutoff")
    _write(root, "seasons/index.html", "Basketball season archive",
           _table(("Season", "Teams", "Weekly checkpoints", "Completed games"), reversed(season_index)))
    ordered = sorted(latest_names, key=lambda i: (latest_names[i].casefold(), i))
    for team_id in ordered:
        body = _table(("Season and game log", "Rank", "Record", "CORS"), reversed(history[team_id]))
        _write(root, f'teams/{team_id}/index.html', f'{escape(latest_names[team_id])} basketball history', body)
    _write(root, "teams/index.html", "Basketball team histories",
           _table(("Program", "Seasons"), ((team_label(latest_names[i], i, logos), len(history[i])) for i in ordered)))
    editions = []
    for path in sorted((root / "editions").glob("*.html"), reverse=True):
        stamp = datetime.strptime(path.stem, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
        if stamp <= as_of:
            editions.append((stamp, path))
    _performance(root, performance, editions, fixtures, as_of)
