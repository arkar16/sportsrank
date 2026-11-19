"""Run the offline 2022–25 FBS archive-week development experiment."""

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import json
from math import fsum, log10, sqrt
from pathlib import Path
import tempfile

from cfb.historical_rebuild import historical_carryover, local_day
from cfb.performance import _integer, _neutral, _read
from cfb.ranking_engine import (
    HFA, MODEL_VERSION, PreviousFinal, _margin_of_victory,
    _previous_final_from_rows, _pythagorean_expected, _sort_rankings, _sos,
    natural_matchup, preseason_ranking, records_for_week,
)
from cfb.season_snapshot import SeasonSnapshot
from cfb.season_source import SourceGame, SourceTeam


SEED_YEAR = 2021
WARMUP_YEAR = 2022
FIRST_FIT_YEAR = 2023
EVALUATION_YEARS = (2024, 2025)
YEARS = (WARMUP_YEAR, FIRST_FIT_YEAR, *EVALUATION_YEARS)
OUTPUT = Path(".sportsrank/football-development.json")
CORS_NORMALIZER = 50 + (log10(31) * 4) * 1.2
RANK_COLUMNS = ("rank", "school", "conference", "record", "win_pct", "cors", "mov", "sos",
                "expected_wins", "wins_vs_expected")


@dataclass(frozen=True)
class Candidate:
    hfa: float = HFA
    mov_coefficient: float = 4.0
    carryover_scale: float = 1.0


# Vary one parameter at a time. Prefer the baseline on an exact fit tie.
BASELINE = Candidate()
GRID = (
    BASELINE,
    Candidate(hfa=HFA - 1),
    Candidate(hfa=HFA + 1),
    Candidate(mov_coefficient=2.0),
    Candidate(mov_coefficient=6.0),
    Candidate(carryover_scale=0.5),
    Candidate(carryover_scale=1.5),
)


@dataclass(frozen=True)
class TeamWeek:
    record: dict
    games: tuple[SourceGame, ...]
    mov: float
    expected_wins: float


@dataclass(frozen=True)
class PreparedWeek:
    games: tuple[SourceGame, ...]
    teams: tuple[TeamWeek, ...]


@dataclass(frozen=True)
class Replay:
    preseason: list[dict]
    rankings: tuple[list[dict], ...]
    margins: dict[str, Decimal]


def season_root(website: Path, year: int) -> Path:
    return website / "cfb" / "years" / str(year)


def ranking_path(root: Path, year: int, checkpoint: str) -> Path:
    return root / "rankings" / f"{year}_{checkpoint}_FBS_cors.html"


def load_season(root: Path, year: int) -> SeasonSnapshot:
    """Read the corrected archive directly, preserving game order and week labels."""
    rankings, _ = _read(ranking_path(root, year, "FINAL"))
    teams = tuple(SourceTeam(row["school"], row["conference"]) for row in rankings)
    members = {team.school for team in teams}
    if not teams or len(members) != len(teams):
        raise ValueError(f"{year}: invalid FINAL membership")

    results, digest = _read(root / "data/results" / f"{year}_FBS_results.html")
    if not results:
        raise ValueError(f"{year}: no archived results")
    rows_by_week = defaultdict(list)
    dates_by_week = defaultdict(list)
    games = []
    ids = set()
    for row in results:
        week = _integer(row["week"])
        identifier = row["game_id"]
        home, away = row["home_team"], row["away_team"]
        home_class, away_class = row["home_division"], row["away_division"]
        if not identifier or identifier in ids or not home or not away or home == away:
            raise ValueError(f"{year} Week {week}: invalid or duplicate archived matchup {identifier!r}")
        if (home_class.lower() == "fbs") != (home in members) or (
            (away_class.lower() == "fbs") != (away in members)
        ):
            raise ValueError(f"{year} Week {week}: archived classification disagrees with membership")
        ids.add(identifier)
        rows_by_week[week].append(row)
        if row["game_date"]:
            dates_by_week[week].append(local_day(row["game_date"]))
        games.append(SourceGame(
            week, home, home_class, _integer(row["home_score"]),
            away, away_class, _integer(row["away_score"]), _neutral(row["neutral_site"]),
            provider_id=identifier, date=row["game_date"] or None,
            completed=True, disposition="completed",
        ))

    through = max(rows_by_week)
    for week in range(through + 1):
        path = root / "data/results/weekly_results" / f"{year}_W{week}_FBS_results.html"
        weekly, _ = _read(path)
        if weekly != rows_by_week[week]:
            raise ValueError(f"{year} Week {week}: weekly and season result tables disagree")
    dated_weeks = [dates_by_week[week] for week in sorted(dates_by_week)]
    if any(max(earlier) >= min(later) for earlier, later in zip(dated_weeks, dated_weeks[1:])):
        raise ValueError(f"{year}: dates overlap or reverse across archived weeks")
    return SeasonSnapshot("cfb", "FBS", year, teams, tuple(games), {"results_sha256": digest}, "")


