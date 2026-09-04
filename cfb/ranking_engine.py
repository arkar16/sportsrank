"""Pure CFB ranking, records, and spread calculations.

The legacy implementation in :mod:`cfb.cors` writes files and changes the
process working directory.  This module is the calculation seam used by
Release generation.  It intentionally retains the CORS v0.4.0 constants and
formula, but accepts an immutable :class:`SeasonSnapshot` and returns plain
Python values so callers can render or validate without I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from io import StringIO

import pandas as pd

try:  # Package imports are used by tests and ``python -m cfb``.
    from .season_snapshot import SeasonSnapshot
    from .season_source import SourceGame, SourceTeam
except ImportError:  # Preserve direct execution from cfb/ for legacy users.
    from season_snapshot import SeasonSnapshot
    from season_source import SourceGame, SourceTeam


MODEL_VERSION = "v0.4.0"
CORS_VERSION = MODEL_VERSION
FCS_CORS = -10.0
HFA = 2.0
REGRESSION_FACTOR = 1.75
TIE_BREAK = "cors desc, wins desc, losses asc, school asc"


@dataclass(frozen=True)
class PreviousFinal:
    """A prior final ranking used for Season Carryover.

    ``cors`` and ``wins_vs_expected`` are keyed by school.  The latter is
    optional in old static files and defaults to zero during readjustment.
    """

    cors: Mapping[str, float]
    wins_vs_expected: Mapping[str, float]


class RankingEngine:
    """Small state-free facade around the pure calculation functions."""

    def __init__(self, model_version: str = MODEL_VERSION) -> None:
        if model_version != MODEL_VERSION:
            raise ValueError(f"Unsupported CORS model version: {model_version}")
        self.model_version = model_version

    def records(self, snapshot: SeasonSnapshot, week: int) -> list[dict[str, Any]]:
        return records_for_week(snapshot, week)

    def ranking(
        self,
        snapshot: SeasonSnapshot,
        week: int,
        previous_cors: Mapping[str, float] | None = None,
        previous_wins_vs_expected: Mapping[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        return ranking_for_week(snapshot, week, previous_cors, previous_wins_vs_expected, model_version=self.model_version)

    calculate = ranking

    def season(
        self,
        snapshot: SeasonSnapshot,
        target_week: int | None = None,
        previous_final: PreviousFinal | Mapping[str, float] | None = None,
    ) -> dict[int, list[dict[str, Any]]]:
        return season_rankings(snapshot, target_week, previous_final, model_version=self.model_version)

    calculate_season = season

    def spreads(self, snapshot: SeasonSnapshot, week: int, rankings: Sequence[Mapping[str, Any]], *, hfa: float = HFA) -> list[dict[str, Any]]:
        return spreads_for_week(snapshot, week, rankings, hfa=hfa)

    calculate_spreads = spreads
    records_for_week = records


CORSRankingEngine = RankingEngine


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def is_completed(game: SourceGame | Mapping[str, Any]) -> bool:
    """Return whether both sides of a game have a finite score."""

    if isinstance(game, Mapping):
        home = game.get("home_points", game.get("home_score"))
        away = game.get("away_points", game.get("away_score"))
    else:
        home = game.home_points
        away = game.away_points
    if home is None or away is None:
        return False
    try:
        return math.isfinite(float(home)) and math.isfinite(float(away))
    except (TypeError, ValueError):
        return False


def _games(snapshot: SeasonSnapshot, through_week: int | None = None) -> tuple[SourceGame, ...]:
    """Completed games through ``through_week`` in stable source order."""

    if through_week is None:
        through_week = snapshot.complete_through_week
    return tuple(
        game
        for game in snapshot.games
        if int(game.week) <= int(through_week) and is_completed(game)
    )


def completed_games(snapshot: SeasonSnapshot, through_week: int | None = None) -> tuple[SourceGame, ...]:
    """Public completed-game filter used by calculations and validators."""

    return _games(snapshot, through_week)


def scheduled_games(snapshot: SeasonSnapshot, through_week: int | None = None) -> tuple[SourceGame, ...]:
    """Scheduled games through a boundary, including not-yet-scored games."""

    if through_week is None:
        through_week = snapshot.complete_through_week
    return tuple(game for game in snapshot.games if int(game.week) <= int(through_week))


def scheduled_season_end_week(snapshot: SeasonSnapshot) -> int:
    """Return the scheduled season boundary present in the snapshot.

    CFBD's season-games response includes future scheduled games as well as
    completed games.  The greatest week in that immutable response is the
    only end-of-season signal available to an offline renderer; calendar
    guesses are intentionally not part of the release contract.
    """

    return max((int(game.week) for game in snapshot.games), default=0)


def season_is_complete(snapshot: SeasonSnapshot) -> bool:
    """Whether every scheduled game reaches the snapshot's completion edge."""

    if not snapshot.games:
        return False
    scheduled_end = scheduled_season_end_week(snapshot)
    return int(snapshot.complete_through_week) >= scheduled_end and all(
        is_completed(game) for game in snapshot.games
    )


