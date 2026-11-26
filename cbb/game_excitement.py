"""Men's basketball pregame records and qualified score-and-clock measurements."""
from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from cfb.excitement import BASKETBALL, QualifiedPregame, calculate_bev, matchup_quality
from cfb.excitement_pages import aev_label
from cfb.game_excitement import GameAppeal
from cfb.record_book import _write_changed

DATA = Path(__file__).with_name("excitement_data")
EASTERN = ZoneInfo("America/New_York")
FIELDS = ("version", "checkpoint", "game", "date", "home_id", "away_id", "neutral",
          "home_rank", "away_rank", "home_margin", "bev")


def local_date(stamp):
    return datetime.fromisoformat(stamp).astimezone(EASTERN).date().isoformat()


def basketball_bev(home_rank, away_rank, margin, game_id, cutoff):
    pre = QualifiedPregame(home_rank, away_rank, float(margin), str(game_id), cutoff)
    return f"{calculate_bev(pre, scale=BASKETBALL).value:.1f}"


def read_measurements(year):
    path = DATA / f"{year}.csv"
    evidence = defaultdict(list)
    if path.is_file():
        with path.open(newline="") as source:
            for row in csv.DictReader(source):
                key = row["date"], frozenset((row["home_id"], row["away_id"]))
                evidence[key].append(row)
    return evidence


class PregameLedger:
    def __init__(self, path):
        self.path = path
        self.rows = {}
        if path.is_file():
            with path.open(newline="") as stream:
                reader = csv.DictReader(stream)
                if tuple(reader.fieldnames or ()) != FIELDS:
                    raise ValueError(f"unknown basketball BEV ledger in {path}")
                for record in reader:
                    key = record["checkpoint"], record["game"]
                    if key in self.rows or record["version"] != BASKETBALL.version:
                        raise ValueError(f"duplicate or incompatible basketball BEV in {path}")
                    expected = basketball_bev(int(record["home_rank"]), int(record["away_rank"]),
                                              record["home_margin"], record["game"], record["checkpoint"])
                    if expected != record["bev"]:
                        raise ValueError(f"invalid frozen basketball BEV for {key}")
                    self.rows[key] = record

    def capture(self, cutoff, game_id, tip, home, away, neutral, ranks, margin):
        if datetime.fromisoformat(cutoff) >= datetime.fromisoformat(tip):
            raise ValueError("basketball BEV checkpoint must precede tipoff")
        value = basketball_bev(ranks[home], ranks[away], margin, game_id, cutoff)
        record = dict(zip(FIELDS, (BASKETBALL.version, cutoff, str(game_id), local_date(tip), str(home), str(away),
                                   str(int(neutral)), str(ranks[home]), str(ranks[away]), f"{margin:.2f}", value)))
        key = cutoff, str(game_id)
        if self.rows.setdefault(key, record) != record:
            raise ValueError(f"changed inputs to frozen basketball BEV for {key}")
        return value

    def write(self):
        self.path.parent.mkdir(exist_ok=True, parents=True)
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(self.rows[key] for key in sorted(self.rows))
        _write_changed(self.path, output.getvalue().encode("utf-8"))


def after_game(g, bev, ranks, evidence, as_of):
    if (g["status"] != "final" or datetime.fromisoformat(g["start_utc"]) + timedelta(hours=6) >= as_of
            or g["home_points"] is None or g["away_points"] is None):
        return GameAppeal(bev, None)
    home, away = str(g["home_team_id"]), str(g["away_team_id"])
    final = (g["home_points"], g["away_points"])
    key = local_date(g["start_utc"]), frozenset((home, away))
    candidates = []
    for row in evidence.get(key, ()):
        flipped = row["home_id"] != home
        scores = (int(row["home_score"]), int(row["away_score"]))
        if final == (scores[::-1] if flipped else scores):
            candidates.append(row)
    if len(candidates) != 1 or not candidates[0]["tension"]:
        return GameAppeal(bev, final)
    row = candidates[0]
    tension = float(row["tension"])
    leads, deficit, periods = (int(row[k]) for k in ("lead_changes", "winner_deficit", "periods"))
    if not (0 <= tension <= 1 and leads >= 0 and deficit >= 0 and periods >= 2):
        raise ValueError(f"invalid basketball score path for {row['game_id']}")
    quality = matchup_quality(ranks[g["home_team_id"]], ranks[g["away_team_id"]])
    margin = g["home_margin"] if final[0] > final[1] else -g["home_margin"]
    surprise = min(1, max(0, -margin / BASKETBALL.close_margin))
    value = (80 * tension + 8 * min(1, leads / 3) + 4 * min(1, deficit / BASKETBALL.comeback_margin)
             + 2 * (periods > 2) + 3 * quality + 3 * surprise)
    return GameAppeal(bev, final, value, "full", row["game_id"])


