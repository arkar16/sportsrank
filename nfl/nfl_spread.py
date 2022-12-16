import random
import nfl_teams


def nfl_spread_prediction(home_team, away_team, venue):
    K_CONSTANT = 3  # score constant adjustment
    HFA = .05  # home field advantage
    SIMULATION_RUNS = int(input("Enter how many simulations to run: "))  # how many matches to run
    LOWER_RAND_CONSTANT = -10  # lower random elo bound
    UPPER_RAND_CONSTANT = 10  # upper random elo bound
    SPORT = "NFL"  # what sport

    simulation_counter = 0  # counter
    spread_counter = 0  # counts up spread

    # print(f"{home_team} vs. {away_team}")
    while simulation_counter < SIMULATION_RUNS:
        home_rat = nfl_teams.nfl_teams[home_team]
        away_rat = nfl_teams.nfl_teams[away_team]
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
        spread_counter += rat_spread  # for debugging reasons
        # if rat_spread >= 0:
        # spread_counter += int(rat_spread)
        # rat_spread = "+" + str(rat_spread)
        # else:
        # rat_spread = rat_spread
        # spread_counter += int(rat_spread)

        # print(f"{home_team} {home_rat} {away_team} {away_rat} {rat_spread}")
        # print(f"{home_team} expected points: {t1_points}")
        # print(f"{away_team} expected points: {t2_points}")
        # if t1_points >= t2_points:
        # print(f"Expected score: {home_team} {t1_points} - {away_team} {t2_points}")
        # else:
        # print(f"Expected score: {away_team} {t2_points} - {home_team} {t1_points}")
        # print(f"Expected score: {home_team} {t1_points} - {away_team} {t2_points}")

        simulation_counter += 1

    avg_spread = round((spread_counter / SIMULATION_RUNS), 1)
    # print(avg_spread)
    if avg_spread > 0.0:
        avg_spread = "+" + str(avg_spread)
    elif avg_spread == 0.0:
        avg_spread = "PUSH"
    print(f"{home_team} vs. {away_team} ({avg_spread})")


nfl_spread_prediction("Patriots", "Bills", "Home")