def season_end_week(snapshot: SeasonSnapshot, *, completed_only: bool = True) -> int:
    """Derive a season checkpoint or scheduled season boundary.

    ``completed_only=True`` preserves the legacy helper's meaning.  Release
    completion checks use :func:`scheduled_season_end_week` and
    :func:`season_is_complete` so a partial snapshot cannot be mislabeled as
    a final season.
    """

    if completed_only:
        return max(0, int(snapshot.complete_through_week))
    return scheduled_season_end_week(snapshot)


end_week = season_end_week
scheduled_end_week = scheduled_season_end_week
is_season_complete = season_is_complete


def _teams(snapshot: SeasonSnapshot) -> tuple[SourceTeam, ...]:
    # Sorting gives deterministic base order without mutating the snapshot.
    return tuple(sorted(snapshot.teams, key=lambda team: (team.school, team.conference)))


def _team_map(snapshot: SeasonSnapshot) -> dict[str, SourceTeam]:
    result: dict[str, SourceTeam] = {}
    for team in snapshot.teams:
        if team.school in result:
            raise ValueError(f"Duplicate FBS team in snapshot: {team.school}")
        result[team.school] = team
    return result


def records_for_week(snapshot: SeasonSnapshot, week: int) -> list[dict[str, Any]]:
    """Compute records from completed games through ``week``.

    Games against FCS and other non-FBS opponents count toward the FBS team's
    record when one side is a snapshot team.  A game with an unknown FBS side
    is ignored rather than inventing a team in the ranking set.
    """

    teams = _team_map(snapshot)
    stats = {
        school: {"wins": 0, "losses": 0, "ties": 0}
        for school in teams
    }
    for game in _games(snapshot, week):
        home_fbs = game.home_team in teams
        away_fbs = game.away_team in teams
        if not home_fbs and not away_fbs:
            continue
        home_score = float(game.home_points)  # is_completed checked above
        away_score = float(game.away_points)
        if home_fbs and away_fbs:
            if home_score > away_score:
                stats[game.home_team]["wins"] += 1
                stats[game.away_team]["losses"] += 1
            elif away_score > home_score:
                stats[game.away_team]["wins"] += 1
                stats[game.home_team]["losses"] += 1
            else:
                stats[game.home_team]["ties"] += 1
                stats[game.away_team]["ties"] += 1
        else:
            fbs_team = game.home_team if home_fbs else game.away_team
            fbs_score = home_score if home_fbs else away_score
            opponent_score = away_score if home_fbs else home_score
            if fbs_score > opponent_score:
                stats[fbs_team]["wins"] += 1
            elif opponent_score > fbs_score:
                stats[fbs_team]["losses"] += 1
            else:
                stats[fbs_team]["ties"] += 1

    rows: list[dict[str, Any]] = []
    for team in _teams(snapshot):
        values = stats[team.school]
        wins, losses, ties = values["wins"], values["losses"], values["ties"]
        # CORS v0.4.0 historically uses wins/(wins+losses) internally.  The
        # legacy record presentation, however, includes ties in its
        # pre-1996 denominator.  Keep those concerns separate: this row's
        # win_pct is presentation data while _cors_value below retains the
        # established calculation denominator.
        denominator = wins + losses + ties if snapshot.year < 1996 else wins + losses
        record = f"{wins}-{losses}-{ties}" if snapshot.year < 1996 else f"{wins}-{losses}"
        rows.append(
            {
                "school": team.school,
                "conference": team.conference,
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "record": record,
                "win_pct": round(wins / denominator, 2) if denominator else 0.0,
            }
        )
    return rows


