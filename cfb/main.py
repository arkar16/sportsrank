# this file calls all other functions to create rankings for a particular season or week

import os
from teams import get_teams
from cors import weekly_cors
from games import *

# CONSTANTS
YEAR = 2022  # define year
WEEK = 10  # define week
DIVISION = "fbs"  # define division (currently only supporting FBS)
BASE_CORS = 1000

get_teams(YEAR, DIVISION)  # sets team file for year and division, creates HTML output
get_slate(YEAR, DIVISION)  # updates season schedule, creates HTML output
get_results(YEAR, DIVISION)  # updates season results, creates HTML output

get_week_slate(YEAR, WEEK, DIVISION)  # gets weekly slate
weekly_cors(BASE_CORS, YEAR, WEEK, DIVISION)  # runs CORS calculation for set year and week, creates HTML output
