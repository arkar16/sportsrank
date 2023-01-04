import pandas as pd
import numpy as np
import os
import config

def week_zero_readjust(year, division, teams, week_zero_file):
    os.chdir(config.owd)
    os.chdir(f"{year}/rankings/")

    missing_teams = teams[~teams["school"].isin(week_zero_file["school"])]
    missing_teams_copy = missing_teams.copy()
    # TODO MAKE SURE MISSING TEAMS MATCHES WEEK ZERO FILE
    missing_teams_copy["cors"] = config.fcs_constant 
    print(missing_teams)
    # FIXME FIX DROPPING RANK
    #week_zero_file = week_zero_file.drop(columns="rank", inplace=True)
    week_zero_file.loc[:, "record"] = "0-0"
    week_zero_file.loc[:, "win_pct"] = ""
    #week_zero_file = week_zero_file.insert(0, "logo", "")
    # TODO ADD LOGOS TO WEEK ZERO
    week_zero_file.reset_index(inplace=True)
    week_zero_file.index = range(1, week_zero_file.shape[0] + 1)
    week_zero_file.columns.name = "rank"

    #print(week_zero_file)
    week_zero_html = week_zero_file.to_html(escape=False)
    with open(f"{year}_W0_{division}_cors.html", "w") as f:
        f.write(week_zero_html)
    os.chdir(config.owd)

    return week_zero_file