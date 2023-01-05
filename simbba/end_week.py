import cfbd
import config
from api import api_key
import os
import pandas as pd


def get_end_week(year):
    # get original working directory
    os.chdir("/Users/aryak/Projects/sportsrank/cfb/years")
    owd = os.getcwd() # config.owd

    # Configure API key authorization: ApiKeyAuth
    configuration = cfbd.Configuration()
    configuration.api_key["Authorization"] = api_key
    configuration.api_key_prefix["Authorization"] = "Bearer"
    games_api_instance = cfbd.GamesApi(cfbd.ApiClient(configuration))

    season_weeks = games_api_instance.get_calendar(year=year)
    data = [{"week": week.week, "season_type": week.season_type} for week in season_weeks]
    season_weeks_df = pd.DataFrame(data)
    season_weeks_df = season_weeks_df.sort_values(by="week", ascending=False)
    end_week = season_weeks_df.iloc[0]["week"] # takes highest week as end week
    if end_week > 16:
        end_week = 16
    else:
        end_week = end_week
    
    return end_week