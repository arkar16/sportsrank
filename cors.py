import random
import pandas as pd
from records import cfb_records
from teams import cfb_teams
import os

# get original working directory
os.chdir("/Users/aryak/PycharmProjects/sportsrank")
owd = os.getcwd()

# constants
BASE_CORS = 1000
YEAR = 2022
WEEK = 1
DIVISION = "fbs"

cors_team = cfb_records

for index, row in cors_team.iterrows():
    wins = row["wins"]
    losses = row["losses"]
    cors = (BASE_CORS + (wins * 50) - (losses * 25))
    cors_team.loc[index, "cors"] = cors

cors_team = cors_team.drop(columns=["wins", "losses"])
cors_team = cors_team.sort_values(by=["cors"], ascending=False, ignore_index=True)

cors_html = cors_team.to_html()

os.chdir("rankings")
os.chdir(f"{YEAR}_rankings")
with open(f"{YEAR}_W{WEEK}_{DIVISION}_cors.html", "w") as f:
    f.write(cors_html)
os.chdir(owd)
