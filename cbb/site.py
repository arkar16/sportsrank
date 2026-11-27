"""Render the public basketball page from a frozen weekly ranking."""
from datetime import datetime, timezone
from html import escape
from hashlib import sha256
from pathlib import Path
import shutil

from alltime_excitement import publish_pages
from excitement_grades import graded_number
from site_navigation import masthead
from spread_cards import ConferenceSlate, basketball_links
from spread_preview import refresh_retained_previews
from .cors_v1 import VERSION, _available, cors_v1_seasons, version_for_edition
from .source import supplemental_fixtures
from .api_history import championship_finals, materialize
from zoneinfo import ZoneInfo
from tempfile import TemporaryDirectory
from .public_tree import ISSUED, install_pages, record_artifacts, restore_artifacts
from .game_excitement import basketball_bev, frozen_excitement_seasons
from .home import landing_page
from .honors import write_honors
from .march_archive import build_tournaments
from .record_book import SECTIONS, _games, _tipoff, _write, build, conference_game, season_name, team_label
from .team_schedule import schedule_views


V1_START = datetime.fromisoformat("2026-11-24T00:00:00+00:00")
BEV_EDITION_START = datetime.fromisoformat("2026-11-26T00:00:00+00:00")
# This dated edition must still reproduce its original published fields.
LAST_NUMBER_ONLY_EDITION = datetime.fromisoformat("2026-11-26T16:47:00+00:00")


def share_conferences(season, checkpoint):
    """Attach fixture classification and league records available at the edition cutoff."""
    cutoff = datetime.fromisoformat(checkpoint["cutoff_utc"])
    names = {row["team_id"]: row["school"] for row in checkpoint["rows"]}
    conferences = {row["school"]: row["conference"] for row in checkpoint["rows"]}
    completed, fixtures = [], {}
    for row in season["forecasts"]:
        if row["home_team_id"] not in names or row["away_team_id"] not in names:
            continue
        if row["cutoff_utc"] == checkpoint["cutoff_utc"]:
            game = conference_game(row, checkpoint["week"], names)
            fixtures[_tipoff(row).date().isoformat(), game.home, game.away] = game
        elif row["status"] == "final":
            available = row.get("result_cutoff_utc") or _available(datetime.fromisoformat(row["start_utc"]))
            if datetime.fromisoformat(available) <= cutoff:
                completed.append(conference_game(row, checkpoint["week"], names))
    return ConferenceSlate.build(conferences, completed, fixtures)


def _forecasts(forecasts, prepared_at, logo_ids, current, *, year=None):
    forecast_rows = []
    has_bev = any("bev" in row for row in forecasts)
    for row in forecasts:
        margin = row["predicted_home_margin"]
        pick = "Pick’em" if margin == 0 else escape(row["home"] if margin > 0 else row["away"])
        bev = row.get("bev", "—")
        if bev != "—" and (current or prepared_at > LAST_NUMBER_ONLY_EDITION):
            bev = graded_number("cbb", "BEV", bev)
        forecast_rows.append(
            f'<tr><td>{escape(row["tipoff"])}</td><td>{team_label(row["away"], row.get("away_id"), logo_ids, linked=current, year=year if current else None)}</td>'
            f'<td>{team_label(row["home"], row.get("home_id"), logo_ids, linked=current, year=year if current else None)}</td><td>{pick}</td><td class="num">{abs(margin):.2f}</td>'
            + (f'<td class="num">{bev}</td>' if has_bev else "") + "</tr>"
        )
    section_id = ' id="forecasts"' if current else ""
    forecast_section = ""
    if forecast_rows:
        forecast_section = f"""
<section{section_id}><h2>Game forecasts</h2><p>Prepared {prepared_at:%B %d, %Y at %H:%M %Z}. Tipoffs reflect the schedule at preparation.</p><div class="table-scroll"><table>
<thead><tr><th>Tipoff (Eastern)</th><th>Away</th><th>Home</th><th>CORS pick</th><th>Margin</th>{"<th>BEV</th>" if has_bev else ""}</tr></thead>
<tbody>{''.join(forecast_rows)}</tbody></table></div></section>"""
    return forecast_section


