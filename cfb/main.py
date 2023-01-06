from calc import *
from end_week import get_end_week
import time
from html_grab import *
from nc_wt_clean import *


# CONSTANTS
start_time = time.time()
YEAR = 1897  # define year (cannot be earlier than 1897)
END_YEAR = 2022 # define end year
WEEK = 0  # define week
END_WEEK = get_end_week(YEAR)  # define last week of season
DIVISION = "fbs"  # define division (currently only supporting FBS)
HFA = 2  # define HFA constant for spread
BASE_CORS = 0

#single_week_calc(YEAR, WEEK, END_WEEK, DIVISION, HFA, BASE_CORS)  # for week-by-week calculations
#full_season_calc(YEAR, WEEK, END_WEEK, DIVISION, HFA, BASE_CORS)  # for full season calculations
#history_calc(YEAR, END_YEAR, WEEK, END_WEEK, DIVISION, HFA, BASE_CORS) # for historical year to year calculations

# files to run after running a calc

html_grab(YEAR, END_YEAR, DIVISION) # writes to index.html
#nc_clean(DIVISION) # cleans up NC files
#wt_clean(DIVISION) # cleans up NC files

# TODO add main and config to outside CFB folder to allow for different sports

print("Process finished --- %s seconds ---" % (time.time() - start_time))