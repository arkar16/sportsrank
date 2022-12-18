import os
from teams import get_teams
import pandas as pd
from games import get_results


def get_current_records(year, week, division):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb")
    owd = os.getcwd()

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    teams_df = get_teams(YEAR, DIVISION)
    results_df = get_results(YEAR, DIVISION)

    cfb_records_df = teams_df.copy()
    cfb_records_df['wins'] = 0
    cfb_records_df['losses'] = 0
    cfb_records_df['record'] = ''
    cfb_records_df['win_pct'] = 0

    current_record_games_df = results_df[results_df['week'] < WEEK + 1]  # a df of games up until the current week

    for index, row in current_record_games_df.iterrows():
        if row['home_score'] is not None and row['away_score'] is not None:
            home_team = cfb_records_df['school'] == row['home_team']
            away_team = cfb_records_df['school'] == row['away_team']
            if row['home_score'] > row['away_score']:
                cfb_records_df.loc[home_team, 'wins'] += 1
                cfb_records_df.loc[away_team, 'losses'] += 1
            else:
                cfb_records_df.loc[away_team, 'wins'] += 1
                cfb_records_df.loc[home_team, 'losses'] += 1
            cfb_records_df['record'] = cfb_records_df['wins'].astype(str) + "-" + cfb_records_df['losses'].astype(str)
            cfb_records_df['win_pct'] = round(
                cfb_records_df['wins'] / (cfb_records_df['wins'] + cfb_records_df['losses']), 2)

    cfb_records_df = cfb_records_df.sort_values(by=["win_pct", "wins"], ascending=[False, False], ignore_index=True)

    # write dataframe to html
    cfb_presentable_records_df = cfb_records_df.drop(columns=["wins", "losses"])
    records_html = cfb_presentable_records_df.to_html(justify="center", escape=False, index=False)
    os.chdir("data")
    os.chdir(f"{YEAR}_data")
    os.chdir("records")
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_records.html", "w") as f:
        f.write(records_html)
    os.chdir(owd)
    return cfb_records_df
