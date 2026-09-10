import pandas as pd
import os
import config
from cfbd_client import get_snapshot_service


def fetch_fbs_teams(year, division="FBS", snapshot_service=None):
    service = snapshot_service or get_snapshot_service()
    return list(service.get(year, division).teams)

def get_teams(year, division, timestamp, snapshot_service=None):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # CONSTANTS
    YEAR = year
    DIVISION = division

    fbs_teams = fetch_fbs_teams(YEAR, DIVISION, snapshot_service)
    cfb_teams = pd.DataFrame(
        ({"school": team.school, "conference": team.conference} for team in fbs_teams),
        columns=["school", "conference"],
    )

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
