import cfbd
from api import api_key
import pandas as pd
import os

# get original working directory
os.chdir("/Users/aryak/PycharmProjects/sportsrank")
owd = os.getcwd()

# Configure API key authorization: ApiKeyAuth
configuration = cfbd.Configuration()
configuration.api_key["Authorization"] = api_key
configuration.api_key_prefix["Authorization"] = "Bearer"
games_api_instance = cfbd.GamesApi(cfbd.ApiClient(configuration))

# CONSTANTS
YEAR = 2022
DIVISION = "fbs"

games = games_api_instance.get_games(year=YEAR, division=DIVISION)
fbs_games = pd.DataFrame(
    columns=["week", "home_team", "home_division", "home_score", "away_team",
             "away_division", "away_score", "neutral_site"]
)

for game in games:
    week = game.week
    home = game.home_team
    h_score = game.home_points
    h_division = game.home_division
    away = game.away_team
    a_score = game.away_points
    a_division = game.away_division
    neutral = game.neutral_site

    # add games to dataframe
    fbs_games = pd.concat(
        [fbs_games, pd.DataFrame(
            {"week": week, "home_team": home, "home_division": h_division, "home_score": h_score, "away_team": away,
             "away_division": a_division, "away_score": a_score, "neutral_site": neutral}, index=[0])],
        ignore_index=True
    )
    fbs_games["neutral_site"] = fbs_games["neutral_site"].astype(bool)

fbs_games = fbs_games
# convert games to html
games_html = fbs_games.to_html()

# write games to html file for viewing
os.chdir("data")
os.chdir(f"{YEAR}_data")
with open(f"{YEAR}_{DIVISION}_results.html", "w") as f:
    f.write(games_html)
