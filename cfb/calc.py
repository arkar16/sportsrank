import os
import config
from records import get_current_records
from teams import *
from cors import weekly_cors
from games import *
from spread import *
import pandas as pd
import shutil
from readjust import week_zero_readjust
# FIXME NEED TO FIX ALL FUNCTIONS TO REMOVE CONSTANTS AND USE PARAMETERS
# TODO CHANGE ALL WEEK CODE TO FUNCTIONS TO CALL

def single_week_calc(year, week, end_week, division, hfa, base_cors):
    # CONSTANTS
    YEAR = year  # define year
    WEEK = week  # define week
    END_WEEK = end_week  # define end week
    DIVISION = division  # define division (currently only supporting FBS)
    HFA = hfa  # define HFA constant
    BASE_CORS = base_cors

    # get original working directory
    os.chdir(config.owd)
    old_cors_file = f"{config.owd}/{YEAR - 1}/rankings/{YEAR - 1}_FINAL_{DIVISION}_cors.html"  # last year final CORS ranking
    dst = f"{config.owd}/{YEAR}/rankings"
    week_zero_cors = f"{YEAR}_W0_{DIVISION}_cors.html"

    if WEEK == 0:
        teams = get_teams(YEAR, DIVISION)
        print("teams done")
        get_slate(YEAR, DIVISION)
        print("slate done")
        current_records = get_current_records(YEAR, WEEK, DIVISION)
        print("records done")
        shutil.copy(old_cors_file, dst + "/" + week_zero_cors)  # copies FINAL to WEEK 0
        print("copy done")
        os.chdir(f"{YEAR}/rankings")
        week_zero_file = pd.read_html(f"{YEAR}_W0_{DIVISION}_cors.html")[0].set_index("rank")
        week_zero_file_df = week_zero_file.drop(columns="logo")
        week_cors = week_zero_readjust(YEAR, DIVISION, teams, week_zero_file_df)
        print("readjust done")
        weekly_spread(YEAR, WEEK, DIVISION, week_cors, HFA)
        print("spread done")
        print(f"W{WEEK} done")
    elif WEEK != END_WEEK:
        current_records = get_current_records(YEAR, WEEK, DIVISION)
        print("records done")
        weekly_results = get_weekly_results(YEAR, WEEK, DIVISION)
        print("weekly results done")
        results = get_results(YEAR, DIVISION)  # need to edit weekly_results to pass this through as a parameter
        print("results done")
        week_cors = weekly_cors(BASE_CORS, YEAR, WEEK, END_WEEK, DIVISION, current_records, results, weekly_results)
        print("week cors done")
        week_games = get_week_slate(YEAR, WEEK, DIVISION)
        print("week games done")
        weekly_spread(YEAR, WEEK, DIVISION, week_cors, HFA)
        print("week spread done")
        print(f"W{WEEK} done")
    else:
        current_records = get_current_records(YEAR, WEEK, DIVISION)
        print("records done")
        weekly_results = get_weekly_results(YEAR, WEEK, DIVISION)
        print("weekly results done")
        results = get_results(YEAR, DIVISION)  # need to edit weekly_results to pass this through as a parameter
        print("results done")
        week_cors = weekly_cors(BASE_CORS, YEAR, WEEK, END_WEEK, DIVISION, current_records, results, weekly_results)
        print("week cors done")
        week_games = get_week_slate(YEAR, WEEK, DIVISION)
        print("week games done")
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
    os.chdir(config.owd)
    old_cors_file = f"{config.owd}/{YEAR - 1}/rankings/{YEAR - 1}_FINAL_{DIVISION}_cors.html"  # last year final CORS ranking
    dst = f"{config.owd}/{YEAR}/rankings"
    week_zero_cors = f"{YEAR}_W0_{DIVISION}_cors.html"

    for i in range(WEEK, END_WEEK + 1):
        if WEEK == 0:
            teams = get_teams(YEAR, DIVISION)
            print("teams done")
            get_slate(YEAR, DIVISION)
            print("slate done")
            current_records = get_current_records(YEAR, WEEK, DIVISION)
            print("records done")
            shutil.copy(old_cors_file, dst + "/" + week_zero_cors)  # copies FINAL to WEEK 0
            print("copy done")
            os.chdir(f"{YEAR}/rankings")
            week_zero_file = pd.read_html(f"{YEAR}_W0_{DIVISION}_cors.html")[0].set_index("rank")
            week_zero_file_df = week_zero_file.drop(columns="logo")
            week_cors = week_zero_readjust(YEAR, DIVISION, teams, week_zero_file_df)
            print("readjust done")
            weekly_spread(YEAR, WEEK, DIVISION, week_cors, HFA)
            print("spread done")
            print(f"W{WEEK} done")
        elif WEEK != END_WEEK:
            current_records = get_current_records(YEAR, WEEK, DIVISION)
            print("records done")
            weekly_results = get_weekly_results(YEAR, WEEK, DIVISION)
            print("weekly results done")
            results = get_results(YEAR, DIVISION)  # need to edit weekly_results to pass this through as a parameter
            print("results done")
            week_cors = weekly_cors(BASE_CORS, YEAR, WEEK, END_WEEK, DIVISION, current_records, results, weekly_results)
            print("week cors done")
            week_games = get_week_slate(YEAR, WEEK, DIVISION)
            print("week games done")
            weekly_spread(YEAR, WEEK, DIVISION, week_cors, HFA)
            print("week spread done")
            print(f"W{WEEK} done")
        else:
            current_records = get_current_records(YEAR, WEEK, DIVISION)
            print("records done")
            weekly_results = get_weekly_results(YEAR, WEEK, DIVISION)
            print("weekly results done")
            results = get_results(YEAR, DIVISION)  # need to edit weekly_results to pass this through as a parameter
            print("results done")
            week_cors = weekly_cors(BASE_CORS, YEAR, WEEK, END_WEEK, DIVISION, current_records, results, weekly_results)
            print("week cors done")
            week_games = get_week_slate(YEAR, WEEK, DIVISION)
            print("week games done")
            print(f"W{WEEK} done")
        WEEK += 1
