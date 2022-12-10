import cfbd
from api import api_key
import pandas as pd

# Configure API key authorization: ApiKeyAuth
configuration = cfbd.Configuration()
configuration.api_key['Authorization'] = api_key
configuration.api_key_prefix['Authorization'] = 'Bearer'
teams_api_instance = cfbd.TeamsApi(cfbd.ApiClient(configuration))

# CONSTANTS
YEAR = 2022
DIVISION = 'fbs'
BASE_ELO = 1000
BASE_CORS = 0

fbs_teams = teams_api_instance.get_fbs_teams(year=YEAR)
cfb_teams = pd.DataFrame(columns=['school', 'conference'])

# Loop through the FBS teams to create teams list
for team in fbs_teams:
    school = team.school
    conf = team.conference
    division = team.division

    # Add the data to the dataframe
    cfb_teams = pd.concat([cfb_teams, pd.DataFrame({'school': school, 'conference': conf, 'elo': BASE_ELO, 'cors': BASE_CORS}, index=[0])], ignore_index=True)

nfl_teams = {
    "Test1": 1000,
    "Test2": 1000,
    "49ers": 1590,
    "Bears": 1412,
    "Bengals": 1585,
    "Bills": 1672,
    "Broncos": 1384,
    "Browns": 1458,
    "Buccaneers": 1567,
    "Cardinals": 1488,
    "Chargers": 1470,
    "Chiefs": 1703,
    "Colts": 1481,
    "Commanders": 1502,
    "Cowboys": 1638,
    "Dolphins": 1555,
    "Eagles": 1593,
    "Falcons": 1450,
    "Giants": 1435,
    "Jaguars": 1378,
    "Jets": 1473,
    "Lions": 1431,
    "Packers": 1472,
    "Panthers": 1406,
    "Patriots": 1555,
    "Raiders": 1437,
    "Rams": 1458,
    "Ravens": 1593,
    "Saints": 1466,
    "Seahawks": 1555,
    "Steelers": 1444,
    "Texans": 1323,
    "Titans": 1616,
    "Vikings": 1570
}


