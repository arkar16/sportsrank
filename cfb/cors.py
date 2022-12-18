import random
import pandas as pd
from records import get_current_records
from games import get_weekly_results
import os


def weekly_cors(base, year, week, division):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb")
    owd = os.getcwd()

    # constants
    BASE_CORS = base
    YEAR = year
    WEEK = week
    DIVISION = division

    cors_teams_df = get_current_records(YEAR, WEEK, DIVISION).copy()
    weekly_results = get_weekly_results(YEAR, WEEK, DIVISION)

    for index, row in cors_teams_df.iterrows():
        wins = row["wins"]
        losses = row["losses"]
        cors = (BASE_CORS + (wins * 50) - (losses * 25))
        cors_teams_df.loc[index, "cors"] = cors

    cors_teams_df = cors_teams_df.drop(columns=["wins", "losses"])
    cors_teams_df = cors_teams_df.sort_values(by=["cors"], ascending=False, ignore_index=True)
    cors_teams_df.index = range(1, cors_teams_df.shape[0] + 1)
    cors_teams_df.columns.name = "rank"

    # set escape tag false to prevent HTML code passthrough as plain text
    cors_html = cors_teams_df.to_html(escape=False)
    os.chdir("rankings")
    os.chdir(f"{YEAR}_rankings")
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_cors.html", "w") as f:
        f.write(cors_html)
    os.chdir(owd)

    return cors_teams_df

weekly_cors(1000, 2022, 2, "fbs")