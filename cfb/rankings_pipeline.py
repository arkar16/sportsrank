from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cfbd
import pandas as pd
import polars as pl

try:
    from . import config
    from .api import api_key
    from .site_paths import (
        YEARS_ROOT,
        game_source_path,
        ranking_cache_path,
        ranking_html_path,
        ranking_payload_path,
        route_name_for_week,
        season_manifest_path,
        team_source_path,
    )
except ImportError:  # pragma: no cover - supports running modules as scripts
    import config
    from api import api_key
    from site_paths import (
        YEARS_ROOT,
        game_source_path,
        ranking_cache_path,
        ranking_html_path,
        ranking_payload_path,
        route_name_for_week,
        season_manifest_path,
        team_source_path,
    )


RANKING_COLUMNS = [
    "rank",
    "school",
    "conference",
    "record",
    "win_pct",
    "cors",
    "mov",
    "sos",
    "expected_wins",
    "wins_vs_expected",
]

PYLOAD_COLUMNS = {
    "rank": "rank",
    "school": "school",
    "conference": "conference",
    "record": "record",
    "win_pct": "winPct",
    "cors": "cors",
    "mov": "mov",
    "sos": "sos",
    "expected_wins": "expectedWins",
    "wins_vs_expected": "winsVsExpected",
}

MOV_SCALE = 4.0
SOS_MIN = 0.8
SOS_MAX = 1.2
REGRESSION_FACTOR = 1.75
PYTHAGOREAN_EXPONENT = 2.37
CORS_MAX = 50 + (math.log10(31) * MOV_SCALE) * SOS_MAX


@dataclass(frozen=True)
class RankingPage:
    season: int
    division: str
    week: int
    is_final: bool
    title: str
    updated_at: str
    route_name: str
    page_slug: str

    @property
    def payload_url(self) -> str:
        return f"/assets/data/cfb/{self.season}/{self.division.lower()}/rankings/{self.route_name}.json"

    @property
    def html_path(self) -> Path:
        return ranking_html_path(self.season, self.route_name)

    @property
    def payload_path(self) -> Path:
        return ranking_payload_path(self.season, self.division, self.route_name)

    @property
    def cache_path(self) -> Path:
        return ranking_cache_path(self.season, self.division, self.page_slug)


def _games_client() -> cfbd.GamesApi:
    configuration = cfbd.Configuration()
    configuration.api_key["Authorization"] = api_key
    configuration.api_key_prefix["Authorization"] = "Bearer"
    return cfbd.GamesApi(cfbd.ApiClient(configuration))


def _teams_client() -> cfbd.TeamsApi:
    configuration = cfbd.Configuration()
    configuration.api_key["Authorization"] = api_key
    configuration.api_key_prefix["Authorization"] = "Bearer"
    return cfbd.TeamsApi(cfbd.ApiClient(configuration))


def page_title(season: int, week: int, division: str, is_final: bool) -> str:
    sport_upper = config.sport.upper()
    week_label = "Final Rankings" if is_final else f"W{week} Rankings"
    return f"CORS {config.cors_version} - {season} {week_label} - {division.upper()} {sport_upper}"


def page_descriptor(season: int, week: int, division: str, updated_at: str, is_final: bool = False) -> RankingPage:
    route_name = route_name_for_week(season, week, division, is_final=is_final)
    page_slug = "final" if is_final else f"week-{week:02d}"
    return RankingPage(
        season=season,
        division=division.upper(),
        week=week,
        is_final=is_final,
        title=page_title(season, week, division, is_final),
        updated_at=updated_at,
        route_name=route_name,
        page_slug=page_slug,
    )


def build_teams_frame(team_rows: list[dict[str, Any]]) -> pl.DataFrame:
    frame = pl.DataFrame(team_rows) if team_rows else pl.DataFrame({"school": [], "conference": []})
    if "conference" not in frame.columns:
        frame = frame.with_columns(pl.lit("FBS Independents").alias("conference"))
    return (
        frame.select(
            pl.col("school").cast(pl.Utf8),
            pl.col("conference").fill_null("FBS Independents").cast(pl.Utf8),
        )
        .unique(subset=["school"])
        .sort("school")
    )


