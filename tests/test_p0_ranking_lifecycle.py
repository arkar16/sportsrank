"""Hand-checkable P0 tests for the explicit CORS ranking lifecycle."""

from types import MappingProxyType
import json
from pathlib import Path
import tempfile
import unittest

from cfb.ranking_engine import (
    PreviousFinal,
    RankingContractError,
    RankingPhase,
    final_ranking,
    preseason_ranking,
    ranking_for_phase,
    ranking_for_week,
    week_ranking,
)
from cfb.season_snapshot import SeasonSnapshot
from cfb.season_source import SourceGame, SourceTeam


class RankingLifecycleTests(unittest.TestCase):
    def snapshot(self, *, complete_through_week: int = 1, include_week_two: bool = True):
        games = [
            SourceGame(0, "Alpha", "fbs", 21, "Beta", "fbs", 14, False),
            SourceGame(1, "Beta", "fbs", 17, "Alpha", "fbs", 14, True),
        ]
        if include_week_two:
            games.append(SourceGame(2, "Alpha", "fbs", None, "Beta", "fbs", None, False))
        return SeasonSnapshot(
            "cfb",
            "FBS",
            2025,
            (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test")),
            tuple(games),
            MappingProxyType({"complete_through_week": complete_through_week}),
            "synthetic",
        )

    @staticmethod
    def prior() -> PreviousFinal:
        return PreviousFinal(
            cors={"Alpha": 20.0, "Beta": 10.0},
            wins_vs_expected={"Alpha": 1.0, "Beta": -1.0},
        )

    def test_preseason_is_carryover_only_and_excludes_scored_week_zero(self):
        snapshot = self.snapshot(complete_through_week=0)
        rows = preseason_ranking(snapshot, self.prior())
        by_school = {row["school"]: row for row in rows}

        # 20 - (1 * 1.75), 10 - (-1 * 1.75); the Week 0 score is irrelevant.
        self.assertEqual(by_school["Alpha"]["cors"], 18.25)
        self.assertEqual(by_school["Beta"]["cors"], 11.75)
        self.assertEqual(by_school["Alpha"]["record"], "0-0")
        self.assertEqual(by_school["Alpha"]["expected_wins"], 0.0)

    def test_week_zero_is_scored_and_uses_divisor_one(self):
        snapshot = self.snapshot(complete_through_week=0)
        scored = week_ranking(snapshot, 0, previous_final=self.prior())
        by_school = {row["school"]: row for row in scored}

        self.assertEqual(by_school["Alpha"]["record"], "1-0")
        self.assertEqual(by_school["Beta"]["record"], "0-1")
        self.assertNotEqual(by_school["Alpha"]["cors"], 18.25)
        self.assertGreater(by_school["Alpha"]["cors"], by_school["Beta"]["cors"])
        compat = ranking_for_week(snapshot, 0, self.prior())
        self.assertEqual(
            {row["school"]: row["cors"] for row in compat},
            {row["school"]: row["cors"] for row in scored},
        )

    def test_week_zero_compatibility_api_cannot_bypass_prior_final(self):
        snapshot = self.snapshot(complete_through_week=0)
        with self.assertRaises(RankingContractError):
            # This mapping looks like a prior ranking but is not a validated
            # prior FINAL and must not initialize a post-genesis Week 0.
            week_ranking(snapshot, 0, previous_ranking={"Alpha": 99.0, "Beta": -5.0})
        with self.assertRaises(RankingContractError):
            ranking_for_week(snapshot, 0, {"Alpha": 99.0, "Beta": -5.0})

    def test_numbered_week_uses_only_completed_games_through_checkpoint(self):
        snapshot = self.snapshot(complete_through_week=1)
        rows = week_ranking(
            snapshot,
            1,
            previous_final=self.prior(),
        )
        by_school = {row["school"]: row for row in rows}

        # Both completed W0/W1 games count; the scheduled, unscored W2 game does not.
        self.assertEqual(by_school["Alpha"]["record"], "1-1")
        self.assertEqual(by_school["Beta"]["record"], "1-1")

    def test_post_genesis_carryover_rejects_missing_malformed_incomplete_and_mismatched(self):
        snapshot = self.snapshot(complete_through_week=0)
        invalid = (
            None,
            PreviousFinal({"Alpha": 20.0, "Beta": float("nan")}, {}),
            PreviousFinal({"Alpha": 20.0}, {}),
            {"Alpha": 20.0, "Gamma": 10.0},
            {"Alpha": "not-a-number", "Beta": 10.0},
        )
        for prior in invalid:
            with self.subTest(prior=prior):
                with self.assertRaises(RankingContractError):
                    preseason_ranking(snapshot, prior)

    def test_duplicate_rows_in_prior_final_are_rejected(self):
        snapshot = self.snapshot(complete_through_week=0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prior.json"
            path.write_text(
                json.dumps(
                    [
                        {"school": "Alpha", "cors": 20.0},
                        {"school": "Alpha", "cors": 19.0},
                        {"school": "Beta", "cors": 10.0},
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RankingContractError):
                preseason_ranking(snapshot, path)

    def test_final_requires_complete_snapshot(self):
        incomplete = self.snapshot(complete_through_week=1)
        with self.assertRaises(RankingContractError):
            final_ranking(incomplete, self.prior())

        complete = self.snapshot(complete_through_week=2)
        # Replace the scheduled W2 game with a scored result for a complete snapshot.
        complete = SeasonSnapshot(
            complete.sport,
            complete.classification,
            complete.year,
            complete.teams,
            complete.games[:-1]
            + (SourceGame(2, "Alpha", "fbs", 28, "Beta", "fbs", 7, False),),
            complete.metadata,
            complete.checksum,
        )
        rows = ranking_for_phase(complete, RankingPhase.FINAL, previous_final=self.prior())
        self.assertEqual({row["school"] for row in rows}, {"Alpha", "Beta"})


if __name__ == "__main__":
    unittest.main()
