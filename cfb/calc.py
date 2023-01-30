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
from bs4 import BeautifulSoup


# TODO def postseason_games() -> maybe not?
# TODO add timestamp to all files


def if_week_zero_true(year, week, division, hfa, timestamp):
    # get original working directory
    os.chdir(config.owd)
    try:
        old_cors_file = f"{config.owd}/{year - 1}/rankings/{year - 1}_FINAL_{division}_cors.html"  # last year final CORS ranking
        dst = f"{config.owd}/{year}/rankings"
        week_zero_cors = f"{year}_W0_{division}_cors.html"
        teams = get_teams(year, division, timestamp)
        print("teams done W0")
        get_slate(year, division, timestamp)
        print("slate done W0")
        get_current_records(year, week, division, timestamp)
        print("records done W0")
        shutil.copy(old_cors_file, dst + "/" + week_zero_cors)  # copies FINAL to WEEK 0
        print("copy done W0")
        os.chdir(f"{year}/rankings")
        week_zero_file = pd.read_html(f"{year}_W0_{division}_cors.html")[0].set_index("rank")
        #with open(f"{year}_W0_{division}_cors.html") as f:
            #soup = BeautifulSoup(f, "html.parser")
        #for index, row in week_zero_file.iterrows(): # FIXME THIS DOES NOT WORK IF THE PAST SEASON TEAM DID NOT HAVE A LOGO
            #imgs = soup.find_all("img")
            #srcs = [img["src"] for img in imgs]
            #srcs_team = srcs[index-1:index]
            #src_str = " ".join(srcs_team)  # Concatenate the srcs list into a single string
            #if src_str:  # Check if src_str is not empty
                #week_zero_file.at[index, "logo"] = f"<center><img src='{src_str}' style='width: 20px; height: 20px;'></center>"
            #else:
                #week_zero_file.at[index, "logo"] = ""  # Set the value to "N/A" if no logo was found
        week_zero_file_df = week_zero_file #week_zero_file.drop(columns="logo")
        print("week zero file done")
        week_cors = week_zero_readjust(year, division, teams, week_zero_file_df, timestamp)
        print("readjust done W0")
        weekly_spread(year, week, division, week_cors, hfa)
        print("spread done")
        print(f"W{week} done")
    except:
        #teams = get_teams(year, division)
        #print("teams2 done")
        #get_slate(year, division, timestamp)
        #print("slate2 done")
        #current_records = get_current_records(year, week, division, timestamp)
        #print("records2 done")
        os.chdir(config.owd)
        os.chdir(f"{year}/rankings")
        week_zero_file_df = teams.copy()
        week_zero_file_df["record"] = "0-0"
        week_zero_file_df["win_pct"] = ""
        week_zero_file_df["cors"] = 0.0
        #week_zero_file_df.reset_index(inplace=True)
        week_zero_file_df.index = range(1, week_zero_file_df.shape[0] + 1)
        week_zero_file_df.columns.name = "rank"
        week_zero_html = week_zero_file_df.to_html(index=True, escape=False)
        with open(f"{year}_W{week}_{division}_cors.html", "w") as f:
            f.write(week_zero_html)
        os.chdir(config.owd)
        print("week zero file done")
        week_cors = week_zero_file_df
        print("cors done")
        #weekly_spread(year, week, division, week_cors, hfa)
        print("no spread")
        print(f"W{week} done")

def regular_season_week(year, week, end_week, division, hfa, base_cors, timestamp):
    current_records = get_current_records(year, week, division, timestamp)
    print("records done")
    weekly_results = get_weekly_results(year, week, division, timestamp)
    print("weekly results done")
    results = get_results(year, division, timestamp)  # TODO need to edit weekly_results to pass this through as a parameter
    print("results done")
    week_cors = weekly_cors(base_cors, year, week, end_week, division, current_records, results, weekly_results, timestamp)
    print("week cors done")
    week_games = get_week_slate(year, week, division, timestamp)
    print("week games done")
    weekly_spread(year, week, division, week_cors, hfa, timestamp)
    print("week spread done")
    print(f"W{week} done") 

def last_regular_week(year, week, end_week, division, hfa, base_cors, timestamp):
    current_records = get_current_records(year, week, division, timestamp)
    print("records done")
    weekly_results = get_weekly_results(year, week, division, timestamp)
    print("weekly results done")
    results = get_results(year, division, timestamp)  # need to edit weekly_results to pass this through as a parameter
    print("results done")
    week_cors = weekly_cors(base_cors, year, week, end_week, division, current_records, results, weekly_results, timestamp)
    print("week cors done")
    week_games = get_week_slate(year, week, division, timestamp)
    print("week games done")
    print(f"W{week} done")

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
        for wk in range(week, end_week + 1):
            if wk == 0:
                if_week_zero_true(year, wk, division, hfa, timestamp)
            elif wk != end_week:
                regular_season_week(year, wk, end_week, division, hfa, base_cors, timestamp)
            else:
                last_regular_week(year, wk, end_week, division, hfa, base_cors, timestamp)
            print(f"Y{year} W{wk} done")
        year += 1