"""Weekly CORS v0.6.0, the frozen opponent-adjusted margin blend (#100)."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

import numpy as np

from .replay import _rank

VERSION = "CORS v0.6.0"
SHARE = .9482
CARRY = 1.0261
PRIOR_GAMES = 4.613
DECAY = .96
HFA = 3.5


def blend_seasons(database, seasons):
    """Return reconstructed seasons while leaving the v0.4.0 replay available."""
    seed = json.loads(Path(__file__).with_name("blend_seed.json").read_bytes())
    if seasons[0]["season"] != seed["season"] + 1:
        raise ValueError("The blend seed requires the complete replay from 2019–20")
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        keys = {(year, team): int(source) for year, team, source in con.execute(
            "SELECT season,id,json_extract(raw_json,'$.sourceId') FROM teams")}
    sources = sorted(set(keys.values()) | {int(k) for k in seed["teams"]})
    index = {source: i for i, source in enumerate(sources)}
    member, cors, adj, wve, hsum, asum, hn, an = np.array(
        [seed["teams"].get(str(source), [0]*8) for source in sources], dtype=float).T.copy()
    xx, xy = seed["xx"], seed["xy"]
    # All arrays use stable provider identities, including teams absent for a season.
    nteams = len(index)
    output = []
    for season in seasons:
        year = season["season"]
        lookup = {team: index[source] for (sy, team), source in keys.items() if sy == year}
        if len(set(lookup.values())) != len(lookup):
            raise ValueError(f"Duplicate source team identity in {year}")
        returning = member == year-1
        cors = np.where(returning, CARRY*(cors-.5*wve), 0)
        prior = np.where(returning, CARRY*adj, 0)
        adj = prior.copy()
        wve = np.zeros(nteams)
        active = (hn > 0) & (an > 0)
        effect = np.zeros(nteams)
        counts = np.minimum(hn[active], an[active])
        effect[active] = .5*(hsum[active]/hn[active]-asum[active]/an[active])*counts/(counts+49.2985)
        if active.any():
            effect[active] -= effect[active].mean()
        scale = np.clip(xy/xx, .02, 8.) if xx else 1.
        fixtures = defaultdict(list)
        finals = []
        for g in season["forecasts"]:
            fixtures[g["cutoff_utc"]].append(g)
            if g["actual_home_margin"] is not None:
                finals.append((datetime.fromisoformat(g["start_utc"]), g))
        finals.sort(key=lambda item: (item[0], item[1]["game_id"]))
        cursor = 0
        home, away, margins, weeks = [], [], [], []
        played, won, differential, pf, pa = np.zeros((5, nteams))
        ys_xx = ys_xy = 0.
        rankings, forecasts = [], []
        for c in season["rankings"]:
            cutoff = c["cutoff_utc"]
            limit = datetime.fromisoformat(cutoff) - timedelta(hours=6)
            # Age observations once per Monday before adding newly eligible results.
            for stats in (played, won, differential, pf, pa):
                stats *= DECAY
            while cursor < len(finals) and finals[cursor][0] < limit:
                g = finals[cursor][1]
                cursor += 1
                h, a = lookup[g["home_team_id"]], lookup[g["away_team_id"]]
                y = g["actual_home_margin"]
                home.append(h)
                away.append(a)
                site = 0 if g["neutral_site"] else HFA+effect[h]
                margins.append(np.clip(y-site, -60, 60))
                weeks.append(c["week"])
                for t, margin, scored, allowed in ((h,y,g["home_points"],g["away_points"]),
                                                   (a,-y,g["away_points"],g["home_points"])):
                    played[t] += 1
                    won[t] += margin > 0
                    differential[t] += np.clip(margin, -60, 60)
                    pf[t] += scored
                    pa[t] += allowed
            sos = np.zeros(nteams)
            if home:
                h, a = np.array(home), np.array(away)
                weights = DECAY**(c["week"]-np.array(weeks))
                def accumulate(h_values, a_values):
                    return (np.bincount(h, weights*h_values, minlength=nteams)
                            + np.bincount(a, weights*a_values, minlength=nteams))
                denom = np.where(played > 0, played, 1)
                wp = (won+1)/(played+2)
                sos = accumulate(cors[a], cors[h])/denom
                schedule = .9*sos + 2.5*accumulate(wp[a], wp[h])/denom
                m = differential/denom
                mov = .75*np.sign(m)*np.log10(1+abs(m))+.25*m/5
                cors = 8*mov*(.8+.4*schedule/57.16)
                ratio = np.divide(pa, pf, out=np.ones(nteams), where=pf>0)
                wve = won-played/(1+ratio**2.1991)
                rhs = PRIOR_GAMES*prior + accumulate(np.array(margins), -np.array(margins))
                # Match the twelve damped Jacobi steps used by the selected model.
                for _ in range(12):
                    adj = .75*(rhs+accumulate(adj[a], adj[h]))/(PRIOR_GAMES+played)+.25*adj
            # Store ratings in points so their difference is the neutral-court spread.
            rating = scale*(SHARE*adj+(1-SHARE)*cors)
            rows = _rank([{**r, "cors": float(rating[lookup[r["team_id"]]]),
                           "sos": float(sos[lookup[r["team_id"]]])} for r in c["rows"]])
            rankings.append({**c, "rows": rows})
            if cutoff == season["final_cutoff_utc"]:
                final_rows = rows
                end_state = cors.copy(), adj.copy(), wve.copy()
            for g in fixtures[cutoff]:
                h, a = lookup[g["home_team_id"]], lookup[g["away_team_id"]]
                gap = SHARE*(adj[h]-adj[a])+(1-SHARE)*(cors[h]-cors[a])
                venue = 0 if g["neutral_site"] else HFA
                raw = scale*gap + venue
                prediction = raw + (0 if g["neutral_site"] else effect[h])
                forecasts.append({**g, "home_cors": float(rating[h]), "away_cors": float(rating[a]),
                                  "home_margin": round(float(prediction), 2)})
                # These accumulate calibration inputs for the following season only.
                y = g["actual_home_margin"]
                if y is not None:
                    ys_xx += gap*gap
                    ys_xy += gap*(y-venue)
                    if not g["neutral_site"]:
                        hsum[h] += y-raw
                        asum[a] -= y-raw
                        hn[h] += 1
                        an[a] += 1
        member[played>0] = year
        cors, adj, wve = end_state
        xx, xy = .9191*xx+ys_xx, .9191*xy+ys_xy
        output.append({**season, "rankings": tuple(rankings), "final_rankings": final_rows,
                       "forecasts": tuple(forecasts)})
    return tuple(output)
