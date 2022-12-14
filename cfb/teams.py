import cfbd
from api import api_key
import pandas as pd
import os
import config


def get_teams(year, division):
    # get original working directory
    os.chdir(config.owd)

    # Configure API key authorization: ApiKeyAuth
    configuration = cfbd.Configuration()
    configuration.api_key["Authorization"] = api_key
    configuration.api_key_prefix["Authorization"] = "Bearer"
    teams_api_instance = cfbd.TeamsApi(cfbd.ApiClient(configuration))

    # CONSTANTS
    YEAR = year
    DIVISION = division

    fbs_teams = teams_api_instance.get_fbs_teams(year=YEAR)
    cfb_teams = pd.DataFrame(columns=["logo", "school", "conference"])

    # create dataframe of FBS teams
    for team in fbs_teams:
        try:
            logo = team.logos[0]
            logo_png = f"<img src='{logo}' style='width: 20px; height: 20px;'>" # TODO potentially add these as a constant (20x) across filesystem
        except:
            logo_png = ""
        school = team.school
        try:
            conf = team.conference
        except:
            conf = "FBS Independents"

        # Add the data to the dataframe
        cfb_teams = pd.concat(
            [cfb_teams, pd.DataFrame({"logo": logo_png, "school": school, "conference": conf}, index=[0])],
            ignore_index=True)

    teams_html = cfb_teams.to_html(index=False, escape=False)
    os.chdir(f"{YEAR}/data")
    with open(f"{YEAR}_{DIVISION}_teams.html", "w") as f:
        f.write(teams_html)
    os.chdir(config.owd)

    return cfb_teams
