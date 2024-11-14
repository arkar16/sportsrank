import os
from teams import get_teams
import pandas as pd
from games import get_results
import config


def get_current_records(year, week, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    teams_df = get_teams(YEAR, DIVISION, timestamp)
    results_df = get_results(YEAR, DIVISION, timestamp)

    cfb_records_df = teams_df.copy()
    cfb_records_df["wins"] = 0
    cfb_records_df["losses"] = 0
    cfb_records_df["ties"] = 0
    cfb_records_df["record"] = ""
    cfb_records_df["win_pct"] = 0

    current_record_games_df = results_df[results_df["week"] < WEEK + 1]  # a df of games up until the current week

    for index, row in current_record_games_df.iterrows():
        if row["home_score"] is not None and row["away_score"] is not None:
            home_team = cfb_records_df["school"] == row["home_team"]
            away_team = cfb_records_df["school"] == row["away_team"]
            if row["home_score"] > row["away_score"]:
                cfb_records_df.loc[home_team, "wins"] += 1
                cfb_records_df.loc[away_team, "losses"] += 1
            elif row["home_score"] < row["away_score"]:
                cfb_records_df.loc[away_team, "wins"] += 1
                cfb_records_df.loc[home_team, "losses"] += 1
            else:
                cfb_records_df.loc[away_team, "ties"] += 1
                cfb_records_df.loc[home_team, "ties"] += 1
            if YEAR < 1996:
                cfb_records_df["record"] = cfb_records_df["wins"].astype(str) + "-" + cfb_records_df["losses"].astype(str) + "-" + cfb_records_df["ties"].astype(str)
                cfb_records_df["win_pct"] = round(
                    cfb_records_df["wins"] / (cfb_records_df["wins"] + cfb_records_df["losses"] + cfb_records_df["ties"]), 2)
            else:
                cfb_records_df["record"] = cfb_records_df["wins"].astype(str) + "-" + cfb_records_df["losses"].astype(str)
                cfb_records_df["win_pct"] = round(
                    cfb_records_df["wins"] / (cfb_records_df["wins"] + cfb_records_df["losses"]), 2)

    cfb_records_df = cfb_records_df.sort_values(by=["win_pct", "wins"], ascending=[False, False], ignore_index=True)

    # write dataframe to html
    cfb_presentable_records_df = cfb_records_df.drop(columns=["wins", "losses", "ties"])
    records_html = cfb_presentable_records_df.to_html(justify="center", escape=False, index=False)
    os.chdir(f"{YEAR}/data/records")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} W{WEEK} Records - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} W{WEEK} Records - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n"  
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_records.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(records_html)
    os.chdir(config.owd)
    print("records done")
    return cfb_records_df
