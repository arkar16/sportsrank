import cfbd
from api import api_key
import pandas as pd

# Configure API key authorization: ApiKeyAuth
configuration = cfbd.Configuration()
configuration.api_key['Authorization'] = api_key
configuration.api_key_prefix['Authorization'] = 'Bearer'
games_api_instance = cfbd.GamesApi(cfbd.ApiClient(configuration))

# CONSTANTS
YEAR = 2022
DIVISION = 'fbs'

games = games_api_instance.get_games(year=YEAR, division=DIVISION)
fbs_games = pd.DataFrame(columns=['week', 'home_team', 'home_score', 'away_team', 'away_score', 'neutral_site'])

for game in games:
    week = game.week
    home = game.home_team
    h_score = game.home_points
    away = game.away_team
    a_score = game.away_points
    neutral = game.neutral_site

    # add games to dataframe
    fbs_games = pd.concat([fbs_games, pd.DataFrame({'week': week, 'home_team': home, 'home_score': h_score, 'away_score': a_score, 'neutral_site': neutral}, index=[0])], ignore_index=True)
    fbs_games['neutral_site'] = fbs_games['neutral_site'].astype(bool)
    
print(fbs_games)

