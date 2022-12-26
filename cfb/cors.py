import random
import pandas as pd
import os


def cors_calc(base, wins, losses, weekly_results):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb/years")
    owd = os.getcwd()

    # constants
    BASE_CORS = base
    wins = int(wins)
    losses = int(losses)
    win_pct = round((wins / (wins + losses)), 2)

    # weekly_results_df = weekly_results MOV comes next

    # values that affect the ranking
    win_pct_m = round(((win_pct * 100) / 3), 2)
    cors = win_pct_m
    # mov = next to add
    # sos = next to add

    return cors


def weekly_cors(base, year, week, division, current_records, weekly_results):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb/years")
    owd = os.getcwd()

    # constants
    BASE_CORS = base
    YEAR = year
    WEEK = week
    DIVISION = division

    cors_teams_df = current_records.copy()
    weekly_results_df = weekly_results

    for index, row in cors_teams_df.iterrows():
        wins = row["wins"]
        losses = row["losses"]
        cors = cors_calc(BASE_CORS, wins, losses, weekly_results)
        cors_teams_df.loc[index, "cors"] = cors

    cors_teams_df = cors_teams_df.drop(columns=["wins", "losses"])
    cors_teams_df = cors_teams_df.sort_values(by=["cors"], ascending=False, ignore_index=True)
    cors_teams_df.index = range(1, cors_teams_df.shape[0] + 1)
    cors_teams_df.columns.name = "rank"

    # set escape tag false to prevent HTML code passthrough as plain text
    cors_html = cors_teams_df.to_html(escape=False)
    os.chdir(f"{YEAR}/rankings")
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_cors.html", "w") as f:
        f.write(cors_html)
    os.chdir(owd)

    return cors_teams_df
