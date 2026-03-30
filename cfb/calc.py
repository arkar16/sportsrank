import os
import config
from records import get_current_records
from teams import *
from games import *
from spread import *
from spread_results import analyze_last_week_spreads
from bs4 import BeautifulSoup
from end_week import get_end_week
from rankings_pipeline import generate_rankings_through_week

# TODO
#def hfa_calc():
#    return None


# TODO def postseason_games() -> maybe not?
# TODO add timestamp to all files
def if_week_zero_true(year, week, division, hfa, timestamp):
    print(f"Starting {year}")
    os.chdir(config.owd)
    get_teams(year, division, timestamp)
    get_slate(year, division, timestamp)
    get_current_records(year, week, division, timestamp)
    rankings_frame = generate_rankings_through_week(year, week, division, timestamp)
    weekly_spread(year, week, division, rankings_frame.to_pandas(), hfa, timestamp)

def regular_season_week(year, week, end_week, division, hfa, base_cors, timestamp):
    get_current_records(year, week, division, timestamp)
    weekly_results = get_weekly_results(year, week, division, timestamp)
    get_results(year, division, timestamp)
    rankings_frame = generate_rankings_through_week(year, week, division, timestamp)
    weekly_spread(year, week, division, rankings_frame.to_pandas(), hfa, timestamp)
    
    # After calculating this week's spreads, analyze last week's results
    if week > 1:  # Only if not week 0
        # Get last week's results
        last_week_results = get_weekly_results(year, week-1, division, timestamp)
        analyze_last_week_spreads(year, week, division, last_week_results, timestamp)
        #print("spread results analysis done")
    
    #print(f"Y{year} - W{week} done") 

def last_regular_week(year, week, end_week, division, hfa, base_cors, timestamp):
    get_current_records(year, week, division, timestamp)
    get_weekly_results(year, week, division, timestamp)
    get_results(year, division, timestamp)
    generate_rankings_through_week(year, week, division, timestamp)
    last_week_results = get_weekly_results(year, week - 1, division, timestamp)
    analyze_last_week_spreads(year, week, division, last_week_results, timestamp)
    print(f"Y{year} done") 

def single_week_calc(year, week, end_week, division, hfa, base_cors, timestamp):
    # CONSTANTS
    YEAR = year  # define year
    WEEK = week  # define week
    END_WEEK = end_week  # define end week
    DIVISION = division  # define division (currently only supporting FBS)
    HFA = hfa  # define HFA constant
    BASE_CORS = base_cors

    if WEEK == 0:
        if_week_zero_true(year, week, division, hfa, timestamp)
    elif WEEK != END_WEEK:
        regular_season_week(year, week, end_week, division, hfa, base_cors, timestamp)
    else:
        last_regular_week(year, week, end_week, division, hfa, base_cors, timestamp)


def full_season_calc(year, week, end_week, division, hfa, base_cors, timestamp):
    # CONSTANTS
    YEAR = year  # define year
    WEEK = week  # define week
    END_WEEK = end_week  # define end week
    DIVISION = division  # define division (currently only supporting FBS)
    HFA = hfa  # define HFA constant
    BASE_CORS = base_cors

    for wk in range(WEEK, END_WEEK + 1):
        if wk == 0:
            if_week_zero_true(year, wk, division, hfa, timestamp)
        elif wk != END_WEEK:
            regular_season_week(year, wk, end_week, division, hfa, base_cors, timestamp)
        else:
            last_regular_week(year, wk, end_week, division, hfa, base_cors, timestamp)

def history_calc(year, end_year, week, end_week, division, hfa, base_cors, timestamp):
    for i in range(year, end_year + 1):
        end_week = get_end_week(i)
        #print(f"{i} end week: {end_week}")
        for wk in range(week, end_week + 1):
            if wk == 0:
                if_week_zero_true(year, wk, division, hfa, timestamp)
            elif wk != end_week:
                regular_season_week(year, wk, end_week, division, hfa, base_cors, timestamp)
            else:
                last_regular_week(year, wk, end_week, division, hfa, base_cors, timestamp)
            #print(f"Y{year} W{wk} done")
        year += 1