def build_games_frame(game_rows: list[dict[str, Any]]) -> pl.DataFrame:
    if not game_rows:
        return pl.DataFrame(
            {
                "week": [],
                "home_team": [],
                "home_division": [],
                "home_score": [],
                "away_team": [],
                "away_division": [],
                "away_score": [],
                "neutral_site": [],
            }
        )
    return pl.DataFrame(game_rows).with_columns(
        pl.col("week").cast(pl.Int64),
        pl.col("home_team").cast(pl.Utf8),
        pl.col("home_division").cast(pl.Utf8),
        pl.col("home_score").cast(pl.Float64),
        pl.col("away_team").cast(pl.Utf8),
        pl.col("away_division").cast(pl.Utf8),
        pl.col("away_score").cast(pl.Float64),
        pl.col("neutral_site").cast(pl.Boolean),
    )


def normalize_rankings_frame(frame: pl.DataFrame) -> pl.DataFrame:
    normalized = frame
    for column in RANKING_COLUMNS:
        if column not in normalized.columns:
            if column in {"school", "conference", "record"}:
                normalized = normalized.with_columns(pl.lit(None, dtype=pl.Utf8).alias(column))
            elif column == "rank":
                normalized = normalized.with_columns(pl.lit(None, dtype=pl.Int64).alias(column))
            else:
                normalized = normalized.with_columns(pl.lit(None, dtype=pl.Float64).alias(column))

    return normalized.select(
        pl.col("rank").cast(pl.Int64, strict=False),
        pl.col("school").cast(pl.Utf8),
        pl.col("conference").cast(pl.Utf8),
        pl.col("record").cast(pl.Utf8),
        pl.col("win_pct").cast(pl.Float64, strict=False),
        pl.col("cors").cast(pl.Float64, strict=False),
        pl.col("mov").cast(pl.Float64, strict=False),
        pl.col("sos").cast(pl.Float64, strict=False),
        pl.col("expected_wins").cast(pl.Float64, strict=False),
        pl.col("wins_vs_expected").cast(pl.Float64, strict=False),
    )


def legacy_html_to_rankings_frame(html_path: Path) -> pl.DataFrame:
    tables = pd.read_html(html_path)
    if not tables:
        raise ValueError(f"No tables found in {html_path}")
    frame = pl.from_pandas(tables[0])
    return normalize_rankings_frame(frame)


def _completed_games(games: pl.DataFrame, week: int) -> pl.DataFrame:
    return games.filter(
        (pl.col("week") <= week)
        & pl.col("home_score").is_not_null()
        & pl.col("away_score").is_not_null()
    )


def build_team_game_rows(games: pl.DataFrame, week: int) -> pl.DataFrame:
    completed = _completed_games(games, week)
    if completed.is_empty():
        return pl.DataFrame(
            {
                "team": [],
                "opponent": [],
                "points_for": [],
                "points_against": [],
                "margin": [],
                "wins": [],
                "losses": [],
                "ties": [],
            }
        )

    home_rows = completed.select(
        pl.col("home_team").alias("team"),
        pl.col("away_team").alias("opponent"),
        pl.col("home_score").alias("points_for"),
        pl.col("away_score").alias("points_against"),
        (pl.col("home_score") - pl.col("away_score")).alias("margin"),
        pl.when(pl.col("home_score") > pl.col("away_score")).then(1).otherwise(0).alias("wins"),
        pl.when(pl.col("home_score") < pl.col("away_score")).then(1).otherwise(0).alias("losses"),
        pl.when(pl.col("home_score") == pl.col("away_score")).then(1).otherwise(0).alias("ties"),
    )
    away_rows = completed.select(
        pl.col("away_team").alias("team"),
        pl.col("home_team").alias("opponent"),
        pl.col("away_score").alias("points_for"),
        pl.col("home_score").alias("points_against"),
        (pl.col("away_score") - pl.col("home_score")).alias("margin"),
        pl.when(pl.col("away_score") > pl.col("home_score")).then(1).otherwise(0).alias("wins"),
        pl.when(pl.col("away_score") < pl.col("home_score")).then(1).otherwise(0).alias("losses"),
        pl.when(pl.col("away_score") == pl.col("home_score")).then(1).otherwise(0).alias("ties"),
    )
    return pl.concat([home_rows, away_rows], how="vertical")


