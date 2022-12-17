import os
from teams import get_teams
from games import get_results


def get_records(year, week, division):
    # get original working directory
    os.chdir("/Users/aryak/PycharmProjects/sportsrank/cfb")
    owd = os.getcwd()

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    teams_df = get_teams(YEAR, DIVISION)
    results_df = get_results(YEAR, DIVISION)
    cfb_records_df = teams_df

    for index, team_row in teams_df.iterrows():
        team_name = team_row["school"]

        wins = 0
        losses = 0

        for game_index, game_row in results_df.iterrows():
            # Check if the team won or lost
            if game_row['home_team'] == team_name:
                # Skip the comparison if either score is None
                if game_row['home_score'] is not None and game_row['away_score'] is not None:
                    if game_row['home_score'] > game_row['away_score']:
                        wins += 1
                    else:
                        losses += 1
            elif game_row['away_team'] == team_name:
                # Skip the comparison if either score is None
                if game_row['home_score'] is not None and game_row['away_score'] is not None:
                    if game_row['away_score'] > game_row['home_score']:
                        wins += 1
                    else:
                        losses += 1
        cfb_records_df.loc[index, "wins"] = wins
        cfb_records_df.loc[index, "losses"] = losses
        cfb_records_df.loc[index, "record"] = str(wins) + "-" + str(losses)
        cfb_records_df.loc[index, "win_pct"] = round(wins / (wins + losses), 2)

    cfb_records_df = cfb_records_df.sort_values(by=["win_pct"], ascending=False, ignore_index=True)

    # write dataframe to html
    cfb_presentable_records_df = cfb_records_df.drop(columns=["wins", "losses"])
    records_html = cfb_presentable_records_df.to_html(escape=False)
    os.chdir("data")
    os.chdir(f"{YEAR}_data")
    os.chdir("records")
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_records.html", "w") as f:
        f.write(records_html)
    os.chdir(owd)
    return cfb_records_df
