import pandas as pd
import numpy as np
import os
import config


def get_teams(year, division, timestamp):
    sport_upper = config.sport.upper()

    # get data from csv and convert to df
    os.chdir(f"/Users/aryak/Projects/sportsrank/{config.sport}/data/2022")
    cfb_teams = pd.read_csv(f"teams_2022.csv", header=None)
    cfb_teams = cfb_teams.drop(cfb_teams.index[0])
    cfb_teams = cfb_teams.rename(columns={0: "logo", 1: "school", 2: "conference"})
    cfb_teams = cfb_teams.replace(np.nan, "", regex=True)

    # CONSTANTS
    YEAR = year
    DIVISION = division

    # get original working directory
    os.chdir(config.owd)

    teams_html = cfb_teams.to_html(index=False, escape=False)

    # TODO add logos
    
    os.chdir(f"{YEAR}/data")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} Teams - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} Teams - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_{DIVISION}_teams.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(teams_html)
    os.chdir(config.owd)

    return cfb_teams