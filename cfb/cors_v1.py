"""College football CORS v1.1.0 (two-timescale Bayesian state-space multi-tier model).

Implements ADR-0029: exact multivariate Kalman filter with fast/slow multi-year
season carryover, weekly state innovation, Student-t robust margin weighting
(evaluated strictly from pre-week state for 100% within-week order invariance),
adaptive league home-field advantage, and joint latent FCS and Other tier nodes.
ADR-0032 limits the margin credited to FBS favorites against FCS opponents.

Ratings are zero-centered expected neutral-field points above/below an average
FBS team in that season. Positive spreads indicate the home team is favored.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import posixpath
import re
from typing import Any, Mapping, Sequence

import numpy as np

from .historical_rebuild import _school
from .market_lines import MarketLines
from .performance import _page, _read, build_performance_files
from .ranking_progression import build_progression_outputs
from .release import _row_table
from .season_snapshot import SeasonSnapshot, _checksum
from .season_source import SourceGame, SourceTeam

MODEL_VERSION = "v1.1.0"
UNCAPPED_VERSION = "v1.0.0"
POLICY = "cors-kalman-2scale/v1.1"

# Frozen 2010-2016 locked hyperparameters (ADR-0029).
W_SLOW = 0.50
CARRY_TOTAL = 0.96
SIGMA_OFF = 7.5
NU = 14.0
SIGMA_G = 15.0
ALPHA_SLOW = 0.28
SIGMA_WK = 1.3
SIGMA_FCS = 22.0
SIGMA_OTHER = 24.0
ENTRANT_PRIOR = -10.0
INITIAL_SD = 12.0
RETURN_VAR_DECAY = 0.85
TIER_OFFSEASON_VAR = 16.0
FCS_INIT_MEAN = -26.0
FCS_INIT_VAR = 25.0
FCS_MIN_YEAR = 2003
FCS_BLOWOUT_MARGIN = 35.0
FCS_MARGIN_HEADROOM = 7.0
OTHER_INIT_MEAN = -22.0
OTHER_INIT_VAR = 49.0
HFA_INIT = 3.0
HFA_MEMORY = 0.65
PYTH_EXP = 2.37

RANK_COLUMNS = (
    "rank", "school", "conference", "record", "win_pct",
    "cors", "mov", "sos", "expected_wins", "wins_vs_expected",
)
SPREAD_COLUMNS = (
    "week", "home_team", "away_team", "neutral_site",
    "home_cors", "away_cors", "spread_value", "spread",
    "home_margin", "home_handicap", "predicted_winner", "precision",
)


def rounded(value: float) -> float:
    """Round half away from zero to two decimal places, normalizing -0.0."""
    out = math.copysign(math.floor(abs(value) * 100.0 + 0.5) / 100.0, value)
    return 0.0 if out == 0.0 else out


def _fmt2(value: float) -> str:
    val = rounded(value)
    return f"{val:.2f}"


@dataclass(frozen=True)
class CompletedGame:
    week: int
    home_team: str
    home_division: str
    home_score: int
    away_team: str
    away_division: str
    away_score: int
    neutral_site: bool
    game_id: str = ""
    game_date: str = ""


@dataclass(frozen=True)
class ScheduledGame:
    week: int
    home_team: str
    home_division: str
    away_team: str
    away_division: str
    neutral_site: bool


@dataclass(frozen=True)
class SeasonInput:
    year: int
    members: tuple[tuple[str, str], ...]  # (school, conference)
    completed_games: tuple[CompletedGame, ...]
    upcoming_games: tuple[ScheduledGame, ...] = ()
    is_final: bool = True
    existing_ranking_weeks: tuple[int, ...] = ()
    existing_spread_weeks: tuple[int, ...] = ()
    has_reconstructed: bool = False


@dataclass
class KalmanState:
    prev_fast: dict[str, float] = field(default_factory=dict)
    prev_slow: dict[str, float] = field(default_factory=dict)
    prev_var: dict[str, float] = field(default_factory=dict)
    last_seen_yr: dict[str, int] = field(default_factory=dict)
    fcs_mean: float = FCS_INIT_MEAN
    fcs_var: float = FCS_INIT_VAR
    other_mean: float = OTHER_INIT_MEAN
    other_var: float = OTHER_INIT_VAR
    hfa_est: float = HFA_INIT
    hfa_n: float = 0.0


@dataclass(frozen=True)
class SeasonReplay:
    year: int
    hfa_used: float
    fcs_rating_end: float
    other_rating_end: float
    max_completed_week: int
    preseason: list[dict[str, Any]]
    weekly_rankings: dict[int, list[dict[str, Any]]]
    final_ranking: list[dict[str, Any]] | None
    weekly_spreads: dict[int, list[dict[str, Any]]]
    upcoming_spreads: dict[int, list[dict[str, Any]]]
    final_uncertainty: dict[str, float]
    game_impacts: dict[CompletedGame, dict[str, float]]


def _format_spread_row(
    week: int,
    home_team: str,
    away_team: str,
    neutral_site: bool,
    home_rating: float,
    away_rating: float,
    site_hfa: float,
) -> dict[str, Any]:
    margin = rounded((home_rating - away_rating) + (0.0 if neutral_site else site_hfa))
    handicap = 0.0 if margin == 0.0 else -margin
    if handicap < 0.0:
        label = f"{home_team} {handicap:.2f}"
    elif handicap > 0.0:
        label = f"{home_team} +{handicap:.2f}"
    else:
        label = f"{home_team} 0.00"
    winner = "home" if margin > 0.0 else ("away" if margin < 0.0 else "pickem")
    return {
        "week": week,
        "home_team": home_team,
        "away_team": away_team,
        "neutral_site": str(bool(neutral_site)),
        "home_cors": _fmt2(home_rating),
        "away_cors": _fmt2(away_rating),
        "spread_value": f"{margin:.2f}",
        "spread": label,
        "home_margin": f"{margin:.2f}",
        "home_handicap": f"{handicap:.2f}",
        "predicted_winner": winner,
        "precision": "2",
    }


def _pythagorean_expected(pf: int, pa: int, games: int) -> float:
    if games <= 0:
        return 0.0
    if pf <= 0 and pa <= 0:
        return 0.5 * games
    if pf <= 0:
        return 0.0
    if pa <= 0:
        return float(games)
    return float(games) / (1.0 + (float(pa) / float(pf)) ** PYTH_EXP)


def _build_ranking_rows(
    teams: Sequence[str],
    conferences: Mapping[str, str],
    mu_centered: Mapping[str, float],
    fcs_centered: float,
    other_centered: float,
    year: int,
    stats: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    unsorted: list[tuple[tuple[float, int, int, str], dict[str, Any]]] = []
    for school in teams:
        st = stats[school]
        wins = st["wins"]
        losses = st["losses"]
        ties = st["ties"]
        played = wins + losses + ties
        record = f"{wins}-{losses}-{ties}" if ties else f"{wins}-{losses}"
        win_pct = rounded((wins + 0.5 * ties) / played) if played else 0.0
        mov = rounded(st["margin_sum"] / played) if played else 0.0
        if st["opponents"]:
            opp_vals = []
            for opp_kind, opp_name in st["opponents"]:
                if opp_kind == "fbs":
                    opp_vals.append(mu_centered[opp_name])
                elif opp_kind == "fcs" and year >= FCS_MIN_YEAR:
                    opp_vals.append(fcs_centered)
                else:
                    opp_vals.append(other_centered)
            sos = rounded(sum(opp_vals) / len(opp_vals))
        else:
            sos = 0.0
        exp_w = rounded(_pythagorean_expected(st["pf"], st["pa"], played))
        wve = rounded((wins + 0.5 * ties) - exp_w)
        cors_val = rounded(mu_centered[school])
        sort_key = (-cors_val, -wins, losses, school)
        row = {
            "rank": 0,
            "school": school,
            "conference": conferences.get(school, "—"),
            "record": record,
            "win_pct": f"{win_pct:.2f}",
            "cors": f"{cors_val:.2f}",
            "mov": f"{mov:.2f}",
            "sos": f"{sos:.2f}",
            "expected_wins": f"{exp_w:.2f}",
            "wins_vs_expected": f"{wve:.2f}",
        }
        unsorted.append((sort_key, row))
    unsorted.sort(key=lambda item: item[0])
    rows = []
    for rank, (_, row) in enumerate(unsorted, 1):
        row["rank"] = rank
        rows.append(row)
    return rows


def _fit_week(mu, cov, observations):
    """Weight every observation at the common pre-week state before fitting the batch."""
    effective_noise = []
    for _, home, away, margin, variance in observations:
        predictive_variance = cov[home, home] + cov[away, away] - 2 * cov[home, away] + variance
        residual = margin - (mu[home] - mu[away])
        z2 = residual ** 2 / max(predictive_variance, 1e-6)
        weight = (NU + 1.0) / (NU + z2)
        effective_noise.append(variance / weight)
    for (_, home, away, margin, _), noise in zip(observations, effective_noise, strict=True):
        direction = cov[:, home] - cov[:, away]
        gain = direction / (direction[home] - direction[away] + noise)
        mu = mu + gain * (margin - (mu[home] - mu[away]))
        cov = cov - np.outer(gain, direction)
    return mu, cov, effective_noise


def _game_impacts(mu, cov, teams, observations, effective_noise):
    """Remove one observation from the fitted week, keeping its peers' pre-week weights."""
    impacts = {}
    for (game, home, away, observed, _), noise in zip(observations, effective_noise, strict=True):
        # Rank-one downdate of the posterior. The model publishes FBS-centered ratings.
        direction = cov[:, home] - cov[:, away]
        residual = observed - (mu[home] - mu[away])
        scale = residual / (noise - (direction[home] - direction[away]))
        center = direction[:len(teams)].mean()
        impacts[game] = {
            teams[i]: float((direction[i] - center) * scale)
            for i in (home, away) if i < len(teams)
        }
    return impacts


