import cfbd
from api import api_key
import pandas as pd
import os

# get original working directory
os.chdir("/Users/aryak/PycharmProjects/sportsrank")
owd = os.getcwd()

# Configure API key authorization: ApiKeyAuth
configuration = cfbd.Configuration()
configuration.api_key["Authorization"] = api_key
configuration.api_key_prefix["Authorization"] = "Bearer"
records_api_instance = cfbd.GamesApi(cfbd.ApiClient(configuration))

# CONSTANTS
YEAR = 2022
DIVISION = "fbs"

team_records = records_api_instance.get_team_records(year=YEAR)
cfb_records = pd.DataFrame(columns=["school", "conference", "wins", "losses", "record"])

for team in team_records:
    school = team.team
    conf = team.conference
    total_record = team.total
    wins = total_record.wins
    losses = total_record.losses
    record = str(wins) + "-" + str(losses)
    win_pct = round(wins / (wins + losses), 2)

    # Add the data to the dataframe
    cfb_records = pd.concat(
        [cfb_records, pd.DataFrame(
            {"school": school, "conference": conf, "wins": wins, "losses": losses,
             "record": record, "win_pct": win_pct}, index=[0])], ignore_index=True
    )

cfb_records = cfb_records.sort_values(by=["win_pct"], ascending=False, ignore_index=True)

# write dataframe to html
records_html = cfb_records.to_html()
os.chdir("data")
os.chdir(f"{YEAR}_data")
with open(f"{YEAR}_{DIVISION}_records.html", "w") as f:
    f.write(records_html)
os.chdir(owd)
