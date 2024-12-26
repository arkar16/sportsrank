import pandas as pd
import numpy as np
import os
import config


def week_zero_readjust(year, division, teams, week_zero_file, timestamp):
    sport_upper = config.sport.upper()
    os.chdir(config.owd)
    os.chdir(f"{year}/rankings/")
    division = division.upper()

    missing_teams = teams[~teams["school"].isin(week_zero_file["school"])]
    missing_teams_copy = missing_teams.copy()
    missing_teams_copy["record"] = "0-0"
    missing_teams_copy["win_pct"] = ""
    missing_teams_copy["cors"] = config.fcs_constant 

    week_zero_file.loc[:, "record"] = "0-0"
    week_zero_file.loc[:, "win_pct"] = 0
    week_zero_file.loc[:, "mov"] = 0
    week_zero_file.loc[:, "sos"] = 0

    # Regress CORS values based on wins vs expected
    # TODO need to make this a real regression model
    try:
        regression_factor = 1.75  # Points to adjust CORS by per win above/below expected
        week_zero_file.loc[:, "cors"] = week_zero_file["cors"] - (week_zero_file["wins_vs_expected"] * regression_factor)
        #print("regression done")
    except:
        pass
    
    week_zero_file = week_zero_file.sort_values(by="cors", ascending=False)

    week_zero_file_final = pd.concat([week_zero_file, missing_teams_copy])
    
    # Drop wins_vs_expected and expected_wins columns if they exist
    if "wins_vs_expected" in week_zero_file_final.columns:
        week_zero_file_final = week_zero_file_final.drop(columns=["wins_vs_expected"])
    if "expected_wins" in week_zero_file_final.columns:
        week_zero_file_final = week_zero_file_final.drop(columns=["expected_wins"])

    # Reset index to get proper ranking
    week_zero_file_final = week_zero_file_final.reset_index(drop=True)
    week_zero_file_final.index = range(1, week_zero_file_final.shape[0] + 1)
    week_zero_file_final.columns.name = "rank"


    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {year} W0 Rankings - {division} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {year} W0 Rankings - {division} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 

    week_zero_html = week_zero_file_final.to_html(index=True, escape=False)

    with open(f"{year}_W0_{division}_cors.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(week_zero_html)
    os.chdir(config.owd)

    return week_zero_file_final