def replay_seasons(
    season_inputs: Sequence[SeasonInput],
    *,
    use_fcs: bool = True,
    use_other: bool = True,
    state: KalmanState | None = None,
    model_version: str = MODEL_VERSION,
) -> tuple[list[SeasonReplay], KalmanState]:
    """Replay seasons chronologically using the football CORS model."""
    if model_version not in (UNCAPPED_VERSION, MODEL_VERSION):
        raise ValueError(f"unknown football CORS model {model_version!r}")
    if not season_inputs:
        return [], state or KalmanState()

    ordered = sorted(season_inputs, key=lambda s: s.year)
    st = state if state is not None else KalmanState()
    genesis_year = ordered[0].year
    results: list[SeasonReplay] = []

    for season in ordered:
        year = season.year
        conferences = {school: conf for school, conf in season.members}
        teams = sorted(conferences)
        k = len(teams)
        tmap = {school: i for i, school in enumerate(teams)}
        idx_fcs = k
        idx_oth = k + 1
        dim = k + 2

        mu = np.zeros(dim, dtype=float)
        cov = np.zeros((dim, dim), dtype=float)
        for i, school in enumerate(teams):
            if school in st.prev_fast:
                gap_yrs = year - st.last_seen_yr[school]
                if gap_yrs == 1:
                    mu[i] = CARRY_TOTAL * (
                        (1.0 - W_SLOW) * st.prev_fast[school] + W_SLOW * st.prev_slow[school]
                    )
                    cov[i, i] = (RETURN_VAR_DECAY ** 2) * st.prev_var[school] + SIGMA_OFF ** 2
                else:
                    eff_gap = min(gap_yrs, 3)
                    mu[i] = (CARRY_TOTAL ** eff_gap) * st.prev_slow[school]
                    cov[i, i] = min(INITIAL_SD ** 2, st.prev_var[school] + eff_gap * (SIGMA_OFF ** 2))
            else:
                mu[i] = 0.0 if year == genesis_year else ENTRANT_PRIOR
                cov[i, i] = INITIAL_SD ** 2

        if k > 0:
            mu[:k] -= float(np.mean(mu[:k]))
        mu[idx_fcs] = st.fcs_mean
        cov[idx_fcs, idx_fcs] = st.fcs_var + TIER_OFFSEASON_VAR
        mu[idx_oth] = st.other_mean
        cov[idx_oth, idx_oth] = st.other_var + TIER_OFFSEASON_VAR

        hfa_est = st.hfa_est
        season_hfa: list[float] = []

        # Group completed games by week.
        games_by_week: dict[int, list[CompletedGame]] = defaultdict(list)
        for game in season.completed_games:
            games_by_week[game.week].append(game)
        max_completed_week = max(games_by_week) if games_by_week else 0

        # Cumulative stats for ranking table columns.
        team_stats: dict[str, dict[str, Any]] = {
            school: {"wins": 0, "losses": 0, "ties": 0, "pf": 0, "pa": 0, "margin_sum": 0, "opponents": []}
            for school in teams
        }

        def _centered() -> tuple[dict[str, float], float, float]:
            fbs_mean = float(np.mean(mu[:k])) if k > 0 else 0.0
            return (
                {school: float(mu[i] - fbs_mean) for i, school in enumerate(teams)},
                float(mu[idx_fcs] - fbs_mean),
                float(mu[idx_oth] - fbs_mean),
            )

        pre_map, pre_fcs, pre_oth = _centered()
        preseason_rows = _build_ranking_rows(
            teams, conferences, pre_map, pre_fcs, pre_oth, year, team_stats
        )

        game_impacts = {}
        weekly_rankings: dict[int, list[dict[str, Any]]] = {}
        weekly_spreads: dict[int, list[dict[str, Any]]] = {}

        for week in range(max_completed_week + 1):
            week_games = games_by_week.get(week, [])
            pre_wk_map, _, _ = _centered()

            # 1. Generate week spreads strictly from pre-week (mu, cov).
            fbs_week_games = [
                g for g in week_games
                if g.home_division.lower() == "fbs" and g.away_division.lower() == "fbs"
            ]
            spread_rows = []
            for g in sorted(fbs_week_games, key=lambda x: (x.home_team, x.away_team, x.neutral_site)):
                ih, ia = tmap[g.home_team], tmap[g.away_team]
                gap = float(mu[ih] - mu[ia])
                spread_rows.append(
                    _format_spread_row(
                        week, g.home_team, g.away_team, g.neutral_site,
                        pre_wk_map[g.home_team], pre_wk_map[g.away_team], hfa_est,
                    )
                )
                if not g.neutral_site:
                    season_hfa.append(float(g.home_score - g.away_score) - gap)
            weekly_spreads[week] = spread_rows

            # 2. Weekly state innovation on FBS teams for week > 0.
            if week > 0 and k > 0:
                diag = np.arange(k)
                cov[diag, diag] += SIGMA_WK ** 2

            # 3. Collect all week completed observations.
            obs: list[tuple[CompletedGame, int, int, float, float]] = []
            uncapped_observations = {}
            for g in week_games:
                hd, ad = g.home_division.lower(), g.away_division.lower()
                margin = float(g.home_score - g.away_score)
                if hd == "fbs" and ad == "fbs":
                    site = 0.0 if g.neutral_site else hfa_est
                    obs.append((g, tmap[g.home_team], tmap[g.away_team], margin - site, SIGMA_G ** 2))
                elif {hd, ad} == {"fbs", "fcs"}:
                    home_is_fbs = hd == "fbs"
                    fbs_school = g.home_team if home_is_fbs else g.away_team
                    if fbs_school in tmap:
                        fbs_margin = margin if home_is_fbs else -margin
                        sgn = 0.0 if g.neutral_site else (1.0 if home_is_fbs else -1.0)
                        if use_fcs and year >= FCS_MIN_YEAR:
                            i = tmap[fbs_school]
                            site = sgn * hfa_est
                            expected = mu[i] - mu[idx_fcs] + site
                            ceiling = max(FCS_BLOWOUT_MARGIN, expected + FCS_MARGIN_HEADROOM)
                            if model_version == MODEL_VERSION and expected > 0 and fbs_margin > ceiling:
                                uncapped_observations[len(obs)] = (g, i, idx_fcs, fbs_margin - site, SIGMA_FCS ** 2)
                                fbs_margin = ceiling
                            obs.append((g, i, idx_fcs, fbs_margin - site, SIGMA_FCS ** 2))
                        elif use_other and year < FCS_MIN_YEAR:
                            obs.append((g, tmap[fbs_school], idx_oth, fbs_margin - sgn * hfa_est, SIGMA_OTHER ** 2))
                elif use_other and (hd == "fbs") != (ad == "fbs"):
                    home_is_fbs = hd == "fbs"
                    fbs_school = g.home_team if home_is_fbs else g.away_team
                    if fbs_school in tmap:
                        fbs_margin = margin if home_is_fbs else -margin
                        sgn = 0.0 if g.neutral_site else (1.0 if home_is_fbs else -1.0)
                        obs.append((g, tmap[fbs_school], idx_oth, fbs_margin - sgn * hfa_est, SIGMA_OTHER ** 2))

                # Update cumulative record / scoring stats for FBS participants.
                for is_home, school, opp, div, opp_div, pts_for, pts_ag in (
                    (True, g.home_team, g.away_team, hd, ad, g.home_score, g.away_score),
                    (False, g.away_team, g.home_team, ad, hd, g.away_score, g.home_score),
                ):
                    if div == "fbs" and school in team_stats:
                        s = team_stats[school]
                        if pts_for > pts_ag:
                            s["wins"] += 1
                        elif pts_for < pts_ag:
                            s["losses"] += 1
                        else:
                            s["ties"] += 1
                        s["pf"] += pts_for
                        s["pa"] += pts_ag
                        s["margin_sum"] += pts_for - pts_ag
                        opp_kind = "fbs" if (opp_div == "fbs" and opp in tmap) else ("fcs" if opp_div == "fcs" else "other")
                        s["opponents"].append((opp_kind, opp))

            # Both fits start at the same pre-week state. Only the shared FCS
            # mean uses uncapped scores, expressed relative to the raw fit's FBS mean.
            if obs:
                if uncapped_observations:
                    raw = [uncapped_observations.get(index, item) for index, item in enumerate(obs)]
                    raw_mu, _, _ = _fit_week(mu, cov, raw)
                    raw_tier = raw_mu[idx_fcs] - raw_mu[:k].mean()
                mu, cov, r_effs = _fit_week(mu, cov, obs)
                game_impacts.update(_game_impacts(mu, cov, teams, obs, r_effs))
                if uncapped_observations:
                    mu[idx_fcs] = raw_tier + mu[:k].mean()

            post_map, post_fcs, post_oth = _centered()
            weekly_rankings[week] = _build_ranking_rows(
                teams, conferences, post_map, post_fcs, post_oth, year, team_stats
            )

        # Preserve any trailing empty ranking weeks that already existed in the archive (e.g., 1898 W14/W15).
        for extra_w in season.existing_ranking_weeks:
            if extra_w > max_completed_week and extra_w not in weekly_rankings:
                weekly_rankings[extra_w] = list(weekly_rankings[max_completed_week])

        # Generate upcoming slate spreads (e.g., 2026 Week 13) strictly from post-max_completed_week state.
        post_final_map, post_final_fcs, post_final_oth = _centered()
        upcoming_by_week: dict[int, list[ScheduledGame]] = defaultdict(list)
        for game in season.upcoming_games:
            if game.home_division.lower() == "fbs" and game.away_division.lower() == "fbs":
                upcoming_by_week[game.week].append(game)
        upcoming_spreads: dict[int, list[dict[str, Any]]] = {}
        for up_w, up_games in sorted(upcoming_by_week.items()):
            upcoming_spreads[up_w] = [
                _format_spread_row(
                    up_w, g.home_team, g.away_team, g.neutral_site,
                    post_final_map[g.home_team], post_final_map[g.away_team], hfa_est,
                )
                for g in sorted(up_games, key=lambda x: (x.home_team, x.away_team, x.neutral_site))
                if g.home_team in post_final_map and g.away_team in post_final_map
            ]

        final_uncertainty = {school: float(math.sqrt(max(cov[i, i], 0.0))) for i, school in enumerate(teams)}

        # End-of-season carryover persistence.
        fbs_mean = float(np.mean(mu[:k])) if k > 0 else 0.0
        for i, school in enumerate(teams):
            val = float(mu[i] - fbs_mean)
            st.prev_fast[school] = val
            st.prev_slow[school] = (
                val if school not in st.prev_slow
                else (1.0 - ALPHA_SLOW) * st.prev_slow[school] + ALPHA_SLOW * val
            )
            st.prev_var[school] = float(cov[i, i])
            st.last_seen_yr[school] = year
        if use_fcs and year >= FCS_MIN_YEAR:
            st.fcs_mean = float(mu[idx_fcs] - fbs_mean)
            st.fcs_var = float(cov[idx_fcs, idx_fcs])
        if use_other:
            st.other_mean = float(mu[idx_oth] - fbs_mean)
            st.other_var = float(cov[idx_oth, idx_oth])
        if season_hfa and year > genesis_year:
            wo = HFA_MEMORY * st.hfa_n
            wn = float(len(season_hfa))
            st.hfa_est = (wo * st.hfa_est + sum(season_hfa)) / (wo + wn)
            st.hfa_n = wo + wn

        results.append(
            SeasonReplay(
                year=year,
                hfa_used=hfa_est,
                fcs_rating_end=post_final_fcs,
                other_rating_end=post_final_oth,
                max_completed_week=max_completed_week,
                preseason=preseason_rows,
                weekly_rankings=weekly_rankings,
                final_ranking=list(weekly_rankings[max_completed_week]) if season.is_final else None,
                weekly_spreads=weekly_spreads,
                upcoming_spreads=upcoming_spreads,
                final_uncertainty=final_uncertainty,
                game_impacts=game_impacts,
            )
        )

    return results, st


