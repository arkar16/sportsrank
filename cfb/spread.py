# import random
import pandas as pd
import os
from games import get_week_slate
import config


def spread_calc(row, hfa):
    HFA = hfa
    home_team = row["home_team"]
    if row["neutral_site"]:  # if the game is at a neutral site
        spread_val = round((row["home_cors"] - row["away_cors"]) * 2) / 2
        if spread_val > 0:
            return f"{home_team} -{spread_val}"
        else:
            return f"{home_team} +{abs(spread_val)}"
    else:
        spread_val = round(((row["home_cors"] + HFA) - row["away_cors"]) * 2) / 2
        if spread_val > 0:
            return f"{home_team} -{spread_val}"
        else:
            return f"{home_team} +{abs(spread_val)}"


def weekly_spread(year, week, division, week_cors, hfa, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # constants
    WEEK = week + 1
    YEAR = year
    DIVISION = division
    HFA = hfa
    try:
        cors_team = week_cors
        week_slate = get_week_slate(YEAR, WEEK, DIVISION, timestamp)
        cors_ratings = cors_team[["school", "cors"]].set_index("school").squeeze()

        cors_week_df = (
            week_slate
            .merge(cors_ratings.rename("home_cors"), left_on="home_team", right_index=True)
            .merge(cors_ratings.rename("away_cors"), left_on="away_team", right_index=True)
        )

        cors_week_df["spread"] = cors_week_df.apply(spread_calc, axis=1, hfa=HFA)

        cors_week_df_clean = cors_week_df.drop(columns=["home_division", "away_division"])
        cors_week_html = cors_week_df_clean.to_html(index=False)
        title_html = "<html>\n"
        title_html += "<head>\n"
        title_html += f"<title>CORS {config.cors_version} - {YEAR} W{week+1} Spread - {DIVISION} {sport_upper}</title>\n"
        title_html += "</head>\n"
        title_html += "<body>\n"
        title_html += f"<h1>CORS {config.cors_version} - {YEAR} W{week+1} Spread - {DIVISION} {sport_upper}</h1>\n"
        title_html += "</body>\n"
        title_html += "</html>\n"
        timestamp = f"Last updated: {timestamp}<hr>\n" 
        os.chdir(f"{YEAR}/spread")
        with open(f"{YEAR}_W{WEEK}_{DIVISION}_spread.html", "w") as f:
            f.write(title_html)
            f.write(timestamp)
            f.write(cors_week_html)
        os.chdir(config.owd)
        print("week spread done")
    except:
        print(f"no FBS games this week")