def prepare_weeks(snapshot: SeasonSnapshot) -> tuple[PreparedWeek, ...]:
    """Cache candidate-independent stats on each prefix in archive source order."""
    weeks = []
    for week in range(snapshot.complete_through_week + 1):
        through_week = tuple(game for game in snapshot.games if game.week <= week)
        prefix = replace(snapshot, games=through_week)
        by_school = defaultdict(list)
        matchups = []
        for game in through_week:
            by_school[game.home_team].append(game)
            by_school[game.away_team].append(game)
            if game.week == week:
                matchups.append(game)
        teams = []
        for row in records_for_week(prefix, week):
            school = row["school"]
            games = tuple(by_school[school])
            teams.append(TeamWeek(row, games, _margin_of_victory(school, games),
                                  _pythagorean_expected(school, games)))
        weeks.append(PreparedWeek(tuple(matchups), tuple(teams)))
    return tuple(weeks)


def replay_season(snapshot: SeasonSnapshot, weeks: tuple[PreparedWeek, ...],
                  prior: PreviousFinal, candidate: Candidate) -> Replay:
    members = {team.school for team in snapshot.teams}
    carried, _ = historical_carryover(snapshot.year, prior, set(prior.cors), members)
    preseason = preseason_ranking(snapshot, carried)
    # Scale the standard, rounded PRESEASON signal for returning teams.
    # Entrants retain their historical classification baseline.
    for row in preseason:
        if row["school"] in prior.cors:
            row["cors"] = round(row["cors"] * candidate.carryover_scale, 2)
    preseason = _sort_rankings(preseason)
    ranking = preseason
    rankings = []
    margins = {}
    for week, prepared in enumerate(weeks):
        previous = {row["school"]: row["cors"] for row in ranking}
        for game in prepared.games:
            if game.home_team in members and game.away_team in members:
                margins[game.provider_id] = natural_matchup(
                    previous[game.home_team], previous[game.away_team],
                    neutral_site=game.neutral_site, hfa=candidate.hfa,
                ).home_margin

        # Historical W0 aliases PRESEASON when there were no scored W0 games.
        if week or prepared.games:
            rows = []
            for team in prepared.teams:
                stats = team.record
                school = stats["school"]
                sos = _sos(school, team.games, previous)
                # Mirror the v0.4.0 evaluation order, varying only the MOV
                # term. The SOS normalization stays fixed across candidates.
                scaled_sos = 0.8 + (sos / CORS_NORMALIZER) * (1.2 - 0.8)
                win_term = round((stats["win_pct"] * 100) / 2, 2)
                mov_term = log10(1 + abs(team.mov)) * candidate.mov_coefficient
                if team.mov <= 0:
                    mov_term = -mov_term
                cors = ((win_term + mov_term) * scaled_sos + previous[school] / max(1, week)) / 2
                rows.append({
                    **stats, "cors": round(cors, 2), "mov": round(team.mov, 2), "sos": sos,
                    "expected_wins": team.expected_wins,
                    "wins_vs_expected": round(stats["wins"] - team.expected_wins, 2),
                })
            ranking = _sort_rankings(rows)
        rankings.append(ranking)
    return Replay(preseason, tuple(rankings), margins)


