"""Historical football game guides from the earliest official CFBD games and season rosters."""

from html import escape
import json

from alltime_excitement import Game as LeaderGame
from .cfbd_appeal import DATA, load
from .excitement import QualifiedPregame, calculate_bev, matchup_quality
from .game_excitement import GameAppeal, _sparse_final
from .excitement_pages import aev_label, bev_label
from .sparse_excitement import pregame_prior


def early_seasons():
    data = json.loads((DATA / "early-games.json").read_bytes())
    if data.get("provider") != "CollegeFootballData":
        raise ValueError("unknown early football source")
    return {int(y): r for y, r in data["seasons"].items()}


def build_guides(*, leaders=None):
    from .cors_v1 import CompletedGame, SeasonInput, replay_seasons, OTHER_INIT_MEAN

    years = early_seasons()
    inputs = []
    for year, row in sorted(years.items()):
        members = {name for name, _ in row["teams"]}
        games = []
        for game in row["games"]:
            fields = list(game)
            for school, division in ((1, 2), (4, 5)):
                if fields[school] in members:
                    fields[division] = "fbs"
                elif fields[division] == "fbs":
                    fields[division] = ""  # The contemporary season roster governs CORS membership.
            games.append(CompletedGame(*fields))
        inputs.append(SeasonInput(year, tuple(map(tuple, row["teams"])), tuple(games)))
    replays, _ = replay_seasons(inputs)
    ref = load()
    for season, replay in zip(inputs, replays, strict=True):
        rows = []
        ledger = []
        candidates = []
        ranks = {-1: replay.preseason, **replay.weekly_rankings}
        spreads = {
            w: {(r["home_team"], r["away_team"], r["neutral_site"]): float(r["home_margin"]) for r in rr}
            for w, rr in replay.weekly_spreads.items()
        }
        for g in season.completed_games:
            cutoff = max((w for w in ranks if w < g.week), default=-1)
            teams = {r["school"]: (r["rank"], float(r["cors"])) for r in ranks[cutoff]}
            home, away = teams.get(g.home_team), teams.get(g.away_team)
            margin = spreads[g.week].get((g.home_team, g.away_team, str(g.neutral_site)))
            if margin is not None and home and away:
                pre = QualifiedPregame(home[0], away[0], margin, g.game_id, str(cutoff))
                bev = calculate_bev(pre).value
                quality = matchup_quality(*[home[0], away[0]])
                basis = None
            else:
                prior = pregame_prior(
                    home, away, field_size=len(teams) or 100, close=14, missing_rating=OTHER_INIT_MEAN
                )
                bev, quality, basis, margin = prior.bev, prior.quality, "Pregame baseline", None
            final = g.home_score, g.away_score
            # These fixtures predate the introduction of overtime.
            value, support = _sparse_final(ref, final, quality, margin, g.game_id, False)
            score = GameAppeal(f"{bev:.1f}", final, value, "final", g.game_id, support, bev_basis=basis)
            ledger.append(
                dict(
                    game=g.game_id,
                    week=g.week,
                    checkpoint=cutoff,
                    home_rank=home[0] if home else None,
                    away_rank=away[0] if away else None,
                    home_margin=margin,
                    bev=score.bev,
                )
            )
            anchor = f"game-{g.game_id}"
            if leaders is not None:
                candidates.append(LeaderGame.from_score("cfb", season.year, g.game_date, g.away_team,
                                                        g.home_team, anchor, score))
            rows.append(
                f'<tr id="{anchor}" data-cfbd-game="{g.game_id}"><td>{g.game_date}</td><td>{escape(g.away_team)}</td>'
                f"<td>{escape(g.home_team)}</td><td>{bev_label(score)}</td><td>{g.away_score}–{g.home_score}</td>"
                f"<td>{aev_label(score)}</td></tr>"
            )
        y = season.year
        if leaders is not None:
            leaders.add_season(candidates)
        text = (
            f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{y} football game excitement | SportsRank</title><link rel="stylesheet" href="/cfb/assets/record-book.css"></head>'
            f'<body><main><p><a href="/cfb/cfb.html">Football seasons</a></p><h1>{y} football game excitement</h1>'
            "<p>BEV rates pregame appeal using the preceding CORS checkpoint. AEV estimates drama from the verified final "
            "when scoring history is unavailable. Scores run from 0 to 100. Game results and rosters come from CollegeFootballData.</p>"
            '<p>* Final-score estimate. <a href="bev-v1.json">Pregame inputs</a></p><div class="table-scroll"><table><thead><tr>'
            "<th>Date</th><th>Away</th><th>Home</th><th>BEV</th><th>Final (away–home)</th><th>AEV</th>"
            "</tr></thead><tbody>" + "\n".join(rows) + "</tbody></table></div></main></body></html>"
        )
        yield y, text, dict(provider="CollegeFootballData", season=y, model="CORS v1.0.0", games=ledger)
