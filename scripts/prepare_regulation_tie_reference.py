"""Freeze tied-regulation drama from validated ESPN scoring histories in the reference seasons."""
from dataclasses import replace
import json
from pathlib import Path

from cfb.excitement import QualifiedPregame, calculate_full_aev
from cfb.game_excitement import flow_inputs


def reference_rows(data):
    # Truncate verified overtime games at regulation, so training has genuine tied endings.
    rows = []
    for year in (2024, 2026):
        source = json.loads((data / f"{year}.json").read_bytes())
        for r in source["games"]:
            if not r["overtime"] or not r.get("states"):
                continue
            final, points = flow_inputs(r, year, source["source"])
            regulation = tuple(p for p in points if not p.overtime)
            end = regulation[-1]
            if end.home_score != end.away_score:
                raise ValueError("overtime reference has no regulation tie")
            tied = replace(final, home_score=end.home_score, away_score=end.away_score, overtime=False)
            pre = QualifiedPregame(1, 1, None, "reference", "regulation")
            value = calculate_full_aev(pre, tied, regulation)
            rows.append((final.game_id, end.home_score, value.components.drama_points))
    return sorted(rows)


if __name__ == "__main__":
    data = Path(__file__).resolve().parents[1] / "cfb/excitement_data"
    (data / "regulation-ties-v1.json").write_text(json.dumps(reference_rows(data), separators=(",", ":")) + "\n")