def verify_baseline(root: Path, year: int, weeks: tuple[PreparedWeek, ...], replay: Replay) -> None:
    def ranking_values(row):
        return tuple(row[column] if column in {"school", "conference", "record"}
                     else Decimal(str(row[column])) for column in RANK_COLUMNS)

    checkpoints = [("PRESEASON", replay.preseason),
                   *((f"W{week}", rows) for week, rows in enumerate(replay.rankings)),
                   ("FINAL", replay.rankings[-1])]
    for checkpoint, actual in checkpoints:
        expected, _ = _read(ranking_path(root, year, checkpoint))
        if list(map(ranking_values, expected)) != list(map(ranking_values, actual)):
            raise ValueError(f"{year} {checkpoint}: baseline ranking disagrees with archive")

    for week, prepared in enumerate(weeks):
        expected, _ = _read(root / "reconstructed" / f"{year}_W{week}_FBS_spread.html")
        reference = sorted((row["home_team"], row["away_team"], _neutral(row["neutral_site"]),
                            Decimal(row["home_margin"])) for row in expected)
        actual = sorted((game.home_team, game.away_team, game.neutral_site, replay.margins[game.provider_id])
                        for game in prepared.games if game.provider_id in replay.margins)
        if reference != actual or any(_integer(row["week"]) != week for row in expected):
            raise ValueError(f"{year} Week {week}: baseline margins disagree with archive reconstruction")


