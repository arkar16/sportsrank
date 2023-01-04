import cfbd
from api import api_key
import os


def get_end_week(year):
    # get original working directory
    os.chdir("/Users/aryak/Projects/sportsrank/cfb/years")
    owd = os.getcwd()

    # Configure API key authorization: ApiKeyAuth
    configuration = cfbd.Configuration()
    configuration.api_key["Authorization"] = api_key
    configuration.api_key_prefix["Authorization"] = "Bearer"
    games_api_instance = cfbd.GamesApi(cfbd.ApiClient(configuration))

    # constants
    YEAR = year

    season_weeks = games_api_instance.get_calendar(year=YEAR)
    print(season_weeks)

get_end_week(1919)