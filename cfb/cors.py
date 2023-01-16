import random
import pandas as pd
import os
import config
from all_time import *


def margin_of_victory(team, week, results):
    # get original working directory
    os.chdir(config.owd)

    # constants
    TEAM = team
    WEEK = week

    results_df = results
    team_margin = 0
    games = 0
    net_mov = None

    current_results = results_df[results_df["week"] < WEEK + 1]  # a df of games up until the current week
    for index, row in current_results.iterrows():
        if row["home_team"] == TEAM and row["home_score"] is not None and row["away_score"] is not None:
            games += 1
            margin = row["home_score"] - row["away_score"]
            team_margin += margin
        elif row["away_team"] == TEAM and row["home_score"] is not None and row["away_score"] is not None:
            games += 1
            margin = row["away_score"] - row["home_score"]
            team_margin += margin
        if games > 0:
            net_mov = team_margin / games
        else:
            net_mov = 0

    return net_mov


def current_sos(team, week, last_cors, results):
    # get original working directory
    os.chdir(config.owd)

    # constants
    TEAM = team
    WEEK = week

    results_df = results
    last_cors_df = last_cors.drop(columns=["logo", "rank", "conference", "record", "win_pct"])
    sos = 0
    teams_played = 0

    current_results = results_df[results_df["week"] < WEEK + 1]  # a df of games up until the current week
    for index, row in current_results.iterrows():
        if row["home_team"] == TEAM and row["home_score"] is not None and row["away_score"] is not None:
            try:
                opp_cors = last_cors_df.loc[row["away_team"], "cors"]
            except KeyError:
                opp_cors = -10 # fcs constant
            sos += opp_cors
            teams_played += 1
        elif row["away_team"] == TEAM and row["home_score"] is not None and row["away_score"] is not None:
            try:
                opp_cors = last_cors_df.loc[row["home_team"], "cors"]
            except KeyError:
                opp_cors = -10 # fcs constant
            sos += opp_cors
            teams_played += 1
    try:
        avg_sos = round((sos / teams_played), 2)
    except:
        avg_sos = 0.0
    #print(f"{TEAM} = SOS {avg_sos}")
    return avg_sos


def cors_calc(team, week, base, wins, losses, results, last_week_cors):
    # get original working directory
    os.chdir(config.owd)

    # constants
    TEAM = team
    WEEK = week
    BASE_CORS = base
    wins = int(wins)
    losses = int(losses)
    try:
        win_pct = round((wins / (wins + losses)), 2)
    except:
        win_pct = 0.0

    results_df = results
    last_cors = last_week_cors

    win_pct_m = round(((win_pct * 100) / 2), 2)
    net_mov = round((margin_of_victory(TEAM, WEEK, results_df) / 4), 2)
    sos = round((current_sos(TEAM, WEEK, last_cors, results_df) / 3), 2)

    cors = win_pct_m + net_mov + sos

    return cors


def weekly_cors(base, year, week, end_week, division, current_records, results, weekly_results, timestamp):
    # get original working directory
    os.chdir(config.owd)

    # constants
    BASE_CORS = base
    YEAR = year
    WEEK = week
    END_WEEK = end_week
    DIVISION = division
    sport_upper = config.sport.upper()

    # last week CORS
    os.chdir(f"{YEAR}/rankings")
    last_week_cors = pd.read_html(f"{YEAR}_W{WEEK - 1}_{DIVISION}_cors.html")[0].set_index("school")
    cors_teams_df = current_records.copy()
    results_df = results
    # weekly_results_df = weekly_results / don't think this is needed no more (replaced with cors_calc)

    for index, row in cors_teams_df.iterrows():
        wins = row["wins"]
        losses = row["losses"]
        team = row["school"]
        cors = cors_calc(team, WEEK, BASE_CORS, wins, losses, results_df, last_week_cors)
        cors_teams_df.loc[index, "cors"] = cors

    cors_teams_df = cors_teams_df.sort_values(by=["cors", "wins", "losses"], ascending=[False, False, True],
                                              ignore_index=True)
    cors_teams_df = cors_teams_df.drop(columns=["wins", "losses", "ties"])
    cors_teams_df.index = range(1, cors_teams_df.shape[0] + 1)
    cors_teams_df.columns.name = "rank"

    DIVISION = DIVISION.upper()

    # set escape tag false to prevent HTML code passthrough as plain text
    if WEEK == END_WEEK:
        title_html = "<html>\n"
        title_html += "<head>\n"
        title_html += f"<title>CORS {config.cors_version} - {YEAR} Final Rankings - {DIVISION} {sport_upper}</title>\n"
        title_html += "</head>\n"
        title_html += "<body>\n"
        title_html += f"<h1>CORS {config.cors_version} - {YEAR} Final Rankings - {DIVISION} {sport_upper}</h1>\n"
        title_html += "</body>\n"
        title_html += "</html>\n"
        timestamp = f"Last updated: {timestamp}<hr>\n" 
        cors_html = cors_teams_df.to_html(escape=False)
        os.chdir(f"{YEAR}/rankings")
        with open(f"{YEAR}_FINAL_{DIVISION}_cors.html", "w") as f:
            f.write(title_html)
            f.write(timestamp)
            f.write(cors_html)
            f.close()
        os.chdir(config.owd)
        nc_to_history(year, division, cors_teams_df)
        worst_to_history(year, division, cors_teams_df)
    else:
        title_html = "<html>\n"
        title_html += "<head>\n"
        title_html += f"<title>CORS {config.cors_version} - {YEAR} W{WEEK} Rankings - {DIVISION} {sport_upper}</title>\n"
        title_html += "</head>\n"
        title_html += "<body>\n"
        title_html += f"<h1>CORS {config.cors_version} - {YEAR} W{WEEK} Rankings - {DIVISION} {sport_upper}</h1>\n"
        title_html += "</body>\n"
        title_html += "</html>\n"
        timestamp = f"Last updated: {timestamp}<hr>\n" 
        cors_html = cors_teams_df.to_html(escape=False)
        os.chdir(f"{YEAR}/rankings")
        with open(f"{YEAR}_W{WEEK}_{DIVISION}_cors.html", "w") as f:
            f.write(title_html)
            f.write(timestamp)
            f.write(cors_html)
            f.close()
        os.chdir(config.owd)

    return cors_teams_df
