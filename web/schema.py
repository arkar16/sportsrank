"""SQLite read-model schema and connection helpers for the SportsRank web app."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from urllib.parse import quote

SCHEMA_VERSION = "sportsrank-web-db/v3"

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE sports (
    sport_id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    classification TEXT NOT NULL,
    current_season INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE programs (
    program_id INTEGER PRIMARY KEY,
    sport_id INTEGER NOT NULL REFERENCES sports(sport_id),
    slug TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    mark_path TEXT,
    UNIQUE(sport_id, slug),
    UNIQUE(sport_id, canonical_name)
);

CREATE TABLE school_marks (
    sport_id INTEGER NOT NULL REFERENCES sports(sport_id),
    school_name TEXT NOT NULL,
    mark_path TEXT NOT NULL,
    PRIMARY KEY(sport_id, school_name)
);

CREATE TABLE seasons (
    season_id INTEGER PRIMARY KEY,
    sport_id INTEGER NOT NULL REFERENCES sports(sport_id),
    year INTEGER NOT NULL,
    label TEXT NOT NULL,
    is_final INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '',
    latest_checkpoint TEXT NOT NULL DEFAULT '',
    home_advantage REAL NOT NULL DEFAULT 3.0,
    has_excitement INTEGER NOT NULL DEFAULT 0,
    UNIQUE(sport_id, year)
);

CREATE TABLE checkpoints (
    checkpoint_id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    code TEXT NOT NULL,
    label TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    cutoff_utc TEXT NOT NULL DEFAULT '',
    UNIQUE(season_id, code)
);

CREATE TABLE rankings (
    checkpoint_id INTEGER NOT NULL REFERENCES checkpoints(checkpoint_id),
    program_id INTEGER NOT NULL REFERENCES programs(program_id),
    rank INTEGER NOT NULL,
    rating TEXT NOT NULL,
    rating_num REAL NOT NULL,
    record_text TEXT NOT NULL,
    conference TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(checkpoint_id, program_id)
);

CREATE TABLE games (
    game_id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES seasons(season_id),
    week INTEGER NOT NULL,
    home_school TEXT NOT NULL,
    away_school TEXT NOT NULL,
    home_program_id INTEGER REFERENCES programs(program_id),
    away_program_id INTEGER REFERENCES programs(program_id),
    home_score INTEGER,
    away_score INTEGER,
    home_handicap TEXT,
    home_handicap_num REAL,
    neutral_site INTEGER NOT NULL DEFAULT 0,
    start_utc TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    home_division TEXT NOT NULL DEFAULT '',
    away_division TEXT NOT NULL DEFAULT '',
    postseason INTEGER NOT NULL DEFAULT 0,
    tipoff_label TEXT NOT NULL DEFAULT '',
    bev_html TEXT NOT NULL DEFAULT '',
    aev_html TEXT NOT NULL DEFAULT '',
    aev_estimated INTEGER NOT NULL DEFAULT 0,
    pick_path TEXT NOT NULL DEFAULT ''
);

CREATE TABLE performance_seasons (
    sport_id INTEGER NOT NULL REFERENCES sports(sport_id),
    year INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    report_json_zlib BLOB NOT NULL,
    PRIMARY KEY(sport_id, year)
);

CREATE TABLE performance_blobs (
    sport_id INTEGER NOT NULL REFERENCES sports(sport_id),
    name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    data_zlib BLOB NOT NULL,
    PRIMARY KEY(sport_id, name)
);

CREATE TABLE editions (
    sport_id INTEGER NOT NULL REFERENCES sports(sport_id),
    slug TEXT NOT NULL,
    season_year INTEGER NOT NULL,
    cutoff_utc TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    title TEXT NOT NULL,
    rankings_json_zlib BLOB NOT NULL,
    forecasts_json_zlib BLOB NOT NULL,
    PRIMARY KEY(sport_id, slug)
);

-- Compressed clean-URL HTML documents for rich site surfaces (conferences,
-- excitement leaderboards, pick share cards, and full CBB archive pages) with
-- a dynamic masthead placeholder injected at serve time.
CREATE TABLE pages (
    canonical_path TEXT PRIMARY KEY,
    sport_slug TEXT NOT NULL DEFAULT '',
    active_nav TEXT NOT NULL DEFAULT '',
    is_final INTEGER NOT NULL DEFAULT 0,
    html_zlib BLOB NOT NULL
);

-- Public machine-readable JSON/CSV exports keyed by exact URL path.
CREATE TABLE blobs (
    path TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    is_final INTEGER NOT NULL DEFAULT 0,
    data_zlib BLOB NOT NULL
);

CREATE INDEX idx_programs_sport_name ON programs(sport_id, canonical_name COLLATE NOCASE);
CREATE INDEX idx_seasons_sport_year ON seasons(sport_id, year DESC);
CREATE INDEX idx_checkpoints_season_order ON checkpoints(season_id, sort_order);
CREATE INDEX idx_rankings_cp_rank ON rankings(checkpoint_id, rank);
CREATE INDEX idx_rankings_prog_cp ON rankings(program_id, checkpoint_id);
CREATE INDEX idx_games_season_week ON games(season_id, week, game_id);
CREATE INDEX idx_games_season_home ON games(season_id, home_program_id, week);
CREATE INDEX idx_games_season_away ON games(season_id, away_program_id, week);
CREATE INDEX idx_editions_sport_slug ON editions(sport_id, slug DESC);
"""


def init_database(connection: sqlite3.Connection) -> None:
    """Create all tables and indexes in a fresh SQLite database."""
    connection.executescript(SCHEMA_DDL)


def connect_readonly(db_path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    """Open a read-only SQLite connection suitable for web request serving."""
    resolved = db_path.resolve()
    uri = f"file:{quote(str(resolved))}?mode=ro"
    if immutable:
        uri += "&immutable=1"
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA cache_size = -16000")
    return connection