def compute_records_frame(teams: pl.DataFrame, games: pl.DataFrame, week: int, season: int) -> pl.DataFrame:
    team_games = build_team_game_rows(games, week)
    aggregated = (
        team_games.group_by("team")
        .agg(
            pl.sum("wins").alias("wins"),
            pl.sum("losses").alias("losses"),
            pl.sum("ties").alias("ties"),
            pl.len().alias("games"),
            pl.sum("points_for").alias("points_for"),
            pl.sum("points_against").alias("points_against"),
            pl.mean("margin").round(2).alias("mov"),
        )
        if not team_games.is_empty()
        else pl.DataFrame(
            {
                "team": [],
                "wins": [],
                "losses": [],
                "ties": [],
                "games": [],
                "points_for": [],
                "points_against": [],
                "mov": [],
            }
        )
    )

    records = teams.join(aggregated, left_on="school", right_on="team", how="left").with_columns(
        pl.col("wins").fill_null(0).cast(pl.Int64),
        pl.col("losses").fill_null(0).cast(pl.Int64),
        pl.col("ties").fill_null(0).cast(pl.Int64),
        pl.col("games").fill_null(0).cast(pl.Int64),
        pl.col("points_for").fill_null(0).cast(pl.Float64),
        pl.col("points_against").fill_null(0).cast(pl.Float64),
        pl.col("mov").fill_null(0.0).cast(pl.Float64),
    )

    if season < 1996:
        record_expr = (
            pl.col("wins").cast(pl.Utf8)
            + pl.lit("-")
            + pl.col("losses").cast(pl.Utf8)
            + pl.lit("-")
            + pl.col("ties").cast(pl.Utf8)
        )
        denominator = pl.col("wins") + pl.col("losses") + pl.col("ties")
    else:
        record_expr = pl.col("wins").cast(pl.Utf8) + pl.lit("-") + pl.col("losses").cast(pl.Utf8)
        denominator = pl.col("wins") + pl.col("losses")

    return records.with_columns(
        record_expr.alias("record"),
        pl.when(denominator > 0)
        .then((pl.col("wins") / denominator).round(2))
        .otherwise(0.0)
        .alias("win_pct"),
        pl.when(pl.col("points_against") == 0)
        .then(pl.when(pl.col("points_for") > 0).then(1.0).otherwise(0.0))
        .otherwise(
            (pl.col("points_for").pow(PYTHAGOREAN_EXPONENT))
            / (
                pl.col("points_for").pow(PYTHAGOREAN_EXPONENT)
                + pl.col("points_against").pow(PYTHAGOREAN_EXPONENT)
            )
        )
        .mul(pl.col("games"))
        .round(2)
        .alias("expected_wins"),
    )


def build_week_zero_rankings(teams: pl.DataFrame, previous_rankings: pl.DataFrame) -> pl.DataFrame:
    previous = normalize_rankings_frame(previous_rankings)
    current = teams.join(previous.select("school", "cors", "wins_vs_expected"), on="school", how="left").with_columns(
        pl.col("cors").fill_null(float(config.fcs_constant)).alias("cors"),
        pl.lit("0-0").alias("record"),
        pl.lit(0.0).alias("win_pct"),
        pl.lit(0.0).alias("mov"),
        pl.lit(0.0).alias("sos"),
    )

    if "wins_vs_expected" in current.columns:
        current = current.with_columns(
            (pl.col("cors") - (pl.col("wins_vs_expected").fill_null(0.0) * REGRESSION_FACTOR))
            .round(2)
            .alias("cors")
        )

    return (
        current.sort(["cors", "school"], descending=[True, False])
        .with_row_index("rank", offset=1)
        .with_columns(
            pl.lit(None, dtype=pl.Float64).alias("expected_wins"),
            pl.lit(None, dtype=pl.Float64).alias("wins_vs_expected"),
        )
        .select(RANKING_COLUMNS)
    )