def _margin_of_victory(team: str, games: Iterable[SourceGame]) -> float:
    margins: list[float] = []
    for game in games:
        if game.home_team == team:
            margins.append(float(game.home_points) - float(game.away_points))
        elif game.away_team == team:
            margins.append(float(game.away_points) - float(game.home_points))
    return sum(margins) / len(margins) if margins else 0.0


def _sos(team: str, games: Iterable[SourceGame], previous_cors: Mapping[str, float]) -> float:
    opponents: list[float] = []
    for game in games:
        if game.home_team == team:
            opponents.append(_number(previous_cors.get(game.away_team), FCS_CORS))
        elif game.away_team == team:
            opponents.append(_number(previous_cors.get(game.home_team), FCS_CORS))
    return round(sum(opponents) / len(opponents), 2) if opponents else 0.0


def _pythagorean_expected(team: str, games: Iterable[SourceGame]) -> float:
    points_for = points_against = 0.0
    count = 0
    for game in games:
        if game.home_team == team:
            points_for += float(game.home_points)
            points_against += float(game.away_points)
            count += 1
        elif game.away_team == team:
            points_for += float(game.away_points)
            points_against += float(game.home_points)
            count += 1
    if count == 0:
        return 0.0
    exponent = 2.37
    if points_against == 0:
        expectation = 1.0 if points_for > 0 else 0.0
    else:
        numerator = points_for**exponent
        expectation = numerator / (numerator + points_against**exponent)
    return round(expectation * count, 2)


def _cors_value(
    team: str,
    week: int,
    stats: Mapping[str, Any],
    games: tuple[SourceGame, ...],
    previous_cors: Mapping[str, float],
) -> float:
    wins = int(stats["wins"])
    losses = int(stats["losses"])
    denominator = wins + losses
    win_pct = round(wins / denominator, 2) if denominator else 0.0
    cors_max = 50 + (math.log10(31) * 4) * 1.2
    norm_sos = _sos(team, games, previous_cors) / cors_max
    win_pct_m = round((win_pct * 100) / 2, 2)
    mov = _margin_of_victory(team, games)
    if mov > 0:
        net_mov = math.log10(1 + mov) * 4
    else:
        net_mov = -math.log10(1 + abs(mov)) * 4
    scaled_sos = 0.8 + (norm_sos * (1.2 - 0.8))
    previous = _number(previous_cors.get(team), FCS_CORS)
    # The week divisor is a deliberate v0.4.0 behavior.  Week zero is handled
    # by carryover below and therefore never divides by zero.
    value = (((win_pct_m + net_mov) * scaled_sos) + (previous / max(1, week))) / 2
    return round(value, 2)


def _sort_rankings(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -_number(row.get("cors")),
            -int(row.get("wins", 0)),
            int(row.get("losses", 0)),
            str(row.get("school", "")),
        ),
    )
    for rank, row in enumerate(ordered, 1):
        row["rank"] = rank
    return ordered


