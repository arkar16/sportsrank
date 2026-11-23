"""Team records from the score archive preceding the final CORS series."""
import csv
from collections import defaultdict
from pathlib import Path

from cfb.reference_markup import school_label

SOURCE = "https://academics.smcvt.edu/jtrono/BBallArchive.htm"


def read_early_records(before_year):
    years = defaultdict(list)
    with Path(__file__).with_name("early_records.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            year = int(row["season"])
            if year < before_year:
                years[year].append((row["school"], *(int(row[col]) for col in
                                    ("wins", "losses", "ncaa_w", "ncaa_l", "nit_w", "nit_l"))))
    return dict(years)


def record_rows(records, routes):
    def record(wins, losses):
        return f"{wins}–{losses}" if wins or losses else "—"

    for school, wins, losses, nw, nl, iw, il in records:
        yield (school_label(school, routes), record(wins + nw + iw, losses + nl + il),
               record(wins, losses), record(nw, nl), record(iw, il))
