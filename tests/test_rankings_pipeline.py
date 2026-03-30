from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from cfb.rankings_pipeline import (
    build_games_frame,
    build_teams_frame,
    build_week_zero_rankings,
    compute_records_frame,
    compute_weekly_rankings,
    legacy_html_to_rankings_frame,
    normalize_rankings_frame,
    page_descriptor,
    rankings_to_payload,
    render_rankings_shell,
    write_page_outputs,
    write_season_manifest,
)


def sample_teams() -> pl.DataFrame:
    return build_teams_frame(
        [
            {"school": "A", "conference": "X"},
            {"school": "B", "conference": "Y"},
            {"school": "C", "conference": "Z"},
        ]
    )


def previous_rankings() -> pl.DataFrame:
    return normalize_rankings_frame(
        pl.DataFrame(
            {
                "rank": [1, 2, 3],
                "school": ["A", "B", "C"],
                "conference": ["X", "Y", "Z"],
                "record": ["0-0", "0-0", "0-0"],
                "win_pct": [0.0, 0.0, 0.0],
                "cors": [15.0, 10.0, 5.0],
                "mov": [0.0, 0.0, 0.0],
                "sos": [0.0, 0.0, 0.0],
                "expected_wins": [None, None, None],
                "wins_vs_expected": [1.0, -1.0, 0.0],
            }
        )
    )


def sample_games() -> pl.DataFrame:
    return build_games_frame(
        [
            {
                "week": 1,
                "home_team": "A",
                "home_division": "fbs",
                "home_score": 30,
                "away_team": "B",
                "away_division": "fbs",
                "away_score": 20,
                "neutral_site": False,
            },
            {
                "week": 1,
                "home_team": "C",
                "home_division": "fbs",
                "home_score": 24,
                "away_team": "A",
                "away_division": "fbs",
                "away_score": 14,
                "neutral_site": False,
            },
            {
                "week": 2,
                "home_team": "B",
                "home_division": "fbs",
                "home_score": 35,
                "away_team": "C",
                "away_division": "fbs",
                "away_score": 21,
                "neutral_site": False,
            },
        ]
    )


def test_build_week_zero_rankings_regresses_prior_cors() -> None:
    frame = build_week_zero_rankings(sample_teams(), previous_rankings())

    assert frame["school"].to_list() == ["A", "B", "C"]
    assert frame["cors"].to_list() == [13.25, 11.75, 5.0]
    assert frame["record"].to_list() == ["0-0", "0-0", "0-0"]
    assert frame["expected_wins"].to_list() == [None, None, None]


def test_compute_records_frame_handles_pre_1996_ties() -> None:
    teams = build_teams_frame(
        [
            {"school": "A", "conference": "X"},
            {"school": "B", "conference": "Y"},
        ]
    )
    games = build_games_frame(
        [
            {
                "week": 1,
                "home_team": "A",
                "home_division": "fbs",
                "home_score": 21,
                "away_team": "B",
                "away_division": "fbs",
                "away_score": 21,
                "neutral_site": False,
            }
        ]
    )

    records = compute_records_frame(teams, games, 1, 1995).select("school", "record", "win_pct").to_dicts()

    assert records == [
        {"school": "A", "record": "0-0-1", "win_pct": 0.0},
        {"school": "B", "record": "0-0-1", "win_pct": 0.0},
    ]


def test_compute_weekly_rankings_matches_expected_fixture_values() -> None:
    frame = compute_weekly_rankings(sample_teams(), sample_games(), previous_rankings(), 2024, 2)

    assert frame["school"].to_list() == ["A", "B", "C"]
    assert frame["record"].to_list() == ["1-1", "1-1", "1-1"]
    assert frame["cors"].to_list() == [14.41, 14.2, 11.5]
    assert frame["sos"].to_list() == [8.0, 10.0, 12.0]
    assert frame["wins_vs_expected"].to_list() == [0.0, -0.09, 0.1]


def test_payload_and_shell_follow_client_render_contract() -> None:
    page = page_descriptor(2024, 10, "FBS", "2026-03-29 18:00:00")
    payload = rankings_to_payload(compute_weekly_rankings(sample_teams(), sample_games(), previous_rankings(), 2024, 2), page)
    shell = render_rankings_shell(page)

    assert payload["payloadUrl"].endswith("/2024/fbs/rankings/2024_W10_FBS_cors.json")
    assert payload["rows"][0]["school"] == "A"
    assert "winsVsExpected" in payload["rows"][0]
    assert "<table" not in shell
    assert "rankings-page-config" in shell
    assert "/assets/client/rankings-app.js" in shell


def test_write_page_outputs_and_manifest_create_expected_files(tmp_path: Path, monkeypatch) -> None:
    import cfb.rankings_pipeline as rankings_pipeline

    def cache_path(_season: int, _division: str, page_slug: str) -> Path:
        return tmp_path / "cache" / f"{page_slug}.parquet"

    def payload_path(_season: int, _division: str, route_name: str) -> Path:
        return tmp_path / "payloads" / f"{route_name}.json"

    def html_path(_season: int, route_name: str) -> Path:
        return tmp_path / "pages" / f"{route_name}.html"

    def manifest_path(_season: int, _division: str) -> Path:
        return tmp_path / "payloads" / "season-manifest.json"

    monkeypatch.setattr(rankings_pipeline, "ranking_cache_path", cache_path)
    monkeypatch.setattr(rankings_pipeline, "ranking_payload_path", payload_path)
    monkeypatch.setattr(rankings_pipeline, "ranking_html_path", html_path)
    monkeypatch.setattr(rankings_pipeline, "season_manifest_path", manifest_path)

    page = rankings_pipeline.page_descriptor(2024, 10, "FBS", "2026-03-29 18:00:00")
    frame = compute_weekly_rankings(sample_teams(), sample_games(), previous_rankings(), 2024, 2)
    page_meta = write_page_outputs(frame, page)
    write_season_manifest(2024, "FBS", 15, [page_meta])

    assert (tmp_path / "cache" / "week-10.parquet").exists()
    payload = json.loads((tmp_path / "payloads" / "2024_W10_FBS_cors.json").read_text(encoding="utf-8"))
    shell = (tmp_path / "pages" / "2024_W10_FBS_cors.html").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "payloads" / "season-manifest.json").read_text(encoding="utf-8"))

    assert payload["rows"][0]["rank"] == 1
    assert "<table" not in shell
    assert manifest["routes"][0]["payloadUrl"].endswith("2024_W10_FBS_cors.json")


def test_legacy_baseline_pages_parse_and_payload_is_smaller_than_html() -> None:
    baseline = json.loads(Path("tests/fixtures/rankings_baseline.json").read_text(encoding="utf-8"))

    for relative_path, sample in baseline.items():
        legacy_path = Path("website/cfb/years") / relative_path
        frame = legacy_html_to_rankings_frame(legacy_path)
        page = page_descriptor(
            int(relative_path.split("/")[0]),
            0 if "_W0_" in relative_path else 10 if "_W10_" in relative_path else 15,
            "FBS",
            "2026-03-29 18:00:00",
            is_final="FINAL" in relative_path,
        )
        payload = rankings_to_payload(frame, page)
        payload_bytes = len(json.dumps(payload).encode("utf-8"))
        shell_bytes = len(render_rankings_shell(page).encode("utf-8"))

        assert frame.height == sample["rows"]
        assert frame.columns[: len(sample["columns"])] == sample["columns"]
        assert frame[0, "school"] == sample["top_school"]
        assert frame[-1, "school"] == sample["bottom_school"]
        assert payload_bytes < sample["bytes"]
        assert shell_bytes < sample["bytes"]
