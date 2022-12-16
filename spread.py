import random
import pandas as pd
from cors import cors_team
from games import fbs_games
import os

# get original working directory
os.chdir("/Users/aryak/PycharmProjects/sportsrank")
owd = os.getcwd()

# constants
WEEK = 1
YEAR = 2022
DIVISION = "fbs"

#def calc_for_week(week_id, week_df):

cors_ratings = cors_team[['school', 'cors']].set_index('school').squeeze()
week_slate = fbs_games[fbs_games["week"] == WEEK]

cors_week_df = (
    week_slate
    .merge(cors_ratings.rename('home_elo'), left_on='home_team', right_index=True)
    .merge(cors_ratings.rename('away_elo'), left_on='away_team', right_index=True)
)

cors_week_df['spread'] = cors_week_df['home_elo'] - cors_week_df['away_elo']
print(cors_week_df)

cors_week_html = cors_week_df.to_html()
os.chdir("spread")
os.chdir(f"{YEAR}_spread")
with open(f"{YEAR}_W{WEEK}_{DIVISION}_spread.html", "w") as f:
    f.write(cors_week_html)
os.chdir(owd)






