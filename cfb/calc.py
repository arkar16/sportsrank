import os
import config
from records import get_current_records
from teams import *
from cors import weekly_cors
from games import *
from spread import *
from spread_results import analyze_last_week_spreads
import pandas as pd
import shutil
from readjust import week_zero_readjust
from bs4 import BeautifulSoup
from end_week import get_end_week

# TODO
#def hfa_calc():
#    return None


# TODO def postseason_games() -> maybe not?
# TODO add timestamp to all files
def if_week_zero_true(year, week, division, hfa, timestamp):
    print(f"Starting {year}")
    # get original working directory
    os.chdir(config.owd)
    if year == 1897:
        teams = get_teams(year, division, timestamp)
        #print("teams done W0")
        get_slate(year, division, timestamp)
        #print("slate done W0")
        get_current_records(year, week, division, timestamp)
        #print("records done W0")
        
        # Create blank template for 1897
        os.chdir(f"{year}/rankings")
        week_zero_file_df = teams.copy()
        week_zero_file_df["record"] = "0-0"
        week_zero_file_df["win_pct"] = ""
        week_zero_file_df["cors"] = 0.0
        week_zero_file_df.index = range(1, week_zero_file_df.shape[0] + 1)
        week_zero_file_df.columns.name = "rank"
        week_zero_html = week_zero_file_df.to_html(index=True, escape=False)
        with open(f"{year}_W0_{division}_cors.html", "w") as f:
            f.write(week_zero_html)
        
        #print("week zero file done")
        week_cors = week_zero_readjust(year, division, teams, week_zero_file_df, timestamp)
        #print("readjust done W0")
        weekly_spread(year, week, division, week_cors, hfa, timestamp)
        #print("spread done")
        #print(f"W{week} done")
    else:
        old_cors_file = f"{config.owd}/{year - 1}/rankings/{year - 1}_FINAL_{division}_cors.html"  # last year final CORS ranking
        dst = f"{config.owd}/{year}/rankings"
        week_zero_cors = f"{year}_W0_{division}_cors.html"
        teams = get_teams(year, division, timestamp)
        #print("teams done W0")
        get_slate(year, division, timestamp)
        #print("slate done W0")
        get_current_records(year, week, division, timestamp)
        #print("records done W0")
        shutil.copy(old_cors_file, dst + "/" + week_zero_cors)  # copies FINAL to WEEK 0
        #print("copy done W0")
        os.chdir(f"{year}/rankings")
        week_zero_file = pd.read_html(f"{year}_W0_{division}_cors.html")[0].set_index("rank")
        week_zero_file_df = week_zero_file
        #print("week zero file done")
        week_cors = week_zero_readjust(year, division, teams, week_zero_file_df, timestamp)
        #print("readjust done W0")
        weekly_spread(year, week, division, week_cors, hfa, timestamp)
        #print("spread done")
        #print(f"W{week} done")

def regular_season_week(year, week, end_week, division, hfa, base_cors, timestamp):
    current_records = get_current_records(year, week, division, timestamp)
    weekly_results = get_weekly_results(year, week, division, timestamp)
    results = get_results(year, division, timestamp)  # TODO need to edit weekly_results to pass this through as a parameter
    week_cors = weekly_cors(base_cors, year, week, end_week, division, current_records, results, weekly_results, timestamp)
    get_week_slate(year, week, division, timestamp)
    weekly_spread(year, week, division, week_cors, hfa, timestamp)
    
    # After calculating this week's spreads, analyze last week's results
    if week > 1:  # Only if not week 0
        # Get last week's results
        last_week_results = get_weekly_results(year, week-1, division, timestamp)
        analyze_last_week_spreads(year, week, division, last_week_results, timestamp)
        #print("spread results analysis done")
    
    #print(f"Y{year} - W{week} done") 

def last_regular_week(year, week, end_week, division, hfa, base_cors, timestamp):
    #print("Starting Last Regular Week")
    current_records = get_current_records(year, week, division, timestamp)
    weekly_results = get_weekly_results(year, week, division, timestamp)
    results = get_results(year, division, timestamp)  # need to edit weekly_results to pass this through as a parameter
    weekly_cors(base_cors, year, week, end_week, division, current_records, results, weekly_results, timestamp)
    get_week_slate(year, week, division, timestamp)
    last_week_results = get_weekly_results(year, week-1, division, timestamp)
    analyze_last_week_spreads(year, week, division, last_week_results, timestamp)
    #print("spread results analysis done")
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