def weekly_scores(root, season, checkpoints, as_of, schedules=None):
    year = season["season"]
    ledger = PregameLedger(root / f"seasons/{year}/excitement/bev-v1.csv")
    evidence = read_measurements(year)
    standings = {c["cutoff_utc"]: {r["team_id"]: r for r in c["rows"]} for c in checkpoints}
    positions = {cutoff: {i: r["rank"] for i, r in rows.items()} for cutoff, rows in standings.items()}
    scores, scheduled = {}, {}
    for g in season["forecasts"]:
        cutoff = g["cutoff_utc"]
        if cutoff not in standings:
            continue
        home, away = g["home_team_id"], g["away_team_id"]
        if (home not in standings[cutoff] or away not in standings[cutoff] or g["home_margin"] is None):
            continue
        for side, team in (("home", home), ("away", away)):
            if standings[cutoff][team]["cors"] != g[side + "_cors"]:
                raise ValueError("basketball spread and checkpoint ratings disagree")
        bev = ledger.capture(cutoff, g["game_id"], g["start_utc"], home, away, g["neutral_site"],
                             positions[cutoff], g["home_margin"])
        scores[g["game_id"]] = after_game(g, bev, positions[cutoff], evidence, as_of)
    cutoff = checkpoints[-1]["cutoff_utc"]
    ids = {r["school"]: i for i, r in standings[cutoff].items()}
    for (y, _), schedule in (schedules or {}).items():
        if y != year:
            continue
        for fixture in schedule.fixtures:
            game = fixture.game
            if (game in scheduled or game.home_handicap is None or not game.start_utc
                    or game.home not in ids or game.away not in ids):
                continue
            # A dated schedule can reverse the designated sides at a neutral site.
            # Preserve its projection and the weekly forecast under their own source identities.
            projection = f"schedule-{schedule.snapshot_date}-{fixture.forecast_id}"
            bev = ledger.capture(cutoff, projection, game.start_utc, ids[game.home], ids[game.away],
                                 game.neutral_site, positions[cutoff], -game.home_handicap)
            scheduled[game] = GameAppeal(bev, None)
    ledger.write()
    return scores, scheduled


def guide(year, games, season_name, label, week_link, table, as_of):
    today = as_of.astimezone(EASTERN).date().isoformat()
    upcoming = sorted((r for r in games if r[4].final is None and r[0] >= today),
                      key=lambda r: (-float(r[4].bev), r[:3]))
    missing = sorted((r for r in games if r[4].final is None and r[0] < today), key=lambda r: r[:3])
    completed = sorted((r for r in games if r[4].final is not None),
                       key=lambda r: (-(r[4].aev if r[4].aev is not None else -1), r[:3]))
    content = (f'<p><a href="../index.html">{season_name(year)} basketball</a> · '
               '<a href="bev-v1.csv">Pregame inputs</a></p>'
               '<p>Before Excitement Value (BEV) rates pregame appeal using CORS ranks and the projected margin. '
               'After Excitement Value (AEV) measures score-and-clock drama over two halves, with lead changes, '
               'comebacks and overtime. Both use a 0–100 scale. Six points is a two-possession margin. '
               'A dash means the scoring evidence is insufficient.</p>')
    for heading, rows in (("Current and upcoming games by BEV", upcoming),
                          ("Completed games by AEV", completed), ("Games awaiting a verified final", missing)):
        if not rows:
            continue
        has_final = rows is completed
        values = []
        for day, away, home, week, score in rows:
            row = (day, week_link(year, week, "Preseason" if not week else f"Week {week}"), label(away), label(home), score.bev)
            if has_final:
                h, a = score.final
                final = f"{a}–{h}"
                if score.source_game:
                    final = f'<a href="https://www.espn.com/mens-college-basketball/game/_/gameId/{escape(score.source_game)}">{final}</a>'
                row += (final, aev_label(score))
            values.append(row)
        headers = ("Date", "Checkpoint", "Away", "Home", "BEV") + (("Final (away–home)", "AEV") if has_final else ())
        content += f"<h2>{heading}</h2>" + table(headers, values)
    return content