def compute_weekly_rankings(
    teams: pl.DataFrame,
    games: pl.DataFrame,
    previous_rankings: pl.DataFrame,
    season: int,
    week: int,
) -> pl.DataFrame:
    previous = normalize_rankings_frame(previous_rankings)
    records = compute_records_frame(teams, games, week, season)
    team_games = build_team_game_rows(games, week)

    if team_games.is_empty():
        raise ValueError(f"No completed games available for {season} week {week}")

    opponent_cors = previous.select(
        pl.col("school").alias("opponent"),
        pl.col("cors").alias("opponent_cors"),
    )

    sos = (
        team_games.join(opponent_cors, on="opponent", how="left")
        .with_columns(pl.col("opponent_cors").fill_null(float(config.fcs_constant)))
        .group_by("team")
        .agg(pl.mean("opponent_cors").round(2).alias("sos_raw"))
    )

    prior = previous.select(
        pl.col("school"),
        pl.col("cors").fill_null(float(config.fcs_constant)).alias("prior_cors"),
    )

    rankings = (
        records.join(sos, left_on="school", right_on="team", how="left")
        .join(prior, on="school", how="left")
        .with_columns(
            pl.col("sos_raw").fill_null(0.0).alias("sos_raw"),
            pl.col("prior_cors").fill_null(float(config.fcs_constant)).alias("prior_cors"),
            (((pl.col("win_pct") * 100) / 2).round(2)).alias("win_pct_score"),
            pl.when(pl.col("mov") > 0)
            .then((pl.col("mov") + 1).log10() * MOV_SCALE)
            .otherwise(-((pl.col("mov").abs() + 1).log10() * MOV_SCALE))
            .alias("net_mov"),
        )
        .with_columns(
            (SOS_MIN + ((pl.col("sos_raw") / CORS_MAX) * (SOS_MAX - SOS_MIN))).alias("scaled_sos"),
            (pl.col("prior_cors") / week).alias("scaled_prior_cors"),
            (pl.col("wins") - pl.col("expected_wins")).round(2).alias("wins_vs_expected"),
            pl.col("sos_raw").round(0).alias("sos"),
        )
        .with_columns(
            (
                (((pl.col("win_pct_score") + pl.col("net_mov")) * pl.col("scaled_sos")) + pl.col("scaled_prior_cors"))
                / 2
            )
            .round(2)
            .alias("cors")
        )
        .sort(["cors", "wins", "losses", "school"], descending=[True, True, False, False])
        .with_row_index("rank", offset=1)
        .select(
            "rank",
            "school",
            "conference",
            "record",
            "win_pct",
            "cors",
            "mov",
            "sos",
            "expected_wins",
            "wins_vs_expected",
        )
    )

    return normalize_rankings_frame(rankings)


def rankings_to_payload(frame: pl.DataFrame, page: RankingPage) -> dict[str, Any]:
    normalized = normalize_rankings_frame(frame)
    rows: list[dict[str, Any]] = []
    for row in normalized.iter_rows(named=True):
        payload_row: dict[str, Any] = {}
        for source_key, payload_key in PYLOAD_COLUMNS.items():
            value = row[source_key]
            if isinstance(value, float):
                value = round(value, 2)
            payload_row[payload_key] = value
        rows.append(payload_row)

    return {
        "sport": config.sport,
        "season": page.season,
        "week": page.week,
        "division": page.division,
        "title": page.title,
        "updatedAt": page.updated_at,
        "isFinal": page.is_final,
        "payloadUrl": page.payload_url,
        "rows": rows,
    }


def render_rankings_shell(page: RankingPage) -> str:
    bootstrap = {
        "sport": config.sport,
        "season": page.season,
        "week": page.week,
        "division": page.division,
        "isFinal": page.is_final,
        "payloadUrl": page.payload_url,
        "title": page.title,
    }
    bootstrap_json = json.dumps(bootstrap, separators=(",", ":"))
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  <title>{page.title}</title>\n"
        "  <link rel=\"stylesheet\" href=\"/assets/client/rankings-app.css\">\n"
        "</head>\n"
        "<body>\n"
        f"  <h1>{page.title}</h1>\n"
        f"  <p>Last updated: {page.updated_at}</p>\n"
        "  <div id=\"rankings-app\" data-render-mode=\"client\">Loading rankings...</div>\n"
        "  <noscript>This rankings page requires JavaScript to render the latest data.</noscript>\n"
        f"  <script id=\"rankings-page-config\" type=\"application/json\">{bootstrap_json}</script>\n"
        "  <script type=\"module\" src=\"/assets/client/rankings-app.js\"></script>\n"
        "</body>\n"
        "</html>\n"
    )


def write_page_outputs(frame: pl.DataFrame, page: RankingPage) -> dict[str, Any]:
    normalized = normalize_rankings_frame(frame)
    page.cache_path.parent.mkdir(parents=True, exist_ok=True)
    page.payload_path.parent.mkdir(parents=True, exist_ok=True)
    page.html_path.parent.mkdir(parents=True, exist_ok=True)

    normalized.write_parquet(page.cache_path)
    page.payload_path.write_text(
        json.dumps(rankings_to_payload(normalized, page), indent=2),
        encoding="utf-8",
    )
    page.html_path.write_text(render_rankings_shell(page), encoding="utf-8")
    return {
        "week": page.week,
        "isFinal": page.is_final,
        "title": page.title,
        "pagePath": f"/cfb/years/{page.season}/rankings/{page.route_name}.html",
        "payloadUrl": page.payload_url,
    }


