from calc import *

# CONSTANTS
YEAR = 2022  # define year
WEEK = 0  # define week
END_WEEK = 15  # define last week of season
DIVISION = "fbs"  # define division (currently only supporting FBS)
HFA = 2  # define HFA constant for spread
BASE_CORS = 0

#single_week_calc(YEAR, WEEK, END_WEEK, DIVISION, HFA, BASE_CORS)  # for week-by-week calculations
full_season_calc(YEAR, WEEK, END_WEEK, DIVISION, HFA, BASE_CORS)  # for full season calculations

# TODO add main and config to outside CFB folder to allow for different sports