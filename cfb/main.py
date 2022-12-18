from records import get_current_records
from cors import weekly_cors
from games import *
from spread import *

# CONSTANTS
YEAR = 2022  # define year
WEEK = 1  # define week
DIVISION = "fbs"  # define division (currently only supporting FBS)
BASE_CORS = 1000

get_slate(YEAR, DIVISION)

for i in range(0, 16):
    weekly_cors(BASE_CORS, YEAR, WEEK, DIVISION)
    week_games = get_week_slate(YEAR, WEEK, DIVISION)
    weekly_spread(YEAR, WEEK, DIVISION, week_games)
    get_weekly_results(YEAR, WEEK, DIVISION)
    get_current_records(YEAR, WEEK, DIVISION)
    print(f"W{WEEK} done")
    WEEK += 1

