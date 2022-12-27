from records import get_current_records
from teams import *
from cors import weekly_cors
from games import *
from spread import *
import pandas as pd


def single_week_calc(year, week, division, hfa, base_cors):
    # CONSTANTS
    YEAR = year  # define year
    WEEK = week  # define week
    DIVISION = division  # define division (currently only supporting FBS)
    HFA = hfa  # define HFA constant
    BASE_CORS = base_cors

    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb/years")
    owd = os.getcwd()

    if WEEK == 0:
        get_teams(YEAR, DIVISION)
        get_slate(YEAR, DIVISION)
        current_records = get_current_records(YEAR, WEEK, DIVISION)
        os.chdir(f"{YEAR - 1}/rankings")
        week_cors = pd.read_html(f"{YEAR - 1}_W15_{DIVISION}_cors.html")[0].set_index("rank")
        # write week_cors as W0 in new year directory
        weekly_spread(YEAR, WEEK, DIVISION, week_cors, HFA)
    else:
        current_records = get_current_records(YEAR, WEEK, DIVISION)
        print("records done")
        weekly_results = get_weekly_results(YEAR, WEEK, DIVISION)
        print("weekly results done")
        results = get_results(YEAR, DIVISION)  # need to edit weekly_results to pass this through as a parameter
        print("results done")
        week_cors = weekly_cors(BASE_CORS, YEAR, WEEK, DIVISION, current_records, results, weekly_results)
        print("week cors done")
        week_games = get_week_slate(YEAR, WEEK, DIVISION)
        print("week games done")
        weekly_spread(YEAR, WEEK, DIVISION, week_cors, HFA)
        print("week spread done")
        print(f"W{WEEK} done")


def full_season_calc(year, week, end_week, division, hfa, base_cors):
    # CONSTANTS
    YEAR = year  # define year
    WEEK = week  # define week
    END_WEEK = end_week  # define end week
    DIVISION = division  # define division (currently only supporting FBS)
    HFA = hfa  # define HFA constant
    BASE_CORS = base_cors

    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb/years")
    owd = os.getcwd()

    for i in range(WEEK, END_WEEK + 1):
        if WEEK == 0:
            get_teams(YEAR, DIVISION)
            get_slate(YEAR, DIVISION)
            current_records = get_current_records(YEAR, WEEK, DIVISION)
            os.chdir(f"{YEAR - 1}/rankings")
            week_cors = pd.read_html(f"{YEAR - 1}_W15_{DIVISION}_cors.html")[0].set_index("rank")
            # write week_cors as W0 in new year directory
            weekly_spread(YEAR, WEEK, DIVISION, week_cors, HFA)
        else:
            current_records = get_current_records(YEAR, WEEK, DIVISION)
            print("records done")
            weekly_results = get_weekly_results(YEAR, WEEK, DIVISION)
            print("weekly results done")
            results = get_results(YEAR, DIVISION)  # need to edit weekly_results to pass this through as a parameter
            print("results done")
            week_cors = weekly_cors(BASE_CORS, YEAR, WEEK, DIVISION, current_records, results, weekly_results)
            print("week cors done")
            week_games = get_week_slate(YEAR, WEEK, DIVISION)
            print("week games done")
            weekly_spread(YEAR, WEEK, DIVISION, week_cors, HFA)
            print("week spread done")
            print(f"W{WEEK} done")
        WEEK += 1
