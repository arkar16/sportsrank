import random
import pandas as pd
import os
from games import get_week_slate


def weekly_spread(year, week, division, week_cors):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb/years")
    owd = os.getcwd()

    # constants
    WEEK = week + 1
    YEAR = year
    DIVISION = division

    cors_team = week_cors
    week_slate = get_week_slate(YEAR, WEEK, DIVISION)
    cors_ratings = cors_team[['school', 'cors']].set_index('school').squeeze()

    cors_week_df = (
        week_slate
        .merge(cors_ratings.rename('home_cors'), left_on='home_team', right_index=True)
        .merge(cors_ratings.rename('away_cors'), left_on='away_team', right_index=True)
    )

    cors_week_df['spread'] = round((cors_week_df['home_cors'] - cors_week_df['away_cors'])*2) / 2

    cors_week_df_clean = cors_week_df.drop(columns=["home_division", "away_division"])
    cors_week_html = cors_week_df_clean.to_html(index=False)
    os.chdir(f"{YEAR}/spread")
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_spread.html", "w") as f:
        f.write(cors_week_html)
    os.chdir(owd)

