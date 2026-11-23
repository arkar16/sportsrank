"""Collect the published ESPN men's D-I fixtures for a team-page schedule snapshot."""
import argparse
import csv
from datetime import date, timedelta
import gzip
from io import StringIO
import json
from pathlib import Path
from urllib.request import urlopen

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"


def collect(season, first, last, cache):
    fixtures = {}
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        path = cache / f"{day:%Y%m%d}.json"
        if path.exists():
            events = json.loads(path.read_bytes())
        else:
            with urlopen(f"{SCOREBOARD}?groups=50&dates={day:%Y%m%d}&limit=1000", timeout=30) as response:
                raw = response.read()
            if raw.startswith(b"\x1f\x8b"):
                raw = gzip.decompress(raw)
            events = json.loads(raw)["events"]
            path.write_text(json.dumps(events, separators=(",", ":")))
        if len(events) >= 1000:
            raise ValueError(f"Incomplete scoreboard on {day}")
        for event in events:
            game = event["competitions"][0]
            if event["season"]["year"] != season or game["status"]["type"]["name"] != "STATUS_SCHEDULED":
                continue
            teams = {row["homeAway"]: row["team"] for row in game["competitors"]}
            home, away = teams["home"], teams["away"]
            if type(game["timeValid"]) is not bool or type(game["neutralSite"]) is not bool:
                raise ValueError(f"Unknown venue or tipoff status for {event['id']}")
            row = (event["id"], game["date"], int(game["timeValid"]), int(game["neutralSite"]),
                   home["id"], home["location"], away["id"], away["location"])
            if event["id"] in fixtures and row != fixtures[event["id"]]:
                raise ValueError(f"Conflicting fixtures for {event['id']}")
            fixtures[event["id"]] = row
    return sorted(fixtures.values(), key=lambda row: (row[1], row[0]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--from-date", required=True, type=date.fromisoformat)
    parser.add_argument("--through-date", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", required=True, type=Path, help="Daily scoreboards for this acquisition")
    args = parser.parse_args()
    if args.through_date < args.from_date:
        parser.error("--through-date must be on or after --from-date")
    args.cache.mkdir(parents=True, exist_ok=True)
    rows = collect(args.season, args.from_date, args.through_date, args.cache)
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("event_id", "tipoff", "time_known", "neutral", "home_id", "home_name", "away_id", "away_name"))
    writer.writerows(rows)
    target = Path(__file__).resolve().parents[1] / "cbb/schedules" / str(args.season) / f"{args.from_date}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output.getvalue(), encoding="utf-8")
    print(f"{len(rows)} scheduled fixtures in {target}")


if __name__ == "__main__":
    main()