def load_site_seasons(site: Path) -> list[SeasonInput]:
    """Read all archived seasons (rosters, completed games, upcoming slates) from website/."""
    years_root = site / "cfb/years"
    years = sorted(int(p.name) for p in years_root.iterdir() if p.is_dir() and p.name.isdecimal())
    inputs: list[SeasonInput] = []
    for year in years:
        folder = years_root / str(year)
        if not (folder / "rankings").exists() and (folder / "excitement").is_dir():
            continue  # The pre-1897 public game guides have no ranked season.
        pre_path = folder / f"rankings/{year}_PRESEASON_FBS_cors.html"
        if not pre_path.exists():
            pre_path = folder / f"rankings/{year}_W0_FBS_cors.html"
        pre_rows, _ = _read(pre_path)
        members = tuple(sorted({_school(r["school"]): r.get("conference", "—") for r in pre_rows}.items()))

        results_path = folder / f"data/results/{year}_FBS_results.html"
        res_rows, _ = _read(results_path)
        completed: list[CompletedGame] = []
        for r in res_rows:
            if r.get("home_score", "").strip() == "" or r.get("away_score", "").strip() == "":
                continue
            completed.append(
                CompletedGame(
                    week=int(r["week"]),
                    home_team=_school(r["home_team"]),
                    home_division=r["home_division"].strip().lower(),
                    home_score=int(r["home_score"]),
                    away_team=_school(r["away_team"]),
                    away_division=r["away_division"].strip().lower(),
                    away_score=int(r["away_score"]),
                    neutral_site=r["neutral_site"].strip().lower() == "true",
                    game_id=r.get("game_id", ""),
                    game_date=r.get("game_date", ""),
                )
            )
        max_completed = max((g.week for g in completed), default=-1)
        is_final = (folder / f"rankings/{year}_FINAL_FBS_cors.html").exists()

        upcoming: list[ScheduledGame] = []
        if not is_final:
            slate_dir = folder / "data/slate/weekly_slate"
            if slate_dir.exists():
                for sp in sorted(slate_dir.glob(f"{year}_W*_FBS_slate.html")):
                    w = int(sp.stem.split("_W", 1)[1].split("_", 1)[0])
                    if w <= max_completed:
                        continue
                    for r in _read(sp)[0]:
                        upcoming.append(
                            ScheduledGame(
                                week=w,
                                home_team=_school(r["home_team"]),
                                home_division=r["home_division"].strip().lower(),
                                away_team=_school(r["away_team"]),
                                away_division=r["away_division"].strip().lower(),
                                neutral_site=r["neutral_site"].strip().lower() == "true",
                            )
                        )

        existing_ranking_weeks = tuple(
            sorted(
                int(p.stem.split("_W", 1)[1].split("_", 1)[0])
                for p in (folder / "rankings").glob(f"{year}_W*_FBS_cors.html")
            )
        )
        existing_spread_weeks = tuple(
            sorted(
                int(p.stem.split("_W", 1)[1].split("_", 1)[0])
                for p in (folder / "spread").glob(f"{year}_W*_FBS_spread.html")
            )
        ) if (folder / "spread").exists() else ()
        has_reconstructed = (folder / "reconstructed").exists() and any(
            (folder / "reconstructed").glob(f"{year}_W*_FBS_spread.html")
        )

        inputs.append(
            SeasonInput(
                year=year,
                members=members,
                completed_games=tuple(completed),
                upcoming_games=tuple(upcoming),
                is_final=is_final,
                existing_ranking_weeks=existing_ranking_weeks,
                existing_spread_weeks=existing_spread_weeks,
                has_reconstructed=has_reconstructed,
            )
        )
    return inputs


