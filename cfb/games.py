import cfbd
from api import api_key
import pandas as pd
import os


def get_results(year, division):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb")
    owd = os.getcwd()

    # Configure API key authorization: ApiKeyAuth
    configuration = cfbd.Configuration()
    configuration.api_key["Authorization"] = api_key
    configuration.api_key_prefix["Authorization"] = "Bearer"
    games_api_instance = cfbd.GamesApi(cfbd.ApiClient(configuration))

    # CONSTANTS
    YEAR = year
    DIVISION = division

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
    os.chdir(f"{YEAR}_data/results")
    with open(f"{YEAR}_{DIVISION}_results.html", "w") as f:
        f.write(games_html)
    os.chdir(owd)

    return fbs_games


def get_week_slate(year, week, division):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb")
    owd = os.getcwd()

    # Configure API key authorization: ApiKeyAuth
    configuration = cfbd.Configuration()
    configuration.api_key["Authorization"] = api_key
    configuration.api_key_prefix["Authorization"] = "Bearer"
    games_api_instance = cfbd.GamesApi(cfbd.ApiClient(configuration))

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    week_games = games_api_instance.get_games(year=YEAR, week=WEEK, division=DIVISION)
    fbs_week_slate = pd.DataFrame(
        columns=["week", "home_team", "home_division", "away_team",
                 "away_division", "neutral_site"]
    )

    for game in week_games:
        week = game.week
        home = game.home_team
        h_division = game.home_division
        away = game.away_team
        a_division = game.away_division
        neutral = game.neutral_site

        # add week_games to dataframe
        fbs_week_slate = pd.concat(
            [fbs_week_slate, pd.DataFrame(
                {"week": week, "home_team": home, "home_division": h_division, "away_team": away,
                 "away_division": a_division, "neutral_site": neutral}, index=[0])],
            ignore_index=True
        )
        fbs_week_slate["neutral_site"] = fbs_week_slate["neutral_site"].astype(bool)

    fbs_week_slate = fbs_week_slate
    # convert week_games to html
    games_html = fbs_week_slate.to_html()

    # write week_games to html file for viewing
    os.chdir("data")
    os.chdir(f"{YEAR}_data/slate/weekly_slate")
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_slate.html", "w") as f:
        f.write(games_html)
    os.chdir(owd)
    return fbs_week_slate


def get_slate(year, division):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb")
    owd = os.getcwd()

    # Configure API key authorization: ApiKeyAuth
    configuration = cfbd.Configuration()
    configuration.api_key["Authorization"] = api_key
    configuration.api_key_prefix["Authorization"] = "Bearer"
    games_api_instance = cfbd.GamesApi(cfbd.ApiClient(configuration))

    # CONSTANTS
    YEAR = year
    DIVISION = division

    games = games_api_instance.get_games(year=YEAR, division=DIVISION)
    fbs_slate = pd.DataFrame(
        columns=["week", "home_team", "home_division", "away_team",
                 "away_division", "neutral_site"]
    )

    for game in games:
        week = game.week
        home = game.home_team
        h_division = game.home_division
        away = game.away_team
        a_division = game.away_division
        neutral = game.neutral_site

        # add games to dataframe
        fbs_slate = pd.concat(
            [fbs_slate, pd.DataFrame(
                {"week": week, "home_team": home, "home_division": h_division, "away_team": away,
                 "away_division": a_division, "neutral_site": neutral}, index=[0])],
            ignore_index=True
        )
        fbs_slate["neutral_site"] = fbs_slate["neutral_site"].astype(bool)

    fbs_slate = fbs_slate
    # convert games to html
    games_html = fbs_slate.to_html()

    # write games to html file for viewing
    os.chdir("data")
    os.chdir(f"{YEAR}_data/slate")
    with open(f"{YEAR}_{DIVISION}_slate.html", "w") as f:
        f.write(games_html)
    os.chdir(owd)
    return fbs_slate

