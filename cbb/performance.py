"""Static model performance index, per-season reports, and CSV/JSON exports for basketball."""
from collections import defaultdict
import csv
from datetime import datetime
from html import escape
import json
from zoneinfo import ZoneInfo
from cfb.excitement_pages import ESTIMATE_NOTE, aev_label

from .grade import grade_game, grade_margins, grade_performance
from .issued import VERIFIED_EDITIONS, grade_edition

EASTERN = ZoneInfo("America/New_York")

_SCORING_RULES = (
    "<details><summary>Scoring rules</summary><p>Only games between Division I teams count. "
    "Each margin uses the Monday ratings preceding tipoff. CORS line coverage is the percentage "
    "of favorites that beat the CORS spread, excluding pushes. An even spread (pick’em) has no "
    "winner or coverage pick. Mean absolute error (MAE) and root mean square error (RMSE) include "
    "all evaluated games, including pick’ems. Lower margin error is better.</p></details>"
)


def _pct(value):
    return "—" if value is None else f"{value:.1%}"


def _num(value):
    return "—" if value is None else f"{value:.2f}"


def _metrics_card(grade):
    if not grade.games:
        return '<p class="metrics"><strong>0 Division I matchups</strong> · No graded games yet.</p>'
    pick_ems = f"; {grade.pick_ems:,} pick’em" + ("s" if grade.pick_ems != 1 else "") if grade.pick_ems else ""
    pushes = f"; {grade.pushes:,} push" + ("es" if grade.pushes != 1 else "") if grade.pushes else ""
    return (
        f'<p class="metrics"><strong>{grade.games:,} Division I matchup'
        f'{"s" if grade.games != 1 else ""}</strong> · '
        f'Winner accuracy {_pct(grade.winner_accuracy)} '
        f'({grade.wins:,}–{grade.losses:,}{pick_ems})<br>'
        f'CORS line coverage {_pct(grade.line_coverage)} '
        f'({grade.covers:,}–{grade.no_covers:,}{pushes})<br>'
        f'MAE {_num(grade.mae)} points · RMSE {_num(grade.rmse)} points</p>'
    )


def _summary_cells(label, grade):
    return (
        label,
        f"{grade.games:,}",
        f"{grade.wins:,}",
        f"{grade.losses:,}",
        f"{grade.pick_ems:,}",
        _pct(grade.winner_accuracy),
        f"{grade.covers:,}",
        f"{grade.no_covers:,}",
        f"{grade.pushes:,}",
        _pct(grade.line_coverage),
        _num(grade.mae),
        _num(grade.rmse),
    )


