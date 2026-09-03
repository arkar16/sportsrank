import cfbd
import pandas as pd
import os
import config
from cfbd_client import create_api_client


def fetch_fbs_teams(year):
    with create_api_client() as api_client:
        return cfbd.TeamsApi(api_client).get_fbs_teams(year=year)

def get_teams(year, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # CONSTANTS
    YEAR = year
    DIVISION = division

    fbs_teams = fetch_fbs_teams(YEAR)
    cfb_teams = pd.DataFrame(columns=["school", "conference"])

    # create dataframe of FBS teams
    for team in fbs_teams:
        #try:
            #logo = team.logos[0]
            #logo_png = f"<img src='{logo}' style='width: 20px; height: 20px;'>"
        #except:
            #logo_png = ""
        school = team.school
        try:
            conf = team.conference
        except:
            conf = "FBS Independents"

        # Add the data to the dataframe
        cfb_teams = pd.concat(
            [cfb_teams, pd.DataFrame({"school": school, "conference": conf}, index=[0])],
            ignore_index=True)

    teams_html = cfb_teams.to_html(index=False, escape=False)
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

    #print("teams done")
    return cfb_teams