def _write_html(site: Path, relative: str, raw: bytes) -> None:
    path = site / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    href = posixpath.relpath("cfb/cfb.html", posixpath.dirname(relative))
    path.write_bytes(raw.replace(b"__CORS_HOME__", href.encode()))


def _data_page(
    title: str,
    rows: list[dict[str, Any]],
    columns: tuple[str, ...],
    updated_at: str,
    *,
    note: str | None = None,
) -> bytes:
    body = f"<p>Last updated: {updated_at}</p>"
    if note:
        body += f"<p>{note}</p>"
    body += _row_table(rows, columns)
    return _page(title, body, '<a href="__CORS_HOME__">CORS home</a>')


def _snapshot_for_progression(season_input: SeasonInput) -> SeasonSnapshot:
    teams = tuple(SourceTeam(school, conf) for school, conf in season_input.members)
    games = tuple(
        SourceGame(
            g.week, g.home_team, g.home_division, g.home_score,
            g.away_team, g.away_division, g.away_score, g.neutral_site,
            provider_id=g.game_id or None, date=g.game_date or None,
            completed=True, disposition="completed",
        )
        for g in season_input.completed_games
    )
    max_w = max((g.week for g in games), default=0)
    metadata = {"schema_version": 3, "dataset_id": POLICY, "complete_through_week": max_w}
    state = {**metadata, "sport": "cfb", "classification": "FBS", "year": season_input.year}
    checksum = _checksum(state, teams, games)
    return SeasonSnapshot("cfb", "FBS", season_input.year, teams, games, metadata, checksum)


