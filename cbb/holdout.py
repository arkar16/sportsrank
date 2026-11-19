"""Evaluate the registered basketball finalist once, after development selection."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .experiment import BASELINE, REST_DAY_LIMIT, Candidate
from .grade import grade_margins
from .run_experiment import candidate_rows
from .rest import RestHistory
from .source import read_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--development", required=True, type=Path, help="Locked development report")
    args = parser.parse_args()
    finalist = json.loads(args.development.read_text())["finalist"]
    if finalist["through_season"] != 2024:
        parser.error("Expected development selection through 2024")
    chosen = finalist["model"]
    teams, = read_history(args.database, 2025, 2025)
    rests = {2025: RestHistory(teams.games)}
    facts = None
    report = {"season": 2025, "finalist": finalist}
    for name, model, coefficient in [
        ("baseline", BASELINE, 0.0),
        ("challenger", Candidate(**chosen["candidate"]), chosen["rest_coefficient"]),
    ]:
        rows = list(candidate_rows(args.database, model, 2025, 2025, rests))
        results = {r.game_id: r.actual_home_margin for r in rows}
        if len(rows) != len(results) or (facts is not None and facts != results):
            raise ValueError("Holdout games or scores differ between models")
        facts = results
        grade = grade_margins(
            (r.predicted_home_margin + coefficient * max(-REST_DAY_LIMIT, min(REST_DAY_LIMIT, r.rest_days_diff or 0)),
             r.actual_home_margin) for r in rows
        )
        report[name] = {**asdict(grade), "winner_accuracy": grade.winner_accuracy}
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
