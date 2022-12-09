import random
from teams import cfb_teams

def calculate_ranking(wins, losses):
    """
    Calculates a college football team's ranking based on their wins, losses, and ties.

    Parameters:
    wins (int): Number of wins
    losses (int): Number of losses

    Returns:
    int: Team's ranking
    """
    # Base ranking is 1000
    ranking = 1000

    # Add 500 points for every win
    ranking += (wins * 500)

    # Subtract 250 points for every loss
    ranking -= (losses * 250)

    return ranking


# Create a dictionary to store the teams and their rankings
rankings = {}

# Loop through the 130 teams
for team in cfb_teams.itertuples('col'):
    # Generate random number of wins and losses
    games = 12
    wins = random.randint(0, games)
    losses = games - wins
    # losses = random.randint(0, games_remaining)

    # Calculate the team's ranking
    ranking = calculate_ranking(wins, losses)

    # Add the team and ranking and record to the dictionary
    rankings[team] = ranking, wins, losses

# Sort the rankings
sorted_rankings = sorted(rankings.items(), key=lambda x: x[1], reverse=True)

# Print out the sorted rankings
print(sorted_rankings)
