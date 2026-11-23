"""Read the offline, final-only archive preceding the modern CBBD era."""
from collections import defaultdict
import csv
from decimal import Decimal
from pathlib import Path

from cfb.reference_archive import Season, Standing

_ROOT = Path(__file__).parent
_CONFERENCES = {
    "a_sun": "ASUN", "a_ten": "A-10", "aac": "American", "aec": "Am. East",
    "big_east": "Big East", "big_eight": "Big Eight", "big_sky": "Big Sky",
    "big_south": "Big South", "big_ten": "Big Ten", "big_twelve": "Big 12",
    "big_west": "Big West", "horizon": "Horizon", "ind": "Indep.", "ivy": "Ivy",
    "maac": "Metro", "metro": "Metro Conference", "mid_cont": "Mid-Continent",
    "mw_city": "Midwestern City", "mw_coll": "Midwestern Collegiate", "mwc": "Mountain West",
    "pac_ten": "Pac-10", "pac_twelve": "Pac-12", "patriot": "Patriot", "southern": "SoCon",
    "southland": "Southland", "summit": "Summit", "sun_belt": "Sun Belt",
}


def read_archive(before_year):
    with (_ROOT / "archive_programs.csv").open(encoding="utf-8") as f:
        programs = {r["historical_id"]: int(r["program_id"]) for r in csv.DictReader(f)}
    ids = {}
    years = defaultdict(list)
    with (_ROOT / "archive_finals.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            year = int(r["season"])
            if year >= before_year:
                continue
            tid, school = r["team_id"], r["school"]
            program = programs.get(tid, f"historical-{tid}")
            if ids.setdefault(program, school) != school:
                raise ValueError(f"Ambiguous historical basketball program: {program}")
            years[year].append(Standing(school, int(r["rank"]), Decimal(r["cors"]),
                                        f'{r["wins"]}–{r["losses"]}',
                                        _CONFERENCES.get(r["conference"], r["conference"].upper())))
    return [Season(year, {"FINAL": standings}, []) for year, standings in sorted(years.items())], ids
