"""Frozen weekly two-timescale basketball CORS filter."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3

import numpy as np

from .source import fixture_start, latest_start
from .replay import _iso_utc, _previous_monday, _rank

VERSION = "CORS v1.0.0"
MODEL_ID = "cbb-kalman-2scale/v1"
CARRY_TOTAL, W_SLOW, ALPHA_SLOW, RHO_VAR = .96, .45, .22, .80
SIGMA_OFF, SIGMA_WK, SIGMA_G_BASE, NU = 3.8, .55, 11., 14.
ENTRANT_PRIOR, SIGMA_NOND1 = -12., 21.
SIGMA_HFA_OFF, SIGMA_HFA_WK = .40, .08
HFA_SHRINK, HFA_TEAM_MEM = 40., .95
NOISE_SCALE = math.sqrt((NU - 2) / NU)


def rounded(x):
    return math.copysign(math.floor(abs(x) * 100 + .5) / 100, x)


def _available(start):
    buffered = start + timedelta(hours=6)
    cutoff = _previous_monday(buffered) + timedelta(weeks=1)
    if cutoff <= buffered:
        cutoff += timedelta(weeks=1)
    return _iso_utc(cutoff)


def _prior(year, previous, genesis):
    if previous is None:
        return (0. if genesis else ENTRANT_PRIOR), 100.
    last, fast, slow, variance = previous[:4]
    gap = year - last
    if gap == 1:
        return CARRY_TOTAL * ((1 - W_SLOW)*fast + W_SLOW*slow), RHO_VAR**2*variance + SIGMA_OFF**2
    gap = min(gap, 3)
    return CARRY_TOTAL**gap * slow, min(100., variance + gap*SIGMA_OFF**2)


def _update(mu, cov, observations, adaptive, *, game_deltas=None):
    """Accumulate information using a single pre-update mean and covariance."""
    n = len(mu) - 2
    cov[np.arange(n), np.arange(n)] += SIGMA_WK**2
    if adaptive:
        cov[-1, -1] += SIGMA_HFA_WK**2
    precision = np.linalg.inv(cov)
    eta = precision @ mu
    terms = []
    for home, away, margin, venue in observations:
        noise = ((SIGMA_NOND1 if away == n else SIGMA_G_BASE) * NOISE_SCALE)**2
        # Only D1-D1 games measure the league home-advantage node.
        ids, design = [home, away], [1., -1.]
        if venue and adaptive and away != n:
            ids.append(n + 1)
            design.append(1.)
        else:
            margin -= venue * mu[-1]
        x = np.asarray(design)
        block = np.ix_(ids, ids)
        residual = margin - x @ mu[ids]
        variance = noise + x @ cov[block] @ x
        weight = (NU + 1) / (NU + residual**2 / max(variance, 1e-6)) / noise
        precision[block] += weight * np.outer(x, x)
        eta[ids] += weight * margin * x
        if game_deltas is not None and away < n:
            terms.append((home, away, margin, ids, x, weight))
    cov = np.linalg.inv(precision)
    mu = cov @ eta
    # Remove one observation from the same weekly fit with its robust
    # weight held fixed. Neither the fit nor its covariance changes.
    for home, away, margin, ids, x, weight in terms:
        v = cov[:, ids] @ x
        leverage = weight * float(x @ v[ids])
        delta = (weight * (margin - x @ mu[ids]) / max(1.0 - leverage, 1e-9)) * v
        center = delta[:n].mean()
        game_deltas.append((float(delta[home] - center), float(delta[away] - center)))
    return mu, cov


def _validate_seed(seed):
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)

    if (not isinstance(seed, dict) or type(seed.get("season")) is not int
            or not isinstance(seed.get("teams"), dict)
            or not all(finite(seed.get(k)) for k in ("hfa_mean", "hfa_var", "nond1_mean", "nond1_var"))
            or min(seed["hfa_var"], seed["nond1_var"]) <= 0):
        raise ValueError("Invalid CORS v1 seed state")
    for key, row in seed["teams"].items():
        if (not isinstance(key, str) or not key.isdecimal() or str(int(key)) != key
                or not isinstance(row, list) or len(row) != 8 or not all(map(finite, row))
                or type(row[0]) is not int or row[0] > seed["season"]
                or row[3] <= 0 or min(row[6:]) < 0):
            raise ValueError(f"Invalid CORS v1 seed team {key}")


def _weekly_games(season, idx, effect, tier_games):
    """Group fixtures by prediction cutoff and finals by first eligible observation cutoff."""
    fixtures, observations, result_ids = defaultdict(list), defaultdict(list), defaultdict(list)
    for g in sorted(season["forecasts"], key=lambda g: (g["start_utc"], g["game_id"])):
        fixtures[g["cutoff_utc"]].append(g)
        if g["actual_home_margin"] is not None:
            h, a = idx[g["home_team_id"]], idx[g["away_team_id"]]
            venue = int(not g["neutral_site"])
            cutoff = _available(latest_start(datetime.fromisoformat(g["start_utc"]), g.get("date_only")))
            observations[cutoff].append((h, a, g["actual_home_margin"] - venue * effect[h], venue))
            result_ids[cutoff].append(g["game_id"])
    for start, team, margin, venue in sorted(tier_games):
        if team in idx:
            h = idx[team]
            observations[_available(start)].append((h, len(idx), margin - (effect[h] if venue == 1 else 0), venue))
    return fixtures, observations, result_ids


def _checkpoints(season, observations):
    """A late tier result may require a final Monday beyond the last D1 game."""
    checkpoints = list(season["rankings"])
    final_cutoff = max(season["final_cutoff_utc"], max(observations, default=""))
    while checkpoints[-1]["cutoff_utc"] < final_cutoff:
        last = checkpoints[-1]
        monday = _previous_monday(datetime.fromisoformat(last["cutoff_utc"]) + timedelta(hours=1))
        checkpoints.append({**last, "week": last["week"] + 1, "cutoff_utc": _iso_utc(monday + timedelta(weeks=1))})
    return checkpoints, final_cutoff


def iter_cors_v1_seasons(seasons, keys, seed=None, *, nond1_by_season=None, hfa_start_year=2008, use_roster=False):
    """Replay consecutive seasons. Tier observations are (UTC start, roster ID, D1 margin, venue sign)."""
    state = {}
    previous_year = None
    tier, tier_var, hfa, hfa_var = -26., 25., 3.6, .25
    if seed is not None:
        _validate_seed(seed)
        previous_year = seed["season"]
        state = {int(k): list(row) for k, row in seed["teams"].items()}
        tier, tier_var = seed["nond1_mean"], seed["nond1_var"]
        hfa, hfa_var = seed["hfa_mean"], seed["hfa_var"]
    for season in seasons:
        year = season["season"]
        genesis = previous_year is None
        if not genesis and year != previous_year + 1:
            raise ValueError(f"CORS v1 requires season {previous_year + 1}, got {year}")
        previous_year = year
        active = {g[k] for g in season["forecasts"] for k in ("home_team_id", "away_team_id")}
        if use_roster:
            active = {row["team_id"] for row in season["rankings"][0]["rows"]}
        teams = sorted(active, key=lambda t: keys[year, t])
        sources = [keys[year, t] for t in teams]
        if len(set(sources)) != len(teams):
            raise ValueError(f"Duplicate source identity in {year}")
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)
        priors = [_prior(year, state.get(s), genesis) for s in sources]
        mu = np.array([p[0] for p in priors] + [tier, hfa])
        mu[:n] -= mu[:n].mean()
        cov = np.diag([p[1] for p in priors] + [tier_var + 9., hfa_var + SIGMA_HFA_OFF**2])
        effect = np.zeros(n)
        experienced = []
        for i, source in enumerate(sources):
            if year > hfa_start_year and source in state:
                hs, aws, hn, an = state[source][4:]
                count = min(hn, an)
                if count > 0:
                    effect[i] = .5 * (hs/hn - aws/an) * count/(count + HFA_SHRINK)
                    experienced.append(i)
        if experienced:
            effect[experienced] -= effect[experienced].mean()

        tier_games = (nond1_by_season or {}).get(year, ())
        fixtures, observations, result_ids = _weekly_games(season, idx, effect, tier_games)
        checkpoints, final_cutoff = _checkpoints(season, observations)
        rankings, forecasts = [], []
        deltas_by_game = {}
        opp_sum, opp_count = np.zeros((2, n))
        residuals = np.zeros((n, 4))
        ratings = mu[:n].copy()
        for c in checkpoints:
            cutoff = c["cutoff_utc"]
            if c["week"] and observations[cutoff]:
                for h, a, _, _ in observations[cutoff]:
                    if a < n:
                        opp_sum[h] += ratings[a]
                        opp_sum[a] += ratings[h]
                        opp_count[[h, a]] += 1
                deltas = []
                mu, cov = _update(mu, cov, observations[cutoff], year >= hfa_start_year, game_deltas=deltas)
                for game_id, (home, away) in zip(result_ids[cutoff], deltas, strict=True):
                    deltas_by_game[game_id] = {
                        "home_cors_delta": home, "away_cors_delta": away, "result_cutoff_utc": cutoff,
                    }
            sos = opp_sum / np.maximum(1, opp_count)
            ratings = mu[:n] - mu[:n].mean()
            standing_rows = []
            advantages = {}
            for row in c["rows"]:
                team = row["team_id"]
                i = idx.get(team)
                standing_rows.append({**row, "cors": float(ratings[i]) if i is not None else 0.,
                                      "sos": float(sos[i]) if i is not None else 0.})
                advantages[team] = float(mu[-1] + (effect[i] if i is not None else 0.))
            rows = _rank(standing_rows)
            rankings.append({**c, "rows": rows, "home_advantages": advantages,
                             "non_d1_rating": float(mu[n] - mu[:n].mean())})
            if cutoff == final_cutoff:
                final_rows, final_advantages = rows, advantages
            for g in fixtures[cutoff]:
                h, a = idx[g["home_team_id"]], idx[g["away_team_id"]]
                gap = mu[h] - mu[a]
                venue = 0. if g["neutral_site"] else advantages[g["home_team_id"]]
                forecasts.append({**g, "home_cors": float(ratings[h]), "away_cors": float(ratings[a]),
                                  "home_margin": rounded(gap + venue)})
                if year >= hfa_start_year and not g["neutral_site"] and g["actual_home_margin"] is not None:
                    err = g["actual_home_margin"] - (gap + mu[-1])
                    residuals[h, 0] += err
                    residuals[a, 1] -= err
                    residuals[h, 2] += 1
                    residuals[a, 3] += 1

        center = mu[:n].mean()
        for i, s in enumerate(sources):
            fast = mu[i] - center
            old = state.get(s)
            slow = fast if old is None else (1 - ALPHA_SLOW)*old[2] + ALPHA_SLOW*fast
            if old:
                residuals[i] += old[4:]
            state[s] = [year, fast, slow, cov[i, i], *residuals[i]]
        if year >= hfa_start_year:
            hfa, hfa_var = mu[-1], cov[-1, -1]
            for row in state.values():
                row[4:] = [v * HFA_TEAM_MEM for v in row[4:]]
        tier, tier_var = mu[n] - center, cov[n, n]
        yield {**season, "rankings": tuple(rankings), "forecasts": tuple({**g, **deltas_by_game.get(g["game_id"], {})} for g in forecasts),
               "final_cutoff_utc": final_cutoff, "final_rankings": final_rows, "home_advantages": final_advantages}


def cors_v1_seasons(database, seasons, *, seed_path=None, api_only=False):
    seed = None
    if not api_only:
        seed = json.loads(Path(seed_path or Path(__file__).with_name("cors_v1_seed.json")).read_bytes())
        _validate_seed(seed)
        if seasons[0]["season"] != seed["season"] + 1:
            raise ValueError(f"CORS v1 requires season {seed['season'] + 1} after seed")
    tiers = defaultdict(list)
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        keys = {(y, t): t if api_only else int(s) for y, t, s in db.execute(
            "SELECT season,id,json_extract(raw_json,'$.sourceId') FROM teams")}
        for season in seasons:
            y = season["season"]
            for start, raw in db.execute("SELECT start_utc,raw_json FROM games WHERE season=? AND status='final'", (y,)):
                g = json.loads(raw)
                h, a = g["homeTeamId"], g["awayTeamId"]
                home = (y, h) in keys
                hp, ap = g.get("homePoints"), g.get("awayPoints")
                if (home == ((y, a) in keys) or (not api_only and g.get("startTimeTbd") is not False)
                        or type(hp) is not int or type(ap) is not int or hp == ap or min(hp, ap) < 0):
                    continue
                sign = 1 if home else -1
                date_only = bool(g.get("startTimeTbd"))
                start = latest_start(fixture_start(start, date_only), date_only)
                tiers[y].append((start, h if home else a,
                                 sign * (hp - ap), 0 if g["neutralSite"] else sign))
    return tuple(iter_cors_v1_seasons(seasons, keys, seed, nond1_by_season=tiers, use_roster=api_only))