def write_season_manifest(
    season: int,
    division: str,
    final_week: int,
    pages: list[dict[str, Any]],
) -> None:
    manifest_path = season_manifest_path(season, division)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "sport": config.sport,
        "season": season,
        "division": division.upper(),
        "finalWeek": final_week,
        "rankingWeeks": [page["week"] for page in pages if not page["isFinal"]],
        "routes": pages,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def fetch_team_rows(season: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for team in _teams_client().get_fbs_teams(year=season):
        rows.append(
            {
                "school": team.school,
                "conference": getattr(team, "conference", None) or "FBS Independents",
            }
        )
    return rows


def fetch_game_rows(season: int, division: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for game in _games_client().get_games(year=season, division=division.upper()):
        rows.append(
            {
                "week": game.week,
                "home_team": game.home_team,
                "home_division": str(game.home_division or "").lower(),
                "home_score": game.home_points,
                "away_team": game.away_team,
                "away_division": str(game.away_division or "").lower(),
                "away_score": game.away_points,
                "neutral_site": bool(game.neutral_site),
            }
        )
    return rows


def load_or_fetch_sources(season: int, division: str, refresh: bool = False) -> tuple[pl.DataFrame, pl.DataFrame]:
    teams_path = team_source_path(season, division)
    games_path = game_source_path(season, division)

    if not refresh and teams_path.exists() and games_path.exists():
        return pl.read_parquet(teams_path), pl.read_parquet(games_path)

    teams = build_teams_frame(fetch_team_rows(season))
    games = build_games_frame(fetch_game_rows(season, division))
    teams_path.parent.mkdir(parents=True, exist_ok=True)
    games_path.parent.mkdir(parents=True, exist_ok=True)
    teams.write_parquet(teams_path)
    games.write_parquet(games_path)
    return teams, games


def load_previous_final_rankings(season: int, division: str) -> pl.DataFrame:
    previous_season = season - 1
    if previous_season < 1897:
        return pl.DataFrame({"school": [], "conference": [], "cors": [], "wins_vs_expected": []})

    cached_path = ranking_cache_path(previous_season, division, "final")
    if cached_path.exists():
        return normalize_rankings_frame(pl.read_parquet(cached_path))

    legacy_path = YEARS_ROOT / str(previous_season) / "rankings" / f"{previous_season}_FINAL_{division.upper()}_cors.html"
    if legacy_path.exists():
        frame = legacy_html_to_rankings_frame(legacy_path)
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(cached_path)
        return frame

    return pl.DataFrame({"school": [], "conference": [], "cors": [], "wins_vs_expected": []})


def infer_final_week(games: pl.DataFrame) -> int:
    if games.is_empty():
        return 0
    return int(min(games.select(pl.max("week")).item(), config.week_count))


def generate_rankings_through_week(
    season: int,
    week: int,
    division: str,
    updated_at: str,
    refresh: bool = False,
) -> pl.DataFrame:
    teams, games = load_or_fetch_sources(season, division, refresh=refresh)
    final_week = infer_final_week(games)
    target_week = min(week, final_week)

    pages: list[dict[str, Any]] = []
    previous_final = load_previous_final_rankings(season, division)
    current = build_week_zero_rankings(teams, previous_final)
    pages.append(write_page_outputs(current, page_descriptor(season, 0, division, updated_at)))

    if target_week == 0:
        write_season_manifest(season, division, final_week, pages)
        return current

    for current_week in range(1, target_week + 1):
        current = compute_weekly_rankings(teams, games, current, season, current_week)
        is_final = current_week == final_week and week >= final_week
        pages.append(
            write_page_outputs(
                current,
                page_descriptor(season, current_week, division, updated_at, is_final=is_final),
            )
        )

    write_season_manifest(season, division, final_week, pages)
    return current


def load_rankings_for_week(season: int, week: int, division: str, updated_at: str) -> pl.DataFrame:
    games = pl.read_parquet(game_source_path(season, division)) if game_source_path(season, division).exists() else None
    if games is None:
        return generate_rankings_through_week(season, week, division, updated_at)

    final_week = infer_final_week(games)
    is_final = week == final_week
    page_slug = "final" if is_final else ("week-00" if week == 0 else f"week-{week:02d}")
    cache_path = ranking_cache_path(season, division, page_slug)
    if cache_path.exists():
        return normalize_rankings_frame(pl.read_parquet(cache_path))
    return generate_rankings_through_week(season, week, division, updated_at)
