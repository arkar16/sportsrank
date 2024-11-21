import os
import config
import pandas as pd

def pythagorean_exp(team, week, results):
    """
    Calculate the Pythagorean expectation (expected wins) for a team
    based on points scored and points allowed up to the given week.
    
    Args:
        team: The team to calculate for
        week: The week number to calculate up to
        results: DataFrame containing all game results
    
    Returns:
        float: Expected number of wins for the team
    """
    # Constants
    TEAM = team
    WEEK = week
    
    # Initialize stats
    points_for = 0
    points_against = 0
    games = 0
    
    # Get games up until current week
    current_results = results[results["week"] < WEEK + 1]
    
    # Calculate points for/against for the team
    for _, game in current_results.iterrows():
        if pd.isna(game['home_score']) or pd.isna(game['away_score']):
            continue
            
        if game['home_team'] == TEAM:
            points_for += game['home_score']
            points_against += game['away_score']
            games += 1
        elif game['away_team'] == TEAM:
            points_for += game['away_score']
            points_against += game['home_score']
            games += 1
    
    # Skip if no games played
    if games == 0:
        return 0.0
        
    # Calculate pythagorean expectation using 2.37 exponent
    if points_against == 0:
        pyth_exp = 1.0 if points_for > 0 else 0.0
    else:
        pyth_exp = (points_for ** 2.37) / ((points_for ** 2.37) + (points_against ** 2.37))
        
    expected_wins = pyth_exp * games
    
    return round(expected_wins, 2)