def ranking_for_week(
    snapshot: SeasonSnapshot,
    week: int,
    previous_cors: Mapping[str, float] | None = None,
    previous_wins_vs_expected: Mapping[str, float] | None = None,
    *,
    model_version: str = MODEL_VERSION,
    regression_factor: float = REGRESSION_FACTOR,
) -> list[dict[str, Any]]:
    """Calculate one deterministic ranking table.

    For Week 0, previous final values are carried over and readjusted with the
    legacy 1.75 regression factor.  For later weeks, ``previous_cors`` is the
    previous week's ranking.  Missing carryover values use the existing FCS
    constant (-10), matching the old ``week_zero_readjust`` behavior.
    """

    if model_version != MODEL_VERSION:
        raise ValueError(f"Unsupported CORS model version: {model_version}")
    if week < 0:
        raise ValueError("week must be non-negative")
    _team_map(snapshot)  # Validate duplicate teams before producing output.
    previous_cors = dict(previous_cors or {})
    previous_wins_vs_expected = dict(previous_wins_vs_expected or {})
    stats_rows = records_for_week(snapshot, week)
    games = _games(snapshot, week)
    rows: list[dict[str, Any]] = []

    for stats in stats_rows:
        school = stats["school"]
        expected = _pythagorean_expected(school, games)
        wins_vs_expected = round(int(stats["wins"]) - expected, 2)
        if week == 0:
            if snapshot.year == 1897:
                cors = 0.0
            else:
                carried = _number(previous_cors.get(school), FCS_CORS)
                regression = _number(previous_wins_vs_expected.get(school), 0.0)
                cors = round(carried - (regression * regression_factor), 2)
            mov = 0.0
            sos = 0.0
        else:
            cors = _cors_value(school, week, stats, games, previous_cors)
            mov = round(_margin_of_victory(school, games), 2)
            sos = _sos(school, games, previous_cors)
        rows.append(
            {
                **stats,
                "cors": cors,
                "mov": mov,
                "sos": sos,
                "expected_wins": expected,
                "wins_vs_expected": wins_vs_expected,
            }
        )
    return _sort_rankings(rows)


def season_rankings(
    snapshot: SeasonSnapshot,
    target_week: int | None = None,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    *,
    model_version: str = MODEL_VERSION,
) -> dict[int, list[dict[str, Any]]]:
    """Calculate Week 0 through ``target_week`` using one snapshot."""

    if target_week is None:
        target_week = max(0, snapshot.complete_through_week)
    target_week = int(target_week)
    if target_week < 0:
        raise ValueError("target_week must be non-negative")
    if isinstance(previous_final, (str, Path)):
        previous_final = load_previous_final_model(previous_final)
    if isinstance(previous_final, PreviousFinal):
        prior_cors = dict(previous_final.cors)
        prior_wve = dict(previous_final.wins_vs_expected)
    else:
        prior_cors = dict(previous_final or {})
        prior_wve = {}
    rankings: dict[int, list[dict[str, Any]]] = {}
    for week in range(target_week + 1):
        current = ranking_for_week(
            snapshot,
            week,
            prior_cors,
            prior_wve,
            model_version=model_version,
        )
        rankings[week] = current
        prior_cors = {row["school"]: float(row["cors"]) for row in current}
        prior_wve = {row["school"]: float(row["wins_vs_expected"]) for row in current}
    return rankings