def verify_cutoff(snapshot: SeasonSnapshot, prior: PreviousFinal,
                  candidate: Candidate, replay: Replay) -> list[int]:
    last = snapshot.complete_through_week
    checked = sorted({0, min(1, last), last // 2, last})
    for cutoff in checked:
        # Change the forecast week's scores and drop every later result.
        altered = replace(snapshot, games=tuple(
            replace(game, home_points=game.home_points + 100) if game.week == cutoff else game
            for game in snapshot.games if game.week <= cutoff
        ))
        alternative = replay_season(altered, prepare_weeks(altered), prior, candidate)
        expected = {g.provider_id: replay.margins[g.provider_id]
                    for g in altered.games if g.provider_id in replay.margins}
        if alternative.margins != expected:
            raise ValueError(f"{snapshot.year} Week {cutoff}: cutoff intervention changed a pregame margin")
    return checked


def verify_hfa(snapshot: SeasonSnapshot, baseline: Replay, alternative: Replay, hfa: float) -> int:
    neutral = 0
    for game in snapshot.games:
        identifier = game.provider_id
        if identifier in baseline.margins:
            expected = Decimal(0) if game.neutral_site else Decimal(str(hfa)) - Decimal(str(HFA))
            if alternative.margins[identifier] - baseline.margins[identifier] != expected:
                raise ValueError(f"{snapshot.year} Week {game.week}: incorrect HFA for {identifier}")
            neutral += game.neutral_site
    return neutral


def final_ratings(year: int, replay: Replay) -> PreviousFinal:
    final = replay.rankings[-1]
    return PreviousFinal({row["school"]: row["cors"] for row in final},
                         {row["school"]: row["wins_vs_expected"] for row in final}, year, "FBS")


def pairs(snapshot: SeasonSnapshot, replay: Replay) -> list[tuple[float, int]]:
    return [(float(replay.margins[game.provider_id]), game.home_points - game.away_points)
            for game in snapshot.games if game.provider_id in replay.margins]


def squared_error(rows: list[tuple[float, int]]) -> float:
    return fsum((prediction - actual) ** 2 for prediction, actual in rows)


def score(rows: list[tuple[float, int]]) -> dict:
    decided = [(prediction, actual) for prediction, actual in rows if prediction and actual]
    correct = sum((prediction > 0) == (actual > 0) for prediction, actual in decided)
    return {
        "games": len(rows), "rmse": sqrt(squared_error(rows) / len(rows)),
        "mae": fsum(abs(prediction - actual) for prediction, actual in rows) / len(rows),
        "straight_up_accuracy": correct / len(decided) if decided else None,
        "correct_picks": correct, "decided_picks": len(decided),
        "pick_ems": sum(prediction == 0 for prediction, _ in rows),
        "actual_ties": sum(actual == 0 for _, actual in rows),
    }


def rolling_scores(development: dict[Candidate, dict[int, list[tuple[float, int]]]]) -> dict:
    pooled = {"baseline": [], "rolling_tuned": []}
    folds = []
    for year in EVALUATION_YEARS:
        fit_years = list(range(FIRST_FIT_YEAR, year))
        fit_rows = {candidate: [row for fit_year in fit_years for row in development[candidate][fit_year]]
                    for candidate in GRID}
        selected = min(GRID, key=lambda candidate: squared_error(fit_rows[candidate]))
        evaluation = {"baseline": development[BASELINE][year], "rolling_tuned": development[selected][year]}
        for strategy, rows in evaluation.items():
            pooled[strategy].extend(rows)
        folds.append({
            "evaluation_year": year, "fit_years": fit_years, "selected": asdict(selected),
            "grid_fit_rmse": [{"candidate": asdict(candidate),
                               "rmse": sqrt(squared_error(rows) / len(rows))}
                              for candidate, rows in fit_rows.items()],
            "scores": {strategy: score(rows) for strategy, rows in evaluation.items()},
        })
    return {"folds": folds, "pooled": {strategy: score(rows) for strategy, rows in pooled.items()}}


def run_experiment(website: Path) -> dict:
    seed_rows, seed_digest = _read(ranking_path(season_root(website, SEED_YEAR), SEED_YEAR, "FINAL"))
    seed = replace(_previous_final_from_rows(seed_rows), year=SEED_YEAR, classification="FBS")
    priors = dict.fromkeys(GRID, seed)
    development = {candidate: {} for candidate in GRID}
    checks = []
    for year in YEARS:
        root = season_root(website, year)
        snapshot = load_season(root, year)
        weeks = prepare_weeks(snapshot)
        replays = {candidate: replay_season(snapshot, weeks, priors[candidate], candidate)
                   for candidate in GRID}
        baseline = replays[BASELINE]
        verify_baseline(root, year, weeks, baseline)
        neutral = verify_hfa(snapshot, baseline, replays[GRID[1]], GRID[1].hfa)
        cutoff_weeks = verify_cutoff(snapshot, priors[GRID[-1]], GRID[-1], replays[GRID[-1]])
        for candidate, replay in replays.items():
            if replay.margins.keys() != baseline.margins.keys() or not replay.margins:
                raise ValueError(f"{year}: candidate coverage differs from baseline or is empty")
            if year >= FIRST_FIT_YEAR:
                development[candidate][year] = pairs(snapshot, replay)
            priors[candidate] = final_ratings(year, replay)
        checks.append({
            "year": year, "results_sha256": snapshot.metadata["results_sha256"],
            "archived_results": len(snapshot.games), "dated_results": sum(bool(g.date) for g in snapshot.games),
            "weekly_result_tables_verified": len(weeks), "ranking_checkpoints_matched": len(weeks) + 2,
            "reconstructed_margins_matched": len(baseline.margins),
            "neutral_margins_invariant_to_hfa": neutral, "cutoff_intervention_weeks": cutoff_weeks,
        })
    return {
        "experiment": "FBS archived-week rolling development",
        "baseline_model": MODEL_VERSION,
        "provenance": (
            "Retrospective scores and season membership from the corrected archive. Each matchup uses "
            "only earlier numbered weeks (PRESEASON for Week 0) and season membership. Archive week labels "
            "do not attest kickoff-time availability or original issuance. The 2024–25 result tables have no dates."
        ),
        "seed": {"year": SEED_YEAR, "checkpoint": "FINAL", "sha256": seed_digest},
        "warmup_year": WARMUP_YEAR, "first_fit_year": FIRST_FIT_YEAR,
        "cohort": "Completed FBS-vs-FBS games. Other archived games contribute to team ratings.",
        "selection": "Pooled prior-year game RMSE, then grid order. Warmup contributes only to ratings.",
        "carryover_scale": "Multiply returning teams' standard rounded PRESEASON CORS by the scale and round to two decimals.",
        "scoring": "MAE and RMSE include all paired games. Accuracy excludes exact pick'ems and actual ties.",
        "checks": checks,
        **rolling_scores(development),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--website", type=Path, default=Path("website"))
    args = parser.parse_args(argv)
    try:
        result = run_experiment(args.website)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=OUTPUT.parent, delete_on_close=False) as file:
            json.dump(result, file, indent=2, allow_nan=False)
            file.write("\n")
            file.close()
            Path(file.name).replace(OUTPUT)
    except (OSError, ValueError, ArithmeticError, KeyError) as error:
        parser.exit(1, f"football development experiment failed: {error}\n")
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
