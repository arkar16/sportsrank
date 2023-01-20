import pandas as pd
import os
import config


def get_weekly_results(year, week, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # get data from csv and convert to df
    os.chdir(f"/Users/aryak/Projects/sportsrank/{config.sport}/data/{year}")
    fbs_week_results = pd.read_csv(f"{config.sport}_{year}.csv", header=None)
    fbs_week_results = fbs_week_results.drop(fbs_week_results.columns[[0]], axis=1)
    fbs_week_results = fbs_week_results.drop(fbs_week_results.index[0])
    fbs_week_results = fbs_week_results.rename(columns={1: "week", 2: "home_team", 3: "home_score", 4: "away_team", 5: "away_score", 6: "neutral_site"})
    fbs_week_results["week"] = fbs_week_results["week"].astype(int)
    fbs_week_results["home_score"] = fbs_week_results["home_score"].astype(int)
    fbs_week_results["away_score"] = fbs_week_results["away_score"].astype(int)
    fbs_week_results["neutral_site"].astype(bool)
    fbs_week_results = fbs_week_results.loc[fbs_week_results["week"] == week]

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    # convert games to html
    week_results_html = fbs_week_results.to_html(index=False)

    # write games to html file for viewing
    os.chdir(config.owd)
    os.chdir(f"{YEAR}/data/results/weekly_results")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} W{WEEK} Results - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} W{WEEK} Results - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_results.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(week_results_html)
    os.chdir(config.owd)

    return fbs_week_results

def get_results(year, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # get data from csv and convert to df
    os.chdir(f"/Users/aryak/Projects/sportsrank/{config.sport}/data/{year}")
    fbs_results = pd.read_csv(f"{config.sport}_{year}.csv", header=None)
    fbs_results = fbs_results.drop(fbs_results.columns[[0]], axis=1)
    fbs_results = fbs_results.drop(fbs_results.index[0])
    fbs_results = fbs_results.rename(columns={1: "week", 2: "home_team", 3: "home_score", 4: "away_team", 5: "away_score", 6: "neutral_site"})
    fbs_results["week"] = fbs_results["week"].astype(int)
    fbs_results["home_score"] = fbs_results["home_score"].astype(int)
    fbs_results["away_score"] = fbs_results["away_score"].astype(int)
    fbs_results["neutral_site"].astype(bool)

    # CONSTANTS
    YEAR = year
    DIVISION = division

    # convert games to html
    results_html = fbs_results.to_html(index=False)

    # write games to html file for viewing
    os.chdir(config.owd)
    os.chdir(f"{YEAR}/data/results")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} Results - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} Results - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_{DIVISION}_results.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(results_html)
    os.chdir(config.owd)

    return fbs_results

def get_week_slate(year, week, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # get data from csv and convert to df
    os.chdir(f"/Users/aryak/Projects/sportsrank/{config.sport}/data/{year}")
    fbs_week_slate = pd.read_csv(f"{config.sport}_{year}.csv", header=None)
    fbs_week_slate = fbs_week_slate.drop(fbs_week_slate.columns[[0,3,5]], axis=1)
    fbs_week_slate = fbs_week_slate.drop(fbs_week_slate.index[0])
    fbs_week_slate = fbs_week_slate.rename(columns={1: "week", 2: "home_team", 4: "away_team", 6: "neutral_site"})
    fbs_week_slate["week"] = fbs_week_slate["week"].astype(int)
    fbs_week_slate["neutral_site"].astype(bool)
    fbs_week_slate = fbs_week_slate.loc[fbs_week_slate["week"] == week]

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    # convert games to html
    week_slate_html = fbs_week_slate.to_html(index=False)

    # write games to html file for viewing
    os.chdir(config.owd)
    os.chdir(f"{YEAR}/data/slate/weekly_slate")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} W{WEEK} Slate - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} W{WEEK} Slate - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_W{WEEK}_{DIVISION}_slate.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(week_slate_html)
    os.chdir(config.owd)

    return fbs_week_slate

def get_slate(year, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # get data from csv and convert to df
    os.chdir(f"/Users/aryak/Projects/sportsrank/{config.sport}/data/{year}")
    fbs_slate = pd.read_csv(f"{config.sport}_{year}.csv", header=None)
    fbs_slate = fbs_slate.drop(fbs_slate.columns[[0,3,5]], axis=1)
    fbs_slate = fbs_slate.drop(fbs_slate.index[0])
    fbs_slate = fbs_slate.rename(columns={1: "week", 2: "home_team", 4: "away_team", 6: "neutral_site"})
    fbs_slate["week"] = fbs_slate["week"].astype(int)
    fbs_slate["neutral_site"].astype(bool)

    # CONSTANTS
    YEAR = year
    DIVISION = division

    # convert games to html
    slate_html = fbs_slate.to_html(index=False)

    # write games to html file for viewing
    os.chdir(config.owd)
    os.chdir(f"{YEAR}/data/slate")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} Slate - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} Slate - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_{DIVISION}_slate.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(slate_html)
    os.chdir(config.owd)

    return fbs_slate