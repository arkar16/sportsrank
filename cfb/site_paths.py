from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
WEBSITE_ROOT = REPO_ROOT / "website"
CFB_SITE_ROOT = WEBSITE_ROOT / "cfb"
YEARS_ROOT = CFB_SITE_ROOT / "years"
ASSETS_ROOT = WEBSITE_ROOT / "assets"
CLIENT_ASSETS_ROOT = ASSETS_ROOT / "client"
DATA_ASSETS_ROOT = ASSETS_ROOT / "data" / "cfb"
BUILD_ROOT = REPO_ROOT / "build"
CACHE_ROOT = BUILD_ROOT / "cache" / "cfb"


def normalize_division(division: str) -> str:
    return division.upper()


def season_cache_dir(season: int, division: str) -> Path:
    return CACHE_ROOT / str(season) / division.lower()


def rankings_cache_dir(season: int, division: str) -> Path:
    return season_cache_dir(season, division) / "rankings"


def rankings_sources_dir(season: int, division: str) -> Path:
    return season_cache_dir(season, division) / "sources"


def ranking_cache_path(season: int, division: str, page_slug: str) -> Path:
    return rankings_cache_dir(season, division) / f"{page_slug}.parquet"


def season_manifest_path(season: int, division: str) -> Path:
    return DATA_ASSETS_ROOT / str(season) / division.lower() / "season-manifest.json"


def ranking_payload_path(season: int, division: str, route_name: str) -> Path:
    return (
        DATA_ASSETS_ROOT
        / str(season)
        / division.lower()
        / "rankings"
        / f"{route_name}.json"
    )


def ranking_html_path(season: int, route_name: str) -> Path:
    return YEARS_ROOT / str(season) / "rankings" / f"{route_name}.html"


def team_source_path(season: int, division: str) -> Path:
    return rankings_sources_dir(season, division) / "teams.parquet"


def game_source_path(season: int, division: str) -> Path:
    return rankings_sources_dir(season, division) / "games.parquet"


def route_name_for_week(season: int, week: int, division: str, is_final: bool = False) -> str:
    division = normalize_division(division)
    if is_final:
        return f"{season}_FINAL_{division}_cors"
    return f"{season}_W{week}_{division}_cors"

