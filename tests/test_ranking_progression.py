"""Focused contract tests for the standalone CORS progression boundary."""

from __future__ import annotations

import json
from types import MappingProxyType
import unittest

from cfb.ranking_engine import MODEL_VERSION
from cfb.ranking_progression import (
    ProgressionContractError,
    build_progression,
    build_progression_outputs,
    progression_json,
    render_progression_html,
)
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam
from cfb.public_safety import assert_public_bytes


class RankingProgressionTests(unittest.TestCase):
    def complete_snapshot(self) -> SeasonSnapshot:
        teams = (
            SourceTeam("Beta Tech", "Big Test"),
            SourceTeam("Alpha State", "Big Test"),
            SourceTeam("Independent U", "Independents"),
        )
        games = tuple(
            SourceGame(
                week=week,
                home_team="Alpha State",
                home_classification="fbs",
                home_points=week + 20,
                away_team="Beta Tech",
                away_classification="fbs",
                away_points=week + 10,
                neutral_site=False,
                provider_id=f"g{week}",
                completed=True,
            )
            for week in range(3)
        )
        metadata = MappingProxyType(
            {
                "schema_version": 3,
                "sport": "cfb",
                "classification": "FBS",
                "year": 2025,
                "complete_through_week": 2,
            }
        )
        return SeasonSnapshot(
            sport="cfb",
            classification="FBS",
            year=2025,
            teams=teams,
            games=games,
            metadata=metadata,
            checksum=_checksum(metadata, teams, games),
        )

    def partial_snapshot(self) -> SeasonSnapshot:
        complete = self.complete_snapshot()
        games = tuple(
            game if game.week < 2 else SourceGame(
                week=game.week,
                home_team=game.home_team,
                home_classification=game.home_classification,
                home_points=None,
                away_team=game.away_team,
                away_classification=game.away_classification,
                away_points=None,
                neutral_site=game.neutral_site,
                provider_id=game.provider_id,
                completed=False,
            )
            for game in complete.games
        )
        metadata = MappingProxyType(
            {
                "schema_version": 3,
                "sport": complete.sport,
                "classification": complete.classification,
                "year": complete.year,
                "complete_through_week": 1,
            }
        )
        return SeasonSnapshot(
            sport=complete.sport,
            classification=complete.classification,
            year=complete.year,
            teams=complete.teams,
            games=games,
            metadata=metadata,
            checksum=_checksum(metadata, complete.teams, games),
        )

    @staticmethod
    def rows(offset: int) -> list[dict[str, object]]:
        # Input order intentionally follows rank rather than alphabetical
        # order.  The presentation boundary must sort only the roster rows.
        return [
            {"school": "Independent U", "conference": "Independents", "cors": 30.0 + offset, "rank": 1},
            {"school": "Beta Tech", "conference": "Big Test", "cors": 20.0 + offset, "rank": 2},
            {"school": "Alpha State", "conference": "Big Test", "cors": 10.0 + offset, "rank": 3},
        ]

    def test_complete_progression_preserves_engine_values_and_roster_order(self) -> None:
        snapshot = self.complete_snapshot()
        document = build_progression(
            snapshot,
            rankings={0: self.rows(1), "W1": self.rows(2), 2: self.rows(3)},
            preseason_rows=self.rows(0),
            final_rows=self.rows(4),
            phase="final",
            dataset_id="2025-fbs-recovered-v1",
        )

        self.assertEqual(
            [checkpoint.key for checkpoint in document.checkpoints],
            ["PRESEASON", "W0", "W1", "W2", "FINAL"],
        )
        self.assertEqual(
            [team.school for team in document.teams],
            ["Alpha State", "Beta Tech", "Independent U"],
        )
        self.assertEqual(document.team("Alpha State").cells["W1"].points, 12.0)
        self.assertEqual(document.team("Alpha State").cells["W1"].rank, 3)
        self.assertEqual(document.team("Independent U").cells["FINAL"].points, 34.0)
        self.assertTrue(all(checkpoint.available for checkpoint in document.checkpoints))
        self.assertEqual(document.source_snapshot, snapshot.checksum)
        self.assertEqual(document.provenance["dataset_id"], "2025-fbs-recovered-v1")
        self.assertEqual(document.provenance["model_version"], MODEL_VERSION)

    def test_bounded_partial_snapshot_keeps_future_and_missing_states_explicit(self) -> None:
        snapshot = self.partial_snapshot()
        document = build_progression(
            snapshot,
            rankings={0: self.rows(1), 1: self.rows(2)},
            preseason_rows=self.rows(0),
            phase="week",
            target_week=1,
        )

        self.assertTrue(document.checkpoint("W0").available)
        self.assertTrue(document.checkpoint("W1").available)
        self.assertFalse(document.checkpoint("W2").available)
        self.assertEqual(document.checkpoint("W2").reason, "future_checkpoint")
        self.assertFalse(document.checkpoint("FINAL").available)
        self.assertEqual(document.checkpoint("FINAL").reason, "future_checkpoint")
        for team in document.teams:
            self.assertFalse(team.cells["W2"].available)
            self.assertIsNone(team.cells["W2"].points)
            self.assertIsNone(team.cells["W2"].rank)

    def test_future_rows_and_partial_or_duplicate_rosters_fail_closed(self) -> None:
        snapshot = self.partial_snapshot()
        with self.assertRaisesRegex(ProgressionContractError, "beyond the validated"):
            build_progression(
                snapshot,
                rankings={2: self.rows(2)},
                preseason_rows=self.rows(0),
                phase="week",
                target_week=1,
            )

        duplicate = self.rows(0)
        duplicate[-1] = dict(duplicate[-2])
        with self.assertRaisesRegex(ProgressionContractError, "duplicate school"):
            build_progression(snapshot, rankings={0: duplicate}, phase="week", target_week=0)

        malformed = self.rows(0)
        malformed[0] = {**malformed[0], "cors": float("nan")}
        with self.assertRaisesRegex(ProgressionContractError, "finite number"):
            build_progression(snapshot, rankings={0: malformed}, phase="week", target_week=0)

    def test_identity_mismatch_and_final_on_incomplete_snapshot_are_rejected(self) -> None:
        snapshot = self.partial_snapshot()
        with self.assertRaisesRegex(ProgressionContractError, "source_snapshot"):
            build_progression(
                snapshot,
                rankings={0: self.rows(0)},
                source_snapshot="c" * 64,
                phase="week",
                target_week=0,
            )
        with self.assertRaisesRegex(ProgressionContractError, "FINAL phase"):
            build_progression(
                snapshot,
                rankings={0: self.rows(0)},
                final_rows=self.rows(1),
                phase="final",
            )

    def test_static_outputs_are_safe_and_deterministic(self) -> None:
        document = build_progression(
            self.complete_snapshot(),
            rankings={0: self.rows(1), 1: self.rows(2), 2: self.rows(3)},
            preseason_rows=self.rows(0),
            final_rows=self.rows(4),
            phase="final",
        )
        html = render_progression_html(document)
        payload = progression_json(document)
        self.assertTrue(html.startswith("<!doctype html>\n"))
        self.assertNotIn("<script", html.lower())
        self.assertLess(html.index("W0 Points"), html.index("W0 National rank"))
        self.assertIn("../rankings/2025_W0_FBS_cors.html", html)
        self.assertIn("../rankings/2025_FINAL_FBS_cors.html", html)
        parsed = json.loads(payload)
        self.assertEqual(parsed["source_snapshot"], document.source_snapshot)
        self.assertEqual(parsed["rows"][0]["checkpoints"]["W0"]["points"], 11.0)
        self.assertNotIn("home_points", payload)
        assert_public_bytes("cfb/years/2025/history/2025_FBS_progression.json", payload.encode())
        self.assertEqual(payload, progression_json(document))

        same_document, same_html, same_json = build_progression_outputs(
            self.complete_snapshot(),
            rankings={0: self.rows(1), 1: self.rows(2), 2: self.rows(3)},
            preseason_rows=self.rows(0),
            final_rows=self.rows(4),
            phase="final",
        )
        self.assertEqual(same_document.to_dict(), document.to_dict())
        self.assertEqual(same_html, html)
        self.assertEqual(same_json, payload)


if __name__ == "__main__":
    unittest.main()
