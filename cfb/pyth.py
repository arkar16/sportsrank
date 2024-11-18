import os
import config
from games import get_results
from teams import get_teams
import pandas as pd

def pythagorean_exp(year, division, timestamp):
    """
    Calculate the Pythagorean expectation (expected wins) for each team
    based on points scored and points allowed over the season.
    
    Args:
        year: The year to calculate for
        division: The division (e.g. 'FBS')
        timestamp: Timestamp for file generation
    
    Returns:
        DataFrame with columns: school, expected_wins, wins_vs_expected
    """
    # Get all games for the season
    results = get_results(year, division, timestamp)
    
    # Get list of teams and their records from final rankings
    os.chdir(config.owd)
    final_rankings = pd.read_html(f"{year}/rankings/{year}_FINAL_{division}_cors.html")[0]
    
    # Initialize DataFrame with team stats
    team_stats = pd.DataFrame({
        'school': final_rankings['school'],
        'actual_wins': final_rankings['record'].str.split('-').str[0].astype(int),
        'points_for': 0,
        'points_against': 0,
        'games': 0
    })
    team_stats = team_stats.set_index('school')
    
    # Calculate points for/against for each team
    for _, game in results.iterrows():
        if pd.isna(game['home_score']) or pd.isna(game['away_score']):
            continue
            
        home_team = game['home_team']
        away_team = game['away_team']
        home_score = game['home_score']
        away_score = game['away_score']
        
        # Only process if both teams are in our teams list
        if home_team in team_stats.index and away_team in team_stats.index:
            # Update home team stats
            team_stats.loc[home_team, 'points_for'] += home_score
            team_stats.loc[home_team, 'points_against'] += away_score
            team_stats.loc[home_team, 'games'] += 1
            
            # Update away team stats
            team_stats.loc[away_team, 'points_for'] += away_score
            team_stats.loc[away_team, 'points_against'] += home_score
            team_stats.loc[away_team, 'games'] += 1
    
    # Calculate pythagorean expectation for each team
    pyth_exp_data = []
    for team in team_stats.index:
        pf = team_stats.loc[team, 'points_for']
        pa = team_stats.loc[team, 'points_against']
        games = team_stats.loc[team, 'games']
        actual_wins = team_stats.loc[team, 'actual_wins']
        #print(f"{team} - PF: {pf} PA: {pa} games: {games} actual_wins: {actual_wins}")
        
        # Skip teams with no games
        if games == 0:
            continue
            
        # Calculate pythagorean expectation using 2.37 exponent
        # Handle case where team hasn't allowed any points
        if pa == 0:
            pyth_exp = 1.0 if pf > 0 else 0.0
        else:
            pyth_exp = (pf ** 2.37) / ((pf ** 2.37) + (pa ** 2.37))
            
        expected_wins = pyth_exp * games
        
        pyth_exp_data.append({
            'school': team,
            'expected_wins': round(expected_wins, 2),
            'wins_vs_expected': round(actual_wins - expected_wins, 2)
        })
    
    # Create DataFrame with index starting at 1
    pyth_df = pd.DataFrame(pyth_exp_data)
    pyth_df.index = range(1, len(pyth_df) + 1)
    
    return pyth_df