def rebuild_site(
    site: Path,
    *,
    updated_at: str | None = None,
    market_lines: MarketLines | None = None,
) -> dict[str, Any]:
    """Replay the full football archive and rebuild its static pages."""
    site = site.resolve()
    stamp = updated_at or datetime.now(timezone.utc).isoformat()
    season_inputs = load_site_seasons(site)
    if not season_inputs:
        raise ValueError(f"no college football seasons found at {site}")

    replays, final_state = replay_seasons(season_inputs, use_fcs=True, use_other=True)
    input_by_year = {s.year: s for s in season_inputs}
    from .game_excitement import retain_baseline_quality

    for replay in replays:
        year = replay.year
        s_in = input_by_year[year]
        prefix = f"cfb/years/{year}"
        retain_baseline_quality(site / prefix, year)

        # 1. Write PRESEASON, weekly W0..W<max>, and FINAL rankings.
        _write_html(
            site, f"{prefix}/rankings/{year}_PRESEASON_FBS_cors.html",
            _data_page(f"{year} PRESEASON CORS {MODEL_VERSION} rankings", replay.preseason, RANK_COLUMNS, stamp),
        )
        for week, rows in sorted(replay.weekly_rankings.items()):
            _write_html(
                site, f"{prefix}/rankings/{year}_W{week}_FBS_cors.html",
                _data_page(f"{year} W{week} CORS {MODEL_VERSION} rankings", rows, RANK_COLUMNS, stamp),
            )
        if replay.final_ranking is not None:
            _write_html(
                site, f"{prefix}/rankings/{year}_FINAL_FBS_cors.html",
                _data_page(f"{year} FINAL CORS {MODEL_VERSION} rankings", replay.final_ranking, RANK_COLUMNS, stamp),
            )

        # 2. Write weekly spreads (all played weeks + upcoming active slate weeks).
        spread_weeks = sorted(
            set(w for w, rows in replay.weekly_spreads.items() if rows)
            | set(s_in.existing_spread_weeks)
        )
        for week in spread_weeks:
            rows = replay.weekly_spreads.get(week, [])
            if week in replay.upcoming_spreads:
                rows = replay.upcoming_spreads[week]
            _write_html(
                site, f"{prefix}/spread/{year}_W{week}_FBS_spread.html",
                _data_page(
                    f"{year} W{week} CORS {MODEL_VERSION} spreads",
                    rows, SPREAD_COLUMNS, stamp,
                    note=f"Calculated from prior-week CORS {MODEL_VERSION} ratings (league HFA {replay.hfa_used:.2f} pts).",
                ),
            )
        for up_week, up_rows in sorted(replay.upcoming_spreads.items()):
            _write_html(
                site, f"{prefix}/spread/{year}_W{up_week}_FBS_spread.html",
                _data_page(
                    f"{year} W{up_week} CORS {MODEL_VERSION} spreads",
                    up_rows, SPREAD_COLUMNS, stamp,
                    note=f"Calculated from prior-week CORS {MODEL_VERSION} ratings (league HFA {replay.hfa_used:.2f} pts).",
                ),
            )

        # 3. Update any existing reconstructed/ spread files so zero v0.4.0 spreads remain.
        if s_in.has_reconstructed:
            recon_dir = site / prefix / "reconstructed"
            recon_weeks = sorted(
                set(int(p.stem.split("_W", 1)[1].split("_", 1)[0]) for p in recon_dir.glob(f"{year}_W*_FBS_spread.html"))
                | set(range(replay.max_completed_week + 1))
            )
            for week in recon_weeks:
                rows = replay.weekly_spreads.get(week, [])
                _write_html(
                    site, f"{prefix}/reconstructed/{year}_W{week}_FBS_spread.html",
                    _data_page(
                        f"{year} Week {week} CORS {MODEL_VERSION} forecasts",
                        rows, SPREAD_COLUMNS, stamp,
                        note="Calculated from prior-week CORS ratings.",
                    ),
                )

        # 4. Update progression outputs where they exist (e.g., 2025).
        prog_base = f"{prefix}/history/{year}_FBS_progression"
        if (site / (prog_base + ".html")).exists():
            snapshot = _snapshot_for_progression(s_in)
            # Convert numeric strings to floats for ranking_progression contract.
            def _num_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [{**r, "cors": float(r["cors"]), "rank": int(r["rank"])} for r in rows]

            _, rendered_prog, serialized_prog = build_progression_outputs(
                snapshot,
                {w: _num_rows(r) for w, r in replay.weekly_rankings.items() if w <= replay.max_completed_week},
                _num_rows(replay.preseason),
                final_rows=_num_rows(replay.final_ranking) if replay.final_ranking is not None else None,
                phase="final" if replay.final_ranking is not None else "week",
                target_week=replay.max_completed_week,
                dataset_id=POLICY,
                model_version=MODEL_VERSION,
            )
            _write_html(site, prog_base + ".html", rendered_prog.encode("utf-8"))
            (site / (prog_base + ".json")).write_text(serialized_prog, encoding="utf-8")

        # 5. Update the season landing title before the record book runs.
        landing_path = site / prefix / f"{year}_CFB.html"
        if landing_path.exists():
            raw_landing = landing_path.read_text(encoding="utf-8", errors="replace")
            updated_landing = re.sub(
                r"CORS v[0-9]+(?:\.[0-9]+)+", f"CORS {MODEL_VERSION}", raw_landing
            )
            if updated_landing != raw_landing:
                landing_path.write_text(updated_landing, encoding="utf-8")

        # 6. Update season metadata.json if present (e.g., 2026).
        metadata_path = site / prefix / "metadata.json"
        if metadata_path.exists():
            meta = json.loads(metadata_path.read_bytes())
            meta["model_version"] = MODEL_VERSION
            meta["last_updated"] = stamp
            meta["cors_v1_hfa"] = round(replay.hfa_used, 6)
            metadata_path.write_text(
                json.dumps(meta, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    # 7. Rebuild ALL model performance reports (1897-2026) and ALL weekly spread_results pages.
    perf_files = build_performance_files(
        site,
        reconstruct_all=False,
        market_lines=market_lines,
        model_version=MODEL_VERSION,
        include_all_weekly_results=True,
    )
    for relative, raw in perf_files.items():
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    # 8. Rebuild the entire CFB static record book (teams, conferences, seasons, checkpoints,
    #    national champions, worst teams, homepage, 404, and archive decorations).
    from .record_book import build as build_record_book
    seasons_count, programs_count, changed_files = build_record_book(site, replays=replays)

    latest = replays[-1]
    return {
        "model_version": MODEL_VERSION,
        "policy": POLICY,
        "seasons": seasons_count,
        "programs": programs_count,
        "record_book_changed": changed_files,
        "performance_files": len(perf_files),
        "latest_year": latest.year,
        "latest_week": latest.max_completed_week,
        "latest_hfa": latest.hfa_used,
        "latest_top5": [(r["rank"], r["school"], r["cors"]) for r in latest.weekly_rankings[latest.max_completed_week][:5]],
        "upcoming_weeks": {w: len(rows) for w, rows in latest.upcoming_spreads.items()},
        "final_fcs_rating": final_state.fcs_mean,
        "final_other_rating": final_state.other_mean,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path("website"), help="generated static site root")
    args = parser.parse_args(argv)
    summary = rebuild_site(args.site)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
