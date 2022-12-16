import random
import pandas as pd
from records import cfb_records
from teams import get_teams
import os


def weekly_cors(year, week, division):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb")
    owd = os.getcwd()

    # constants
    BASE_CORS = 1000
    YEAR = year
    WEEK = week
    DIVISION = division

    cfb_teams = get_teams(YEAR, DIVISION)
    cors_team = cfb_records
    cors_team.insert(0, "logo", None)

    for index, row in cors_team.iterrows():
        school = row["school"]
        # Select the row with the current school in the cfb_teams DataFrame
        logo_row = cfb_teams.loc[cfb_teams["school"] == school]
        # If a matching row was found, get the value of the "logo" column
        if not logo_row.empty:
            logo = logo_row.iloc[0]["logo"]
        # If no matching row was found, set the logo to None
        else:
            logo = None
        # Update the "logo" column for the current row
        cors_team.loc[index, "logo"] = logo
        wins = row["wins"]
        losses = row["losses"]
        cors = (BASE_CORS + (wins * 50) - (losses * 25))
        cors_team.loc[index, "cors"] = cors

    cors_team = cors_team.drop(columns=["wins", "losses"])
    cors_team = cors_team.sort_values(by=["cors"], ascending=False, ignore_index=True)

    # set escape tag false to prevent HTML code passthrough as plain text
    cors_html = cors_team.to_html(escape=False)
    os.chdir("rankings")
    os.chdir(f"{YEAR}_rankings")
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_cors.html", "w") as f:
        f.write(cors_html)
    os.chdir(owd)
