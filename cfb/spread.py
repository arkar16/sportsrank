import random
import pandas as pd
import os
from games import get_week_slate


def spread_calc(row, hfa):
    HFA = hfa
    if row["neutral_site"]:  # if the game is at a neutral site
        return round((row['home_cors'] - row['away_cors']) * 2) / 2
    else:
        return round(((row['home_cors'] + HFA) - row['away_cors']) * 2) / 2


def weekly_spread(year, week, division, week_cors, hfa):
    # get original working directory
    os.chdir("/Users/aryak/Projects/sportsrank/cfb/years")
    owd = os.getcwd()

    # constants
    WEEK = week + 1
    YEAR = year
    DIVISION = division
    HFA = hfa

    cors_team = week_cors
    week_slate = get_week_slate(YEAR, WEEK, DIVISION)
    cors_ratings = cors_team[['school', 'cors']].set_index('school').squeeze()

    cors_week_df = (
        week_slate
        .merge(cors_ratings.rename('home_cors'), left_on='home_team', right_index=True)
        .merge(cors_ratings.rename('away_cors'), left_on='away_team', right_index=True)
    )

    cors_week_df['spread'] = cors_week_df.apply(spread_calc, axis=1, hfa=HFA)

    cors_week_df_clean = cors_week_df.drop(columns=["home_division", "away_division"])
    cors_week_html = cors_week_df_clean.to_html(index=False)
    os.chdir(f"{YEAR}/spread")
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_spread.html", "w") as f:
        f.write(cors_week_html)
    os.chdir(owd)
