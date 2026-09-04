import pandas as pd
import os
import config
from cfbd_client import get_snapshot_service


def fetch_games(
    year, division, week=None, snapshot_service=None, refresh_games=False,
    required_week=None
):
    service = snapshot_service or get_snapshot_service()
    snapshot = service.get(
        year,
        division,
        refresh_games=refresh_games,
        required_week=required_week,
    )
    games = snapshot.games
    if week is not None:
        games = tuple(game for game in games if game.week == week)
    return list(games)


def _results_dataframe(games):
    frame = pd.DataFrame(
        (
            {
                "week": game.week,
                "home_team": game.home_team,
                "home_division": game.home_classification,
                "home_score": game.home_points,
                "away_team": game.away_team,
                "away_division": game.away_classification,
                "away_score": game.away_points,
                "neutral_site": game.neutral_site,
            }
            for game in games
        ),
        columns=["week", "home_team", "home_division", "home_score", "away_team",
                 "away_division", "away_score", "neutral_site"],
    )
    frame["neutral_site"] = frame["neutral_site"].astype(bool)
    return frame


def _slate_dataframe(games):
    frame = pd.DataFrame(
        (
            {
                "week": game.week,
                "home_team": game.home_team,
                "home_division": game.home_classification,
                "away_team": game.away_team,
                "away_division": game.away_classification,
                "neutral_site": game.neutral_site,
            }
            for game in games
        ),
        columns=["week", "home_team", "home_division", "away_team",
                 "away_division", "neutral_site"],
    )
    frame["neutral_site"] = frame["neutral_site"].astype(bool)
    return frame

def get_weekly_results(year, week, division, timestamp, snapshot_service=None):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    games = fetch_games(YEAR, DIVISION, WEEK, snapshot_service)
    fbs_week_results = _results_dataframe(games)
    # convert games to html
    week_results_html = fbs_week_results.to_html(index=False)

    # write games to html file for viewing
    os.chdir(f"{YEAR}/data/results/weekly_results")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} W{WEEK} Results - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} W{WEEK} Results - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_results.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(week_results_html)
    os.chdir(config.owd)
    #print("weekly results done")

    return fbs_week_results

def get_results(year, division, timestamp, snapshot_service=None):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # CONSTANTS
    YEAR = year
    DIVISION = division

    games = fetch_games(YEAR, DIVISION, snapshot_service=snapshot_service)
    fbs_results = _results_dataframe(games)

    # convert games to html
    results_html = fbs_results.to_html(index=False)

    # write games to html file for viewing
    os.chdir(f"{YEAR}/data/results")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} Results - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} Results - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_{DIVISION}_results.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(results_html)
    os.chdir(config.owd)
    #print("results done")
    return fbs_results


def get_week_slate(year, week, division, timestamp, snapshot_service=None):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    # os.chdir(f"{YEAR}_data/slate")

    week_games = fetch_games(YEAR, DIVISION, WEEK, snapshot_service)
    fbs_week_slate = _slate_dataframe(week_games)
    # convert week_games to html
    games_html = fbs_week_slate.to_html(index=False)

    # write week_games to html file for viewing
    os.chdir(f"{YEAR}/data/slate/weekly_slate")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} W{WEEK} Slate - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} W{WEEK} Slate - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_slate.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(games_html)
    os.chdir(config.owd)
    #print("week games done")
    return fbs_week_slate


def get_slate(year, division, timestamp, snapshot_service=None):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # CONSTANTS
    YEAR = year
    DIVISION = division

    games = fetch_games(YEAR, DIVISION, snapshot_service=snapshot_service)
    fbs_slate = _slate_dataframe(games)
    # convert games to html
    games_html = fbs_slate.to_html(index=False)

    # write games to html file for viewing
    os.chdir(f"{YEAR}/data/slate")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} Slate - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} Slate - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_{DIVISION}_slate.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(games_html)
    os.chdir(config.owd)
    #print("slate done")
    return fbs_slate
