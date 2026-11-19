"""Render the public basketball page from a frozen weekly ranking."""
from datetime import datetime
from html import escape
from pathlib import Path
import shutil


def _team_label(name, team_id, logo_ids):
    label = escape(name)
    if team_id not in logo_ids:
        return label
    return (f'<img class="school-mark" src="/cbb/assets/team-logos/{team_id}.png" '
            f'alt="" width="20" height="20" loading="lazy">{label}')


def render_rankings(season, cutoff, rankings, forecasts, prepared_at, *, logo_ids=frozenset()):
    """Rows use team, wins, losses, rating; forecast margins favor the home team."""
    cutoff_local = datetime.fromisoformat(cutoff)
    edition = f"{season - 1}–{str(season)[-2:]}"
    ranking_rows = []
    for row in rankings:
        ranking_rows.append(
            f'<tr><td class="num">{row["rank"]}</td><th scope="row">{_team_label(row["team"], row.get("team_id"), logo_ids)}</th>'
            f'<td>{row["wins"]}–{row["losses"]}</td><td class="num">{row["rating"]:.2f}</td></tr>'
        )
    forecast_rows = []
    for row in forecasts:
        margin = row["predicted_home_margin"]
        pick = "Pick’em" if margin == 0 else escape(row["home"] if margin > 0 else row["away"])
        forecast_rows.append(
            f'<tr><td>{escape(row["tipoff"])}</td><td>{_team_label(row["away"], row.get("away_id"), logo_ids)}</td>'
            f'<td>{_team_label(row["home"], row.get("home_id"), logo_ids)}</td><td>{pick}</td><td class="num">{abs(margin):.2f}</td></tr>'
        )
    forecast_section = ""
    if forecast_rows:
        forecast_section = f"""
<section><h2>Game forecasts</h2><p>Prepared {prepared_at:%B %d, %Y at %H:%M %Z}. Tipoffs reflect the schedule at preparation.</p><div class="table-scroll"><table>
<thead><tr><th>Tipoff (Eastern)</th><th>Away</th><th>Home</th><th>CORS pick</th><th>Margin</th></tr></thead>
<tbody>{''.join(forecast_rows)}</tbody></table></div></section>"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{edition} men's college basketball rankings | SportsRank</title>
<link rel="stylesheet" href="/cfb/assets/record-book.css"></head><body>
<header class="masthead"><a class="nameplate" href="/">SportsRank</a><nav aria-label="Site"><a href="/cfb/cfb.html">College football</a></nav></header>
<main><h1>Men’s college basketball</h1>
<p class="edition">{edition} Division I · Rankings for {cutoff_local:%B %d, %Y}</p>
<section><h2>CORS rankings</h2><p>Records count games between Division I teams. Ratings update on Mondays.</p>
<div class="table-scroll"><table><thead><tr><th scope="col">Rank</th><th scope="col">Team</th>
<th scope="col">Record</th><th scope="col">CORS rating</th></tr></thead>
<tbody>{''.join(ranking_rows)}</tbody></table></div></section>{forecast_section}
</main></body></html>
"""


def main():
    import argparse
    from zoneinfo import ZoneInfo

    from .replay import replay_seasons

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--as-of", required=True, help="Snapshot preparation time, with timezone")
    parser.add_argument("--output", type=Path, required=True, help="Basketball website directory")
    args = parser.parse_args()
    as_of = datetime.fromisoformat(args.as_of)
    if as_of.utcoffset() is None:
        parser.error("--as-of requires a timezone")
    season = replay_seasons(args.database, args.season)[-1]
    checkpoints = [c for c in season["rankings"] if datetime.fromisoformat(c["cutoff_utc"]) <= as_of]
    if not checkpoints:
        parser.error("No ranking checkpoint precedes this timestamp")
    checkpoint = checkpoints[-1]
    cutoff = checkpoint["cutoff_utc"]
    et = ZoneInfo("America/New_York")
    upcoming = []
    for row in season["forecasts"]:
        tipoff = datetime.fromisoformat(row["start_utc"])
        if row["cutoff_utc"] == cutoff and row["status"] == "scheduled" and tipoff > as_of:
            upcoming.append({"tipoff": tipoff.astimezone(et).strftime("%b %d, %I:%M %p"),
                             "home": row["home_team"], "away": row["away_team"],
                             "home_id": row["home_team_id"], "away_id": row["away_team_id"],
                             "predicted_home_margin": row["home_margin"]})
    rankings = [{"rank": r["rank"], "team": r["school"], "team_id": r["team_id"], "rating": r["cors"],
                 "wins": r["wins"], "losses": r["losses"]} for r in checkpoint["rows"]]
    logo_dir = Path(__file__).resolve().parents[1] / "website/cbb/assets/team-logos"
    output_logos = args.output / "assets/team-logos"
    if output_logos.resolve() != logo_dir:
        shutil.copytree(logo_dir, output_logos, dirs_exist_ok=True)
    logo_ids = {int(path.stem) for path in logo_dir.glob("*.png")}
    page = render_rankings(args.season, cutoff, rankings, upcoming, as_of.astimezone(et),
                           logo_ids=logo_ids)
    stamp = as_of.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H%M%SZ")
    archive = args.output / "editions" / f"{stamp}.html"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists() and archive.read_text() != page:
        raise ValueError("A different basketball edition exists for that timestamp")
    archive.write_text(page)
    current = page.replace("</main>", f'<p><a href="editions/{archive.name}">Dated edition</a></p></main>')
    (args.output / "index.html").write_text(current)


if __name__ == "__main__":
    main()