def render_rankings(season, cutoff, rankings, forecasts, prepared_at, *, logo_ids=frozenset(), current=False, tournaments=False):
    """Rows use team, wins, losses, rating; forecast margins favor the home team."""
    cutoff_local = datetime.fromisoformat(cutoff)
    edition = f"{season - 1}–{str(season)[-2:]}"
    ranking_rows = []
    for row in rankings:
        ranking_rows.append(
            f'<tr><td class="num">{row["rank"]}</td><th scope="row">{team_label(row["team"], row.get("team_id"), logo_ids, linked=current)}</th>'
            f'<td>{row["wins"]}–{row["losses"]}</td><td class="num">{row["rating"]:.2f}</td></tr>'
        )
    forecast_shortcut = '<p><a href="#forecasts">Upcoming game forecasts</a></p>' if current and forecasts else ""
    if current and tournaments:
        forecast_shortcut += '<p><a href="/cbb/tournaments/index.html">March Madness picks and results</a></p>'
    forecast_section = _forecasts(forecasts, prepared_at, logo_ids, current)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{edition} men's college basketball rankings | SportsRank</title>
<link rel="stylesheet" href="/cfb/assets/record-book.css"></head><body>
{masthead(SECTIONS if current else "")}
<main><h1>Men’s college basketball</h1>
<p class="edition">{edition} Division I · Rankings for {cutoff_local:%B %d, %Y}</p>{forecast_shortcut}
<section><h2>CORS rankings</h2><p>Records count games between Division I teams. Ratings update on Mondays.</p>
<div class="table-scroll"><table><thead><tr><th scope="col">Rank</th><th scope="col">Team</th>
<th scope="col">Record</th><th scope="col">CORS rating</th></tr></thead>
<tbody>{''.join(ranking_rows)}</tbody></table></div></section>{forecast_section}
</main></body></html>
"""


def main():
    import argparse


    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--as-of", required=True, help="Snapshot preparation time, with timezone")
    parser.add_argument("--output", type=Path, required=True, help="Basketball website directory")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="sportsrank-basketball-") as scratch:
        work = Path(scratch)
        database = work / "api.sqlite3"
        years = materialize(args.database, database, args.season)
        site = work / "site"
        # Render a complete API-backed site before retiring any old public output.
        site.mkdir()
        as_of = datetime.fromisoformat(args.as_of)
        restore_artifacts(args.output, site, as_of)
        _build_api_site(database, site, args, years[0])
        refresh_retained_previews(site, "cbb")
        record_artifacts(site, as_of)
        install_pages(site, args.output)
        publish_pages(args.output, "cbb")


def _build_api_site(database, output, args, first_season):
    from .replay import replay_seasons

    as_of = datetime.fromisoformat(args.as_of)
    if as_of.utcoffset() is None:
        raise ValueError("--as-of requires a timezone")
    seasons = replay_seasons(database, args.season, first_season=first_season)
    baseline = seasons
    blended = as_of >= V1_START
    model = version_for_edition(as_of) if blended else "CORS v0.4.0"
    if blended:
        seasons = cors_v1_seasons(database, baseline, api_only=True, model=model)
    season = seasons[-1]
    checkpoints = [c for c in season["rankings"] if datetime.fromisoformat(c["cutoff_utc"]) <= as_of]
    if not checkpoints:
        # The published schedule can arrive before opening week. Its preseason
        # ratings depend only on the returning roster and preceding seasons.
        checkpoint = {**season["rankings"][0], "cutoff_utc": as_of.astimezone(timezone.utc).isoformat()}
        season = {**season, "rankings": (checkpoint, *season["rankings"])}
        seasons = (*seasons[:-1], season)
        checkpoints = [checkpoint]
    checkpoint = checkpoints[-1]
    cutoff = checkpoint["cutoff_utc"]
    et = ZoneInfo("America/New_York")
    edition_games = []
    frozen = frozen_excitement_seasons(database, baseline, seasons) if model == VERSION else {}
    excitement_season = frozen.get(season["season"], season)
    excitement_checkpoint = next((c for c in excitement_season["rankings"] if c["cutoff_utc"] == cutoff), checkpoint)
    ranks = {r["team_id"]: r["rank"] for r in excitement_checkpoint["rows"]}
    excitement_margins = {g["game_id"]: g["home_margin"] for g in excitement_season["forecasts"] if g["cutoff_utc"] == cutoff}
    for row in season["forecasts"]:
        tipoff = datetime.fromisoformat(row["start_utc"])
        if row["cutoff_utc"] == cutoff and (blended or (row["status"] == "scheduled" and tipoff > as_of)):
            edition_games.append({"tipoff": tipoff.astimezone(et).strftime("%b %d" if row.get("date_only") else "%b %d, %I:%M %p"),
                             "home": row["home_team"], "away": row["away_team"],
                             "home_id": row["home_team_id"], "away_id": row["away_team_id"],
                             "predicted_home_margin": row["home_margin"]})
            if blended and as_of >= BEV_EDITION_START:
                edition_games[-1]["bev"] = basketball_bev(ranks[row["home_team_id"]], ranks[row["away_team_id"]],
                                                          excitement_margins[row["game_id"]], row["game_id"], cutoff)
    rankings = [{"rank": r["rank"], "team": r["school"], "team_id": r["team_id"], "rating": r["cors"],
                 "wins": r["wins"], "losses": r["losses"]} for r in checkpoint["rows"]]
    logo_dir = Path(__file__).resolve().parents[1] / "website/cbb/assets/team-logos"
    output_logos = output / "assets/team-logos"
    if output_logos.resolve() != logo_dir:
        shutil.copytree(logo_dir, output_logos, dirs_exist_ok=True)
    logo_ids = {int(path.stem) for path in logo_dir.glob("*.png")}
    page = render_rankings(args.season, cutoff, rankings, edition_games, as_of.astimezone(et),
                           logo_ids=logo_ids)
    if blended:
        page = page.replace("Division I · Rankings", f"Division I · {model} · Rankings")
    stamp = as_of.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H%M%SZ")
    issued = f"editions/{stamp}.html"
    archive = output / (issued if ISSUED.get(issued) == sha256(page.encode()).hexdigest() else f"editions/{stamp}-cbbd.html")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists() and archive.read_bytes() != page.encode():
        raise ValueError("API edition changed at the same preparation time")
    archive.write_text(page, encoding="utf-8")
    years = build_tournaments(output, baseline, as_of, logo_ids, blends=seasons if blended else (), model=model, api_only=True)
    schedules, upcoming_ids, conference_schedule = (
        schedule_views(database, season, checkpoint, as_of) if blended else (None, set(), [])
    )
    current_excitement = {} if blended else None
    archives, routes, presentation = build(
        output, seasons, as_of, logo_ids, tournament_years=years, model=model,
        schedules=schedules, upcoming_ids=upcoming_ids, conference_schedule=conference_schedule,
        current_excitement=current_excitement, frozen_excitement=frozen,
        supplemental=(supplemental_fixtures(database, [s["season"] for s in seasons])
                      if current_excitement is not None else None),
    )
    write_honors(output, archives, routes, presentation, as_of, championship_games=championship_finals(database, as_of))
    edition_link = f'<nav class="sr-report-nav"><a href="editions/{archive.name}">Dated edition</a></nav>'
    report = _forecasts(edition_games, as_of.astimezone(et), logo_ids, True, year=args.season)
    if not report:
        report = '<p id="forecasts">No upcoming games in this edition.</p>'
    if blended:
        slate = [g for g in season["forecasts"] if g["cutoff_utc"] == cutoff]
        report = (f'<section id="forecasts"><h2>{model} weekly picks</h2>'
                  + _games(slate, logo_ids, as_of, year=args.season, excitement=current_excitement) + '</section>')
        if current_excitement:
            report += f'<p><a href="/cbb/seasons/{args.season}/excitement/index.html">Game excitement and scoring method</a></p>'
    _write(output, "current.html", f"{season_name(args.season)} basketball forecasts", edition_link + report)
    card_urls = {}
    if blended and slate:
        basketball_links(output, share_conferences(season, checkpoint), card_urls=card_urls)
    home = landing_page(archives, routes, presentation, checkpoint, archive, years, model=model,
                        card_urls=card_urls)
    (output / "index.html").write_bytes(home)


if __name__ == "__main__":
    main()
