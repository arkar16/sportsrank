from records import get_current_records
from teams import *
from cors import weekly_cors
from games import *
from spread import *
import pandas as pd

# CONSTANTS
YEAR = 2022  # define year
WEEK = 1  # define week
DIVISION = "fbs"  # define division (currently only supporting FBS)
BASE_CORS = 1000

# get original working directory
os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb")
owd = os.getcwd()

if WEEK == 0:
    get_teams(YEAR, DIVISION)
    get_slate(YEAR, DIVISION)
    current_records = get_current_records(YEAR, WEEK, DIVISION)
    os.chdir(f"rankings/{YEAR - 1}_rankings")
    week_cors = pd.read_html(f"{YEAR - 1}_W15_{DIVISION}_cors.html")[0].set_index("rank")
    weekly_spread(YEAR, WEEK, DIVISION, week_cors)
else:
    current_records = get_current_records(YEAR, WEEK, DIVISION)
    weekly_results = get_weekly_results(YEAR, WEEK, DIVISION)
    week_cors = weekly_cors(BASE_CORS, YEAR, WEEK, DIVISION, current_records, weekly_results)
    week_games = get_week_slate(YEAR, WEEK, DIVISION)
    weekly_spread(YEAR, WEEK, DIVISION, week_cors)