def ranking_engine(
    snapshot: SeasonSnapshot,
    week: int,
    previous_final: PreviousFinal | Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Small functional alias for integrations that prefer a verb-free name."""

    if isinstance(previous_final, PreviousFinal):
        return ranking_for_week(snapshot, week, previous_final.cors, previous_final.wins_vs_expected)
    return ranking_for_week(snapshot, week, previous_final)


def spreads_for_week(
    snapshot: SeasonSnapshot,
    week: int,
    rankings: Sequence[Mapping[str, Any]],
    *,
    hfa: float = HFA,
) -> list[dict[str, Any]]:
    """Produce finite spreads for valid FBS-vs-FBS games in one week.

    The prediction uses the previous completed ranking for a game's week when
    callers provide it.  Scheduled, unscored games remain eligible for a
    spread; they do not affect records or CORS.
    """

    rating = {str(row["school"]): _number(row.get("cors")) for row in rankings}
    valid_teams = set(rating)
    rows: list[dict[str, Any]] = []
    for game in sorted(
        (game for game in snapshot.games if int(game.week) == int(week)),
        key=lambda value: (
            value.home_team,
            value.away_team,
            int(value.week),
        ),
    ):
        if game.home_team not in valid_teams or game.away_team not in valid_teams:
            continue
        value = (rating[game.home_team] - rating[game.away_team])
        if not game.neutral_site:
            value += float(hfa)
        value = round(value * 2) / 2
        # Keep the legacy presentation while exposing a numeric field for
        # machine validation.
        sign = "-" if value > 0 else "+"
        display = f"{game.home_team} {sign}{abs(value):g}"
        rows.append(
            {
                "week": int(game.week),
                "home_team": game.home_team,
                "away_team": game.away_team,
                "neutral_site": bool(game.neutral_site),
                "home_cors": rating[game.home_team],
                "away_cors": rating[game.away_team],
                "spread_value": float(value),
                "spread": display,
            }
        )
    return rows


def load_previous_final_rows(path: str | Path | None) -> list[dict[str, Any]]:
    """Read legacy HTML or machine-readable final ranking rows.

    Old final pages contain a pandas table after a simple HTML title block;
    JSON inputs may be either a list of rows or ``{"rankings": [...]}``.
    """

    if path is None:
        return []
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        import json

        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, Mapping):
            value = value.get("rankings", value.get("rows", []))
        if not isinstance(value, list):
            raise ValueError(f"Previous final JSON must contain a row list: {path}")
        return [dict(row) for row in value]
    # Legacy generated pages predate an explicit encoding and contain a few
    # Latin-1 school names.  Replacement keeps table structure readable while
    # avoiding any mutation of the Published Site.
    html = path.read_text(encoding="utf-8", errors="replace")
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError as exc:
        raise ValueError(f"Previous final has no ranking table: {path}") from exc
    if not tables:
        return []
    frame = tables[0]
    frame = frame.loc[:, [column for column in frame.columns if str(column) != "Unnamed: 0"]]
    return frame.where(pd.notna(frame), None).to_dict(orient="records")


def load_previous_final(path: str | Path | None) -> dict[str, float]:
    """Return prior final CORS values keyed by school."""

    return {
        str(row["school"]): _number(row.get("cors"), FCS_CORS)
        for row in load_previous_final_rows(path)
        if row.get("school") is not None and row.get("cors") is not None
    }


def load_previous_final_model(path: str | Path | None) -> PreviousFinal:
    """Load both carryover CORS and optional regression inputs."""

    rows = load_previous_final_rows(path)
    return PreviousFinal(
        cors={
            str(row["school"]): _number(row.get("cors"), FCS_CORS)
            for row in rows
            if row.get("school") is not None
        },
        wins_vs_expected={
            str(row["school"]): _number(row.get("wins_vs_expected"), 0.0)
            for row in rows
            if row.get("school") is not None
        },
    )


# Compatibility aliases kept intentionally small and pure.
compute_records = records_for_week
compute_ranking = ranking_for_week
compute_rankings = season_rankings
compute_spreads = spreads_for_week
parse_previous_final = load_previous_final
calculate_records = records_for_week
calculate_ranking = ranking_for_week
calculate_rankings = season_rankings
calculate_season = season_rankings
calculate_spreads = spreads_for_week
load_prior_final = load_previous_final


__all__ = [
    "CORS_VERSION",
    "MODEL_VERSION",
    "FCS_CORS",
    "HFA",
    "REGRESSION_FACTOR",
    "TIE_BREAK",
    "PreviousFinal",
    "RankingEngine",
    "CORSRankingEngine",
    "completed_games",
    "scheduled_games",
    "scheduled_season_end_week",
    "scheduled_end_week",
    "season_is_complete",
    "is_season_complete",
    "season_end_week",
    "end_week",
    "records_for_week",
    "ranking_for_week",
    "season_rankings",
    "spreads_for_week",
    "load_previous_final",
    "load_previous_final_rows",
    "load_previous_final_model",
    "compute_records",
    "compute_ranking",
    "compute_rankings",
    "compute_spreads",
    "parse_previous_final",
    "calculate_records",
    "calculate_ranking",
    "calculate_rankings",
    "calculate_season",
    "calculate_spreads",
    "load_prior_final",
]
