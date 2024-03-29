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
    #missing_teams_copy = missing_teams_copy.drop(columns=["logo"])
    missing_teams_copy["record"] = "0-0"
    missing_teams_copy["win_pct"] = ""
    missing_teams_copy["cors"] = config.fcs_constant 

    week_zero_file.loc[:, "record"] = "0-0"
    week_zero_file.loc[:, "win_pct"] = ""
    #week_zero_file.reset_index(inplace=True)
    #week_zero_file.index = range(1, week_zero_file.shape[0] + 1)
    #week_zero_file.columns.name = "rank"
    #week_zero_file = week_zero_file.drop(columns=["rank"])

    week_zero_file_final = pd.concat([week_zero_file, missing_teams_copy])
    week_zero_file_final.reset_index(inplace=True)
    week_zero_file_final.index = range(1, week_zero_file_final.shape[0] + 1)
    week_zero_file_final.columns.name = "rank"
    week_zero_file_final = week_zero_file_final.drop(columns=["index"])
    #week_zero_file_final.insert(0, "logo", "")

    #teams_sorted = teams.sort_values(by="school", ascending=True)
    #week_zero_sorted = week_zero_file_final.sort_values(by="school", ascending=True)
    #week_zero_sorted.reset_index(inplace=True)
    #merged = pd.merge(teams_sorted, week_zero_sorted, on="school")
    #week_zero_sorted["logo"] = merged["logo_x"]
    #week_zero_sorted = week_zero_sorted.drop(columns=["index"])
    #week_zero_sorted = week_zero_file_final.sort_values(by="cors", ascending=False)
    #week_zero_sorted_final = week_zero_sorted.drop(columns=["index"])
    #week_zero_sorted_final = week_zero_sorted_final.sort_values(by="cors", ascending=False)
    #week_zero_sorted_final.index = range(1, week_zero_sorted_final.shape[0]+1)
    #week_zero_file_final = week_zero_file_final.apply(find_logo, axis=1)

    #week_zero_html = week_zero_sorted_final.to_html(escape=False)
    week_zero_html = week_zero_file_final.to_html(escape=False)
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {year} W0 Rankings - {division} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {year} W0 Rankings - {division} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 

    with open(f"{year}_W0_{division}_cors.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(week_zero_html)
    os.chdir(config.owd)

    return week_zero_file_final
    #return week_zero_sorted_final