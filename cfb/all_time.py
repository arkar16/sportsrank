import os
import config

def nc_to_history(year, division, final_cors_df): # national champ to history
    os.chdir(f"{config.owd}/history")
    national_champ_df = final_cors_df.head(n=1)
    national_champ_df.insert(0, 'year', year)
    division = division.upper()
    sport_upper = config.sport.upper()

    if year == 1897: # first year
        nc_html = national_champ_df.to_html(escape=False, index=False, header=False)
        with open(f"CORS_NC_{division}_{sport_upper}.html", "w") as f:
            f.write(nc_html)
        os.chdir(config.owd)
    else:
        nc_html = national_champ_df.to_html(escape=False, index=False, header=False)
        with open(f"CORS_NC_{division}_{sport_upper}.html", "a") as f:
            f.write(nc_html)
        os.chdir(config.owd)

def worst_to_history(year, division, final_cors_df): # worst team to history
    os.chdir(f"{config.owd}/history")
    worst_team_df = final_cors_df.tail(n=1) # FIXME 2020 issue (needs to have played games)
    worst_team_df.insert(0, 'year', year)
    division = division.upper()
    sport_upper = config.sport.upper()

    if year == 1897:
        wt_html = worst_team_df.to_html(escape=False, header=False)
        with open(f"CORS_WT_{division}_{sport_upper}.html", "w") as f:
            f.write(wt_html)
        os.chdir(config.owd)
    else:
        wt_html = worst_team_df.to_html(escape=False, header=False)
        with open(f"CORS_WT_{division}_{sport_upper}.html", "a") as f:
            f.write(wt_html)
        os.chdir(config.owd)