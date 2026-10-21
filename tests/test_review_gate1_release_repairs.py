"""Focused regressions for the second Gate 1 release review."""

from pathlib import Path
from types import MappingProxyType
import tempfile
import unittest

from cfb.release import build_release
from cfb.ranking_engine import PreviousFinal
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam


STAMP = "2026-09-08T12:00:00+00:00"
TEAMS = (
    SourceTeam("Alpha State", "Test"),
    SourceTeam("Beta Tech", "Test"),
)


def _snapshot(*, complete_through_week: int = 1, incomplete_w0: bool = False) -> SeasonSnapshot:
    games = (
        SourceGame(
            0,
            "Alpha State",
            "fbs",
            None if incomplete_w0 else 21,
            "Beta Tech",
            "fbs",
            None if incomplete_w0 else 14,
            False,
            provider_id="review-w0",
            completed=False if incomplete_w0 else True,
            disposition="in_progress" if incomplete_w0 else "completed",
        ),
        SourceGame(
            1,
            "Beta Tech",
            "fbs",
            17,
            "Alpha State",
            "fbs",
            10,
            False,
            provider_id="review-w1",
            completed=True,
            disposition="completed",
        ),
    )
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2025,
            "teams_fetched_at": STAMP,
            "games_fetched_at": STAMP,
            "complete_through_week": complete_through_week,
        }
    )
    return SeasonSnapshot(
        "cfb",
        "FBS",
        2025,
        TEAMS,
        games,
        metadata,
        _checksum(metadata, TEAMS, games),
    )


def _prior() -> PreviousFinal:
    return PreviousFinal(
        {"Alpha State": 10.0, "Beta Tech": 8.0},
        {"Alpha State": 0.0, "Beta Tech": 0.0},
        year=2024,
        classification="FBS",
    )


def _base(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("legacy", encoding="utf-8")
    prior = base / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
    prior.parent.mkdir(parents=True)
    prior.write_text(
        "<table><thead><tr><th>school</th><th>cors</th>"
        "<th>wins_vs_expected</th></tr></thead><tbody>"
        "<tr><td>Alpha State</td><td>10</td><td>0</td></tr>"
        "<tr><td>Beta Tech</td><td>8</td><td>0</td></tr>"
        "</tbody></table>",
        encoding="utf-8",
    )
    return base


class ReviewGate1ReleaseRepairTests(unittest.TestCase):
    def test_incomplete_w0_cannot_claim_a_numbered_completion_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = _snapshot(complete_through_week=0, incomplete_w0=True)
            self.assertEqual(snapshot.complete_through_week, -1)
            base = _base(root)
            with self.assertRaisesRegex(ValueError, "target_week cannot exceed"):
                build_release(
                    snapshot,
                    root / "candidate",
                    release_id="candidate",
                    phase="week",
                    target_week=0,
                    previous_final=_prior(),
                    published_site=base,
                    timestamp=STAMP,
                )
