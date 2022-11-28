import random
import teams


def spread_prediction():
    K_CONSTANT = 3  # score constant adjustment
    HFA = .1  # home field advantage
    SIMULATION_RUNS = 5  # how many matches to run
    LOWER_RAND_CONSTANT = -5  # lower random elo bound
    UPPER_RAND_CONSTANT = 5  # upper random elo bound
    SPORT = "NFL" # what sport

    while True:
        home_team = input("Enter Home Team: ")
        if home_team in teams.nfl_teams:
            break
        else:
            print(f"Team is not a {SPORT} team.")

    while True:
        away_team = input("Enter Away Team: ")
        if away_team in teams.nfl_teams:
            break
        else:
            print(f"Team is not a {SPORT} team.")

    while True: # need to fix
        venue = input("Enter Venue (Home or Neutral): ")
        if venue == "Home" or venue == "home" or venue == "Neutral" or venue == "neutral":
            break
        else:
            print("That is not a venue option.")

    simulation_counter = 0  # counter

    print(f"{home_team} vs. {away_team}")
    while simulation_counter < SIMULATION_RUNS:
        home_rat = teams.nfl_teams[home_team]
        away_rat = teams.nfl_teams[away_team]
        HOME_RAND_CONSTANT = random.randint(LOWER_RAND_CONSTANT, UPPER_RAND_CONSTANT)  # home elo random buffer
        AWAY_RAND_CONSTANT = random.randint(LOWER_RAND_CONSTANT, UPPER_RAND_CONSTANT)  # away elo random buffer
        if venue == "Home":
            home_rat = home_rat + ((home_rat * HFA) + HOME_RAND_CONSTANT)
            away_rat = away_rat + AWAY_RAND_CONSTANT
        else:
            home_rat = home_rat + HOME_RAND_CONSTANT
            away_rat = away_rat + AWAY_RAND_CONSTANT
        # t1_points = round((home_rat ** K_CONSTANT) // (away_rat ** K_CONSTANT))
        # t2_points = round((away_rat ** K_CONSTANT) // (home_rat ** K_CONSTANT))
        rat_spread = round((home_rat - away_rat) / 25)
        if rat_spread >= 0:
            rat_spread = '+' + str(rat_spread)
        else:
            rat_spread = rat_spread
        print(f"{home_team} {home_rat} {away_team} {away_rat} {rat_spread}")
        # print(f"{home_team} expected points: {t1_points}")
        # print(f"{away_team} expected points: {t2_points}")
        # if t1_points >= t2_points:
            # print(f"Expected score: {home_team} {t1_points} - {away_team} {t2_points}")
        # else:
            # print(f"Expected score: {away_team} {t2_points} - {home_team} {t1_points}")
        # print(f"Expected score: {home_team} {t1_points} - {away_team} {t2_points}")

        simulation_counter += 1


spread_prediction()