def _table(headers, rows, *, numeric_from=1):
    head = "".join(
        f'<th scope="col"{" class=\"num\"" if i >= numeric_from else ""}>{header}</th>'
        for i, header in enumerate(headers)
    )
    body = "".join(
        "<tr>" + "".join(
            f'<td{" class=\"num\"" if i >= numeric_from else ""}>{cell}</td>'
            for i, cell in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    return f'<div class="table-scroll" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _grade_row_issued(label, pairs):
    if not pairs:
        return (label, 0, "—", "—", "—", 0)
    grade = grade_margins(pairs)
    accuracy = "—" if not grade.picks else f"{grade.winner_accuracy:.1%}"
    return (label, grade.games, f"{grade.rmse:.2f}", f"{grade.correct_picks}/{grade.picks}",
            accuracy, grade.pick_ems)


def _write_csv(path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)


def _season_grade_dict(grade):
    return {
        "games": grade.games,
        "wins": grade.wins,
        "losses": grade.losses,
        "pick_ems": grade.pick_ems,
        "winner_accuracy": round(grade.winner_accuracy, 6) if grade.winner_accuracy is not None else None,
        "covers": grade.covers,
        "no_covers": grade.no_covers,
        "pushes": grade.pushes,
        "line_coverage": round(grade.line_coverage, 6) if grade.line_coverage is not None else None,
        "mae": round(grade.mae, 4) if grade.mae is not None else None,
        "rmse": round(grade.rmse, 4) if grade.rmse is not None else None,
    }


def _date_text(game):
    tip = datetime.fromisoformat(game["start_utc"]).astimezone(EASTERN)
    if game.get("date_only"):
        return tip.strftime("%b %d, %Y")
    return tip.strftime("%b %d, %Y · %I:%M %p")


def _checkpoint_label(week):
    return "Preseason" if week == 0 else f"Week {week}"


def write_performance(
    root,
    season_records,
    editions,
    fixtures,
    as_of,
    tournament_years,
    model,
    logos,
    *,
    season_name,
    team_label,
    write_page,
):
    """Write performance/index.html, seasons.csv, and per-season reports/CSVs/JSONs."""
    summary_headers = (
        "Season", "Games", "Wins", "Losses", "Pick’ems", "Winner accuracy",
        "Covers", "No covers", "Pushes", "Line coverage", "MAE (pts)", "RMSE (pts)",
    )
    all_pairs = []
    season_rows = []
    csv_seasons = []

    for record in season_records:
        year = record["year"]
        label = season_name(year)
        evaluated = record["evaluated"]
        pending = record["pending"]
        appeal = record.get("excitement")
        date_only = record["date_only"]
        pairs = [(g["home_margin"], g["actual_home_margin"]) for _, g in evaluated]
        all_pairs.extend(pairs)
        season_grade = grade_performance(pairs)
        season_rows.append((year, season_grade))
        csv_seasons.append((
            year, label, season_grade.games, season_grade.wins, season_grade.losses,
            season_grade.pick_ems,
            f"{season_grade.winner_accuracy:.6f}" if season_grade.winner_accuracy is not None else "",
            season_grade.covers, season_grade.no_covers, season_grade.pushes,
            f"{season_grade.line_coverage:.6f}" if season_grade.line_coverage is not None else "",
            f"{season_grade.mae:.4f}" if season_grade.mae is not None else "",
            f"{season_grade.rmse:.4f}" if season_grade.rmse is not None else "",
        ))

        by_week = defaultdict(list)
        for week, g in evaluated:
            by_week[week].append(g)
        weekly_rows_html = []
        weekly_rows_csv = []
        weekly_json = []
        for week in sorted(by_week):
            wpairs = [(g["home_margin"], g["actual_home_margin"]) for g in by_week[week]]
            wgrade = grade_performance(wpairs)
            wlink = f'<a href="/cbb/seasons/{year}/weeks/{week}.html">{_checkpoint_label(week)}</a>'
            weekly_rows_html.append(_summary_cells(wlink, wgrade))
            weekly_rows_csv.append((
                week, _checkpoint_label(week), wgrade.games, wgrade.wins, wgrade.losses,
                wgrade.pick_ems,
                f"{wgrade.winner_accuracy:.6f}" if wgrade.winner_accuracy is not None else "",
                wgrade.covers, wgrade.no_covers, wgrade.pushes,
                f"{wgrade.line_coverage:.6f}" if wgrade.line_coverage is not None else "",
                f"{wgrade.mae:.4f}" if wgrade.mae is not None else "",
                f"{wgrade.rmse:.4f}" if wgrade.rmse is not None else "",
            ))
            weekly_json.append({"week": week, "checkpoint": _checkpoint_label(week), **_season_grade_dict(wgrade)})

        game_rows_html = []
        game_rows_csv = []
        game_json = []
        for week, g in evaluated:
            margin = g["home_margin"]
            actual = g["actual_home_margin"]
            su, cov, err = grade_game(margin, actual)
            handicap = -margin
            pred_winner = "Pick’em" if margin == 0 else g["home_team"] if margin > 0 else g["away_team"]
            pred_id = None if margin == 0 else g["home_team_id"] if margin > 0 else g["away_team_id"]
            pred_cell = (
                "Pick’em" if margin == 0
                else team_label(pred_winner, pred_id, (), year=year)
            )
            when = _date_text(g)
            score = appeal.get(g["game_id"]) if appeal is not None else None
            game_rows_html.append((
                f'<a href="/cbb/seasons/{year}/weeks/{week}.html">{week}</a>',
                when,
                team_label(g["home_team"], g["home_team_id"], logos, year=year),
                team_label(g["away_team"], g["away_team_id"], logos, year=year),
                "Neutral" if g["neutral_site"] else "Home",
                _checkpoint_label(week),
                f"{handicap:+.2f}" if handicap else "0.00",
                pred_cell,
                str(g["home_points"]),
                str(g["away_points"]),
                f"{actual:+d}",
                su,
                cov,
                f"{err:.2f}",
            ) + ((score.bev if score else "—", aev_label(score)) if appeal is not None else ()))
            game_rows_csv.append((
                week, g["start_utc"], g["home_team"], g["away_team"], g["neutral_site"],
                _checkpoint_label(week), f"{handicap:.2f}", pred_winner,
                g["home_points"], g["away_points"], actual, su, cov, f"{err:.2f}", "Evaluated",
            ))
            game_json.append({
                "week": week, "start_utc": g["start_utc"], "home": g["home_team"], "away": g["away_team"],
                "neutral_site": g["neutral_site"], "checkpoint": _checkpoint_label(week),
                "home_handicap": round(handicap, 2), "predicted_winner": pred_winner,
                "home_score": g["home_points"], "away_score": g["away_points"],
                "actual_home_margin": actual, "straight_up": su, "cors_line_coverage": cov,
                "absolute_error": round(err, 2), "status": "Evaluated",
            })

        pending_html = ""
        pending_json = []
        if pending:
            prows = []
            for week, g in pending:
                margin = g["home_margin"]
                handicap = -margin
                pred_winner = "Pick’em" if margin == 0 else g["home_team"] if margin > 0 else g["away_team"]
                pred_id = None if margin == 0 else g["home_team_id"] if margin > 0 else g["away_team_id"]
                pred_cell = "Pick’em" if margin == 0 else team_label(pred_winner, pred_id, (), year=year)
                score = appeal.get(g["game_id"]) if appeal is not None else None
                prows.append((
                    f'<a href="/cbb/seasons/{year}/weeks/{week}.html">{week}</a>',
                    _date_text(g),
                    team_label(g["home_team"], g["home_team_id"], logos, year=year),
                    team_label(g["away_team"], g["away_team_id"], logos, year=year),
                    "Neutral" if g["neutral_site"] else "Home",
                    f"{handicap:+.2f}" if handicap else "0.00",
                    pred_cell,
                ) + ((score.bev if score else "—",) if appeal is not None else ()))
                pending_json.append({
                    "week": week, "start_utc": g["start_utc"], "home": g["home_team"], "away": g["away_team"],
                    "neutral_site": g["neutral_site"], "home_handicap": round(handicap, 2),
                    "predicted_winner": pred_winner, "status": "Pending",
                })
            pending_html = (
                '<h2 id="pending-forecasts">Ungraded forecasts</h2>'
                '<p>Current-week forecasts awaiting final scores.</p>'
                + '<div class="sr-report-matchups">'
                + _table(("Week", "Tipoff (Eastern)", "Home", "Away", "Court", "CORS spread (home)", "Predicted winner")
                         + (("BEV",) if appeal is not None else ()),
                         prows, numeric_from=5)
                + "</div>"
            )

        season_dir = root / "performance" / str(year)
        _write_csv(
            season_dir / "weekly.csv",
            ("week", "checkpoint", "games", "wins", "losses", "pick_ems", "winner_accuracy",
             "covers", "no_covers", "pushes", "line_coverage", "mae", "rmse"),
            weekly_rows_csv,
        )
        _write_csv(
            season_dir / "games.csv",
            ("week", "start_utc", "home", "away", "neutral_site", "checkpoint", "home_handicap",
             "predicted_winner", "home_score", "away_score", "actual_home_margin",
             "straight_up", "cors_line_coverage", "absolute_error", "status"),
            game_rows_csv,
        )
        (season_dir / "report.json").write_text(
            json.dumps({
                "schema_version": "cbb-cors-performance/v1",
                "season": year,
                "season_label": label,
                "model": model,
                "summary": _season_grade_dict(season_grade),
                "weekly": weekly_json,
                "games": game_json,
                "upcoming": pending_json,
            }, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        jumps = ['<a href="#downloads">Downloads</a>']
        if pending:
            jumps.append('<a href="#pending-forecasts">Ungraded forecasts</a>')
        jumps.extend(('<a href="#weekly-performance">By week</a>', '<a href="#game-results">Games</a>'))
        time_col = "Date" if date_only else "Tipoff (Eastern)"
        body = (
            f'<nav class="archive-nav" aria-label="Archive">'
            f'<a href="/cbb/seasons/{year}/index.html">{escape(label)} season</a> · '
            f'<a href="/cbb/seasons/{year}/progression.html">Ranking progression</a> · '
            f'<a href="/cbb/performance/index.html">All seasons</a></nav>'
            f'<nav class="sr-report-nav" aria-label="On this page">{"".join(jumps)}</nav>'
            + _metrics_card(season_grade)
            + _SCORING_RULES
            + '<div class="sr-report-links" id="downloads"><p>'
              '<a href="games.csv">Games (CSV)</a> · '
              '<a href="weekly.csv">Weeks (CSV)</a> · '
              '<a href="report.json">Report (JSON)</a></p></div>'
            + pending_html
            + '<h2 id="weekly-performance">Weekly performance</h2>'
            + _table(("Week", *summary_headers[1:]), weekly_rows_html)
            + '<h2 id="game-results">Game results</h2><div class="sr-report-matchups">'
            + _table(
                ("Week", time_col, "Home", "Away", "Court", "Checkpoint", "CORS spread (home)",
                 "Predicted winner", "Home score", "Away score", "Actual margin",
                 "Straight up", "Line coverage", "Abs. error") + (("BEV", "AEV") if appeal is not None else ()),
                game_rows_html,
                numeric_from=6,
            )
            + "</div>"
            + (f"<p>{ESTIMATE_NOTE}</p>" if appeal and any(a.estimated for a in appeal.values()) else "")
        )
        write_page(root, f"performance/{year}/index.html", f"{label} basketball model performance", body, index=False)

    _write_csv(
        root / "performance/seasons.csv",
        ("season", "season_label", "games", "wins", "losses", "pick_ems", "winner_accuracy",
         "covers", "no_covers", "pushes", "line_coverage", "mae", "rmse"),
        csv_seasons,
    )

    overall = grade_performance(all_pairs)
    table_rows = [
        _summary_cells(f'<a href="/cbb/performance/{year}/index.html">{season_name(year)}</a>', grade)
        for year, grade in reversed(season_rows)
    ]
    table_rows.append(_summary_cells("All seasons", overall))
    latest_year = season_rows[-1][0]
    nav_items = ['<a href="#reconstructed">Season summaries</a>', '<a href="#issued">Dated pregame forecasts</a>']
    if tournament_years:
        nav_items.append('<a href="/cbb/tournaments/index.html">March Madness</a>')

    body = (
        f'<nav class="sr-report-nav" aria-label="Performance reports">{"".join(nav_items)}</nav>'
        + _metrics_card(overall)
        + f'<p><a href="/cbb/performance/{latest_year}/index.html">Current forecasts and results</a> · '
          f'<a href="seasons.csv">Seasons (CSV)</a> · '
          f'{len(season_rows)} seasons · Scores through {as_of.astimezone(EASTERN):%b %d, %Y at %H:%M %Z}.</p>'
        + _SCORING_RULES
        + f'<h2 id="reconstructed">Season summaries</h2><p>{escape(model)}, evaluated against final Division I scores. '
          'Select any season for weekly summaries, game-level results, and CSV/JSON downloads.</p>'
        + _table(summary_headers, table_rows)
    )
    if model == "CORS v0.6.0":
        body += (
            '<p>November 19–22 comparison on 153 games: the blend picked 100 winners '
            '(65.36%) with 13.48 points RMSE. The original v0.4.0 picked 90 (58.82%) '
            'with 16.77 points RMSE. '
            '<a href="https://github.com/arkar16/sportsrank/issues/100#issuecomment-7104093506">Experiment results</a></p>'
        )
    elif model == "CORS v1.0.0":
        body += (
            '<p>In the November 19–23 comparison across 153 games, margin RMSE was 13.25 points '
            'for v1.0.0, 13.48 for v0.6.0, and 16.77 for v0.4.0. Across 54,197 games in the '
            '2017–26 evaluation, RMSE was 11.40, 11.44, and 13.35 points respectively.</p>'
        )
    if tournament_years:
        body += '<p><a href="/cbb/tournaments/index.html">March Madness picks and model comparisons</a></p>'
    body += (
        '<h2 id="issued">Dated pregame forecasts</h2><p>Each edition is graded separately using its printed '
        'pick and margin. A game qualifies only if the edition was verified public before tipoff. '
        'Unmatched games and games awaiting a score stay ungraded.</p>'
    )
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
        graded, pending_count, late = grade_edition(path, verified, fixtures[season])
        grade = _grade_row_issued(label, graded)
        issued_rows.append((label, f'<a href="{url}">{verified:%b %d, %H:%M UTC}</a>',
                            grade[1], grade[2], grade[4], pending_count, late))
    body += _table(
        ("Edition", "Public verification", "Graded", "Margin RMSE", "Winner accuracy",
         "Pending or unmatched", "Already tipped"),
        issued_rows,
        numeric_from=2,
    )
    write_page(root, "performance/index.html", "Basketball model performance", body, index=True)
