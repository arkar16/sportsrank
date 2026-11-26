"""Check every basketball guide against the normalized CollegeBasketballData games."""
import argparse
from collections import Counter
from pathlib import Path
import re
import sqlite3

from lxml import html


def verify(database: Path, site: Path) -> Counter:
    counts = Counter()
    guides = {int(path.parents[1].name): path
              for path in site.glob("seasons/*/excitement/index.html")}
    query = """
        SELECT id, status, json_extract(raw_json, '$.homePoints'),
               json_extract(raw_json, '$.awayPoints')
        FROM games WHERE season = ? AND status IN ('final','scheduled','unverified','in_progress')
    """
    score = re.compile(r"(?:100\.0|[0-9]{1,2}\.[0-9])")
    labels = {"Play-by-play", "Halftime score", "Final score only"}
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        years = {year for year, in connection.execute("SELECT DISTINCT season FROM games")}
        if not years or years != guides.keys():
            raise ValueError(f"Guide seasons differ from source seasons: {sorted(years ^ guides.keys())}")
        for year in sorted(years):
            remaining = {str(game_id): (status, home, away) for game_id, status, home, away
                         in connection.execute(query, (year,))}
            doc = html.parse(guides[year], parser=html.HTMLParser(encoding="utf-8"))
            for row in doc.xpath("//tbody/tr"):
                ids = row.xpath(".//*[@data-cbbd-fixture]/@data-cbbd-fixture")
                if len(ids) != 1 or ids[0] not in remaining:
                    raise ValueError(f"{year}: duplicate or unknown fixture {ids}")
                game_id, = ids
                status, home, away = remaining.pop(game_id)
                cells = row.xpath("./th|./td")
                if len(cells) != (7 if status == "final" else 5):
                    raise ValueError(f"{year} game {game_id}: wrong fields for {status}")
                bev = cells[4].text_content().partition(" · ")[0].strip()
                if not score.fullmatch(bev):
                    raise ValueError(f"{year} game {game_id}: invalid BEV {bev!r}")
                counts[status] += 1
                if status != "final":
                    continue
                result = cells[-2]
                final_id = result.xpath(".//*[@data-cbbd-game]/@data-cbbd-game")
                if final_id != [game_id] or result.text_content().strip() != f"{away}–{home}":
                    raise ValueError(f"{year} game {game_id}: final differs from the official result")
                aev, _, label = cells[-1].text_content().strip().partition(" · ")
                if (label not in labels or not score.fullmatch(aev.removesuffix("*"))
                        or aev.endswith("*") != (label != "Play-by-play")):
                    raise ValueError(f"{year} game {game_id}: invalid AEV or evidence label")
                counts[label] += 1
            if remaining:
                raise ValueError(f"{year}: {len(remaining)} missing fixtures, including {next(iter(remaining))}")
            counts["guides"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--site", type=Path, default=Path("website/cbb"))
    args = parser.parse_args()
    print(dict(verify(args.database, args.site)))


if __name__ == "__main__":
    main()
