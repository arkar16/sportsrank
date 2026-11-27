"""Men's basketball pregame records and qualified score-and-clock measurements."""
from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime, timedelta
from html import escape
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from cfb.excitement import BASKETBALL, QualifiedPregame, calculate_bev, matchup_quality
from cfb.excitement_pages import ESTIMATE_NOTE, aev_label, bev_label
from excitement_grades import SCALE_LINK
from cfb.game_excitement import GameAppeal
from cfb.record_book import _write_changed
from hashlib import sha256
from .cors_v1 import OUTCOME_START, MARGIN_ONLY_VERSION, cors_v1_seasons
from .source import visible_final
from .team_schedule import projection_margin
from .halftime import DATA, policy, halftime_pools, final_pools
from cfb.sparse_excitement import pregame_prior

EASTERN = ZoneInfo("America/New_York")
FIELDS = ("version", "checkpoint", "game", "date", "home_id", "away_id", "neutral",
          "home_rank", "away_rank", "home_margin", "bev")


def local_date(stamp):
    return datetime.fromisoformat(stamp).astimezone(EASTERN).date().isoformat()


def basketball_bev(home_rank, away_rank, margin, game_id, cutoff):
    pre = QualifiedPregame(home_rank, away_rank, float(margin), str(game_id), cutoff)
    return f"{calculate_bev(pre, scale=BASKETBALL).value:.1f}"


def frozen_excitement_seasons(database, baseline, current):
    """Recover the CORS inputs in force at each pregame checkpoint used by the excitement ledgers."""
    cutoff = OUTCOME_START.isoformat()
    earlier = [s for s in baseline if s["rankings"][0]["cutoff_utc"] < cutoff]
    if not earlier:
        return {}
    legacy = cors_v1_seasons(database, earlier, api_only=True, model=MARGIN_ONLY_VERSION)
    result = {}
    for old, new in zip(legacy, current):
        year = old["season"]
        if old["rankings"][-1]["cutoff_utc"] < cutoff:
            result[year] = old
            continue
        # Only the transition season spans checkpoints from both model versions.
        checkpoints = {c["cutoff_utc"]: c for c in old["rankings"] if c["cutoff_utc"] < cutoff}
        forecasts = {g["game_id"]: g for g in old["forecasts"] if g["cutoff_utc"] < cutoff}
        result[year] = {**new,
                        "rankings": tuple(checkpoints.get(c["cutoff_utc"], c) for c in new["rankings"]),
                        "forecasts": tuple(forecasts.get(g["game_id"], g) for g in new["forecasts"])}
    return result


def read_measurements(year):
    digest = policy()["seasons"].get(str(year))
    if not digest:
        return {}
    data = (DATA / f"{year}.csv").read_bytes()
    if sha256(data).hexdigest() != digest:
        raise ValueError(f"corrupt CollegeBasketballData measurements for {year}")
    evidence = {}
    for row in csv.DictReader(StringIO(data.decode())):
        gid = row["game_id"]
        if gid in evidence:
            raise ValueError(f"duplicate CBBD game {gid}")
        evidence[gid] = row
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
        write_ledger(self.path, FIELDS, self.rows)


def write_ledger(path, fields, rows):
    path.parent.mkdir(exist_ok=True, parents=True)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows[key] for key in sorted(rows))
    _write_changed(path, output.getvalue().encode("utf-8"))


