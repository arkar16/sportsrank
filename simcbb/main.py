from calc import *
import time
from html_grab import *
from nc_wt_clean import *


# CONSTANTS
start_time = time.time()
YEAR = 2022  # define current year (cannot be earlier than 2022)
START_YEAR = 2022  # define start year
END_YEAR = 2022 # define end year
WEEK = 8  # define current week
START_WEEK = 0  # define start week
END_WEEK = 14  # define last week of season
DIVISION = "D1"  # define division (currently only supporting D1)
HFA = 2  # define HFA constant for spread
BASE_CORS = 0

# create timestamp variable
timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

#single_week_calc(YEAR, WEEK, END_WEEK, DIVISION, HFA, BASE_CORS, timestamp)  # for week-by-week calculations
#full_season_calc(YEAR, START_WEEK, END_WEEK, DIVISION, HFA, BASE_CORS, timestamp)  # for full season calculations
#history_calc(START_YEAR, END_YEAR, START_WEEK, END_WEEK, DIVISION, HFA, BASE_CORS, timestamp) # for historical year to year calculations

# files to run after running a calc
#nc_clean(DIVISION, timestamp) # cleans up NC files
#wt_clean(DIVISION, timestamp) # cleans up NC files
html_grab(START_YEAR, END_YEAR, START_WEEK, END_WEEK, DIVISION, timestamp) # writes to index.html TODO move to outside CFB folder to allow for different sports

print("Process finished --- %s seconds ---" % (time.time() - start_time))