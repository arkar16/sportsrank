import cfbd
from api import api_key
import pandas as pd
import os
from teams import get_teams

# Configure API key authorization: ApiKeyAuth
configuration = cfbd.Configuration()
configuration.api_key["Authorization"] = api_key
configuration.api_key_prefix["Authorization"] = "Bearer"
teams_api_instance = cfbd.TeamsApi(cfbd.ApiClient(configuration))
players_api_instance = cfbd.PlayersApi(cfbd.ApiClient(configuration))

# CONSTANTS
YEAR = 2022

fbs_talent = teams_api_instance.get_talent(year=YEAR)
fbs_returning_production = players_api_instance.get_returning_production(year=YEAR)
