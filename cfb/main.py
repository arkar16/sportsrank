from teams import get_teams
from records import get_records

# CONSTANTS
YEAR = 2007  # define year
WEEK = 0  # define week
DIVISION = "fbs"  # define division (currently only supporting FBS)
BASE_CORS = 500

for i in range(0, 16):
    get_records(YEAR, WEEK, DIVISION)
    print(f"Week {WEEK} done")
    WEEK += 1
