"""Historical school records from before the national score archive."""
import csv
from collections import defaultdict
from html import escape
from pathlib import Path

from cfb.reference_markup import school_label

ROOT = Path(__file__).parent
REVISION = "8189b49651171b032d1c1d19f54f0d39ee1ed675"
SOURCE = f"https://github.com/pollackelliott/college-basketball-history/blob/{REVISION}"
FOUNDING_SOURCES = {
    "geneva-founding": "https://athletics.geneva.edu/sports/2017/10/30/mens-basketball-all-time-scores-1892-1930.aspx",
    "chicago-founding": "https://athletics.uchicago.edu/sports/mens-basketball/schedule/1893-94?grid=true",
    "temple-founding": "https://owlsports.com/documents/download/2023/10/31/MBB_Record_Book_-_Updated_Cover.pdf",
}


def read_program_records(before_year):
    seasons = defaultdict(list)
    with (ROOT / "program_records.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            year = int(row["season"])
            if year < before_year:
                seasons[year].append(row)
    return seasons


def program_record_rows(records, routes):
    for r in records:
        label = school_label(r["school"], routes)
        record = f'{r["wins"]}–{r["losses"]}'
        if int(r["ties"]):
            record += f'–{r["ties"]}'
        key = r["source"]
        url = FOUNDING_SOURCES.get(key, f"{SOURCE}/schools/{key}/source-notes.md")
        source = f'<a href="{escape(url, quote=True)}">Source</a>'
        yield label, record, escape(r["conference"] or "—"), " ".join(filter(None, (escape(r["coverage"]), source)))