class BaselineLedger:
    fields = ("checkpoint", "game", "home_id", "away_id", "date", "bev", "quality")

    def __init__(self, root, year):
        self.path = root / f"seasons/{year}/excitement/baselines-v1.csv"
        self.rows = {}
        if self.path.is_file():
            with self.path.open(newline="") as source:
                reader = csv.DictReader(source)
                if tuple(reader.fieldnames or ()) != self.fields:
                    raise ValueError("unknown basketball pregame baseline ledger")
                for row in reader:
                    key = row["checkpoint"], row["game"]
                    if key in self.rows:
                        raise ValueError("duplicate basketball pregame baseline")
                    self.rows[key] = row

    def capture(self, checkpoint, game_id, home, away, date, prior):
        key = checkpoint, str(game_id)
        row = dict(zip(self.fields, (*key, str(home), str(away), date, f"{prior.bev:.1f}", repr(prior.quality))))
        if self.rows.setdefault(key, row) != row:
            raise ValueError(f"pregame baseline changed for {key}")
        return row["bev"]

    def write(self):
        if self.rows:
            write_ledger(self.path, self.fields, self.rows)


def final_value(final, quality, margin=None, overtime=None, identity=None):
    home, away = final
    drama, support = final_pools()[overtime].estimate(abs(home - away), identity)
    winner_margin = (margin if home > away else -margin) if margin is not None else 0
    surprise = min(1, max(0, -winner_margin / BASKETBALL.close_margin))
    return drama + 3 * quality + 3 * surprise, support


def after_game(g, bev, ranks, evidence, as_of, *, prior=None):
    basis = "Pregame baseline" if prior is not None else None
    if (not visible_final(g, as_of)
            or g["home_points"] is None or g["away_points"] is None):
        return GameAppeal(bev, None, source_game=str(g["game_id"]), bev_basis=basis)
    home, away = str(g["home_team_id"]), str(g["away_team_id"])
    final = (g["home_points"], g["away_points"])
    identity = str(g["game_id"])
    row = evidence.get(identity, {})
    if row and ((row["home_id"], row["away_id"]) != (home, away)
                or (int(row["home_score"]), int(row["away_score"])) != final):
        row = {}  # A corrected box must be requalified against the scoring evidence.
    if final[0] == final[1] or min(final) < 0:
        return GameAppeal(bev, None, source_game=str(g["game_id"]), bev_basis=basis)
    quality = prior.quality if prior else matchup_quality(ranks[g["home_team_id"]], ranks[g["away_team_id"]])
    margin = g["home_margin"]
    winner_margin = (margin if final[0] > final[1] else -margin) if margin is not None else 0
    surprise = min(1, max(0, -winner_margin / BASKETBALL.close_margin))
    periods = int(row["periods"]) if row.get("periods") else None
    if periods is not None and periods < 2:
        raise ValueError(f"invalid basketball period count for {row['game_id']}")
    overtime = periods > 2 if periods is not None else g.get("overtime")
    if row.get("tension") and periods is not None:
        tension = float(row["tension"])
        leads, deficit = int(row["lead_changes"]), int(row["winner_deficit"])
        if not (0 <= tension <= 1 and leads >= 0 and deficit >= 0):
            raise ValueError(f"invalid basketball score path for {row['game_id']}")
        drama = 80 * tension + 8 * min(1, leads / 3) + 4 * min(1, deficit / BASKETBALL.comeback_margin)
        return GameAppeal(bev, final, drama + 2 * overtime + 3 * quality + 3 * surprise, "full", identity, bev_basis=basis)

    half = g.get("halftime")
    source_final = final
    if half is None or overtime is None or not policy()["halftime_qualified"]:
        value, support = final_value(final, quality, margin, overtime, identity)
        return GameAppeal(bev, final, value, "final", identity, support, bev_basis=basis)
    if any(h < 0 or h > f for h, f in zip(half, source_final)):
        raise ValueError(f"invalid basketball halftime score for {identity}")
    winner_margin = (half[0] - half[1]) * (1 if source_final[0] > source_final[1] else -1)
    drama, support, distance = halftime_pools()[overtime].estimate(
        identity, abs(final[0] - final[1]), sum(final), winner_margin,
    )
    value = drama + 2 * overtime + (3 * quality + 3 * surprise)
    return GameAppeal(bev, final, value, "halftime", identity, support, distance, basis)


