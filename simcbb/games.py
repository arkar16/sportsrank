import cfbd
import pandas as pd
import os
import config
import gspread
from oauth2client.service_account import ServiceAccountCredentials


def get_weekly_results(year, week, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # google sheets scrape
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    os.chdir(f"/Users/aryak/Projects/sportsrank/simcbb")
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open("Advanced Rankings")
    results_ws = spreadsheet.get_worksheet(4)
    data = results_ws.get_all_values()

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    # convert scrape to df
    d1_week_results = pd.DataFrame(data)
    d1_week_results = d1_week_results.drop(d1_week_results.columns[[1,2,5,6,7,10,11,12,14,15,16,17,18,19,20,21,22,23,24,25,26]], axis=1)
    d1_week_results = d1_week_results.rename(columns={0: "week", 3: "home_team", 4: "home_score", 8: "away_team", 9: "away_score", 13: "neutral_site"})
    d1_week_results = d1_week_results.drop(d1_week_results.index[0])
    d1_week_results["week"] = d1_week_results["week"].str.replace('[a-zA-Z]','', regex=True)
    d1_week_results["neutral_site"] = d1_week_results["neutral_site"].astype(bool)
    d1_week_results["week"] = d1_week_results["week"].astype(int)
    d1_week_results["home_score"] = d1_week_results["home_score"].astype(int)
    d1_week_results["away_score"] = d1_week_results["away_score"].astype(int)
    d1_week_results["neutral_site"] = d1_week_results["neutral_site"].astype(bool)
    d1_week_results = d1_week_results.loc[d1_week_results["week"] == week]

    # convert games to html
    week_results_html = d1_week_results.to_html(index=False)

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

    return d1_week_results


def get_results(year, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # google sheets scrape
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    os.chdir(f"/Users/aryak/Projects/sportsrank/simcbb")
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open("Advanced Rankings")
    results_ws = spreadsheet.get_worksheet(4)
    data = results_ws.get_all_values()
    
    # convert scrape to df
    d1_results = pd.DataFrame(data)
    d1_results = d1_results.drop(d1_results.columns[[1,2,5,6,7,10,11,12,14,15,16,17,18,19,20,21,22,23,24,25,26]], axis=1)
    d1_results = d1_results.rename(columns={0: "week", 3: "home_team", 4: "home_score", 8: "away_team", 9: "away_score", 13: "neutral_site"})
    d1_results = d1_results.drop(d1_results.index[0])
    d1_results["week"] = d1_results["week"].str.replace('[a-zA-Z]','', regex=True)
    d1_results["neutral_site"] = d1_results["neutral_site"].astype(bool)
    d1_results["week"] = d1_results["week"].astype(int)
    d1_results["home_score"] = d1_results["home_score"].astype(int)
    d1_results["away_score"] = d1_results["away_score"].astype(int)
    d1_results["neutral_site"] = d1_results["neutral_site"].astype(bool)

    # CONSTANTS
    YEAR = year
    DIVISION = division

    # convert games to html
    results_html = d1_results.to_html(index=False)

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

    return d1_results


def get_week_slate(year, week, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # google sheets scrape
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    os.chdir(f"/Users/aryak/Projects/sportsrank/simcbb")
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open("Advanced Rankings")
    results_ws = spreadsheet.get_worksheet(4)
    data = results_ws.get_all_values()

    # CONSTANTS
    YEAR = year
    WEEK = week
    DIVISION = division

    # convert scrape to df
    d1_week_slate = pd.DataFrame(data)
    d1_week_slate = d1_week_slate.drop(d1_week_slate.columns[[1,2,4,5,6,7,9,10,11,12,14,15,16,17,18,19,20,21,22,23,24,25,26]], axis=1)
    d1_week_slate = d1_week_slate.rename(columns={0: "week", 3: "home_team", 8: "away_team", 13: "neutral_site"})
    d1_week_slate = d1_week_slate.drop(d1_week_slate.index[0])
    d1_week_slate["week"] = d1_week_slate["week"].str.replace('[a-zA-Z]','', regex=True)
    d1_week_slate["neutral_site"] = d1_week_slate["neutral_site"].astype(bool)
    d1_week_slate["week"] = d1_week_slate["week"].astype(int)
    d1_week_slate["neutral_site"] = d1_week_slate["neutral_site"].astype(bool)
    d1_week_slate = d1_week_slate.loc[d1_week_slate["week"] == week]


    # convert games to html
    week_slate_html = d1_week_slate.to_html(index=False)

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

    return d1_week_slate

def get_slate(year, division, timestamp):
    # get original working directory
    os.chdir(config.owd)
    sport_upper = config.sport.upper()

    # google sheets scrape
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    os.chdir(f"/Users/aryak/Projects/sportsrank/simcbb")
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open("Advanced Rankings")
    results_ws = spreadsheet.get_worksheet(4)
    data = results_ws.get_all_values()
    
    # convert scrape to df
    d1_slate = pd.DataFrame(data)
    d1_slate = d1_slate.drop(d1_slate.columns[[1,2,4,5,6,7,9,10,11,12,14,15,16,17,18,19,20,21,22,23,24,25,26]], axis=1)
    d1_slate = d1_slate.rename(columns={0: "week", 3: "home_team", 8: "away_team", 13: "neutral_site"})
    d1_slate = d1_slate.drop(d1_slate.index[0])
    d1_slate["week"] = d1_slate["week"].str.replace('[a-zA-Z]','', regex=True)
    d1_slate["week"] = d1_slate["week"].astype(int)
    d1_slate["neutral_site"] = d1_slate["neutral_site"].astype(bool)
    

    # CONSTANTS
    YEAR = year
    DIVISION = division

    # convert games to html
    slate_html = d1_slate.to_html(index=False)

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

    return d1_slate