def checkpoint_prior(rows, home, away, neutral, checkpoint, *, field_size=None):
    def team(key):
        return (rows[key]["rank"], rows[key]["cors"]) if key in rows else None
    venue = 0 if neutral else (checkpoint.get("home_advantages") or {}).get(home, 3.6)
    return pregame_prior(team(home), team(away), field_size=field_size if field_size is not None else len(rows), close=BASKETBALL.close_margin,
                         venue=venue, missing_rating=checkpoint.get("non_d1_rating", -26.0))


def weekly_scores(root, season, checkpoints, as_of, schedules=None):
    year = season["season"]
    ledger = PregameLedger(root / f"seasons/{year}/excitement/bev-v1.csv")
    baselines = BaselineLedger(root, year)
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
    fixtures = {str(g["game_id"]): g for g in season["forecasts"]}
    for (y, _), schedule in (schedules or {}).items():
        if y != year:
            continue
        for fixture in schedule.fixtures:
            game = fixture.game
            if game in scheduled or not game.start_utc:
                continue
            if game.home_handicap is None or game.home not in ids or game.away not in ids:
                prior = checkpoint_prior(standings[cutoff], ids.get(game.home), ids.get(game.away),
                                         game.neutral_site, checkpoints[-1])
                bev = baselines.capture(cutoff, f"schedule-{schedule.snapshot_date}-{fixture.forecast_id}",
                                        ids.get(game.home, game.home), ids.get(game.away, game.away),
                                        local_date(game.start_utc), prior)
                scheduled[game] = GameAppeal(bev, None, source_game=str(fixture.forecast_id), bev_basis="Pregame baseline")
                continue
            # A dated schedule can reverse the designated sides at a neutral site.
            # Preserve its projection and the weekly forecast under their own source identities.
            projection = f"schedule-{schedule.snapshot_date}-{fixture.forecast_id}"
            home, away = ids[game.home], ids[game.away]
            margin = projection_margin(season, checkpoints[-1], standings[cutoff][home], standings[cutoff][away],
                                       game.neutral_site, fixtures.get(fixture.forecast_id))
            bev = ledger.capture(cutoff, projection, game.start_utc, home, away,
                                 game.neutral_site, positions[cutoff], margin or 0.)
            scheduled[game] = GameAppeal(bev, None, source_game=str(fixture.forecast_id))
    ledger.write()
    baselines.write()
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
               f'A pregame baseline uses the ratings available before tipoff. {SCALE_LINK}.</p>')
    if any(row[4].estimated for row in completed):
        content += f'<p>{ESTIMATE_NOTE} The score label identifies the available evidence.</p>'
    for heading, rows in (("Current and upcoming games by BEV", upcoming),
                          ("Completed games by AEV", completed), ("Games awaiting a verified final", missing)):
        if not rows:
            continue
        has_final = rows is completed
        values = []
        for day, away, home, week, score in rows:
            gid = escape(score.source_game or "")
            row = (day, week_link(year, week, "Preseason" if not week else f"Week {week}"), label(away), label(home),
                   f'<span data-cbbd-fixture="{gid}">{bev_label(score, sport="cbb")}</span>')
            if has_final:
                h, a = score.final
                final = f"{a}–{h}"
                if score.source_game:
                    final = f'<span data-cbbd-game="{escape(score.source_game)}">{final}</span>'
                aev = aev_label(score, sport="cbb")
                if score.tier == "halftime":
                    aev = (f'<span title="Halftime estimate from {score.support} comparable games" '
                           f'data-reference-distance="{score.reference_distance}">{aev}</span>')
                row += (final, aev)
            values.append(row)
        headers = ("Date", "Checkpoint", "Away", "Home", "BEV") + (("Final (away–home)", "AEV") if has_final else ())
        row_ids = [f"game-{score.source_game}" for _, _, _, _, score in rows]
        content += f"<h2>{heading}</h2>" + table(headers, values, row_ids=row_ids)
    return content
