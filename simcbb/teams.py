import cfbd
import pandas as pd
import os
import config
from oauth2client.service_account import ServiceAccountCredentials
import gspread

def get_teams(year, division, timestamp):
    sport_upper = config.sport.upper()

    # google sheets scrape
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    os.chdir(f"/Users/aryak/Projects/sportsrank/simcbb")
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
        
    spreadsheet = client.open("Advanced Rankings")
    results_ws = spreadsheet.get_worksheet(10)
    data = results_ws.get_all_values()

    # convert scrape to df
    cfb_teams = pd.DataFrame(data)
    cfb_teams = cfb_teams.drop(cfb_teams.columns[[1,3,4,5,6,7,8]], axis=1)
    cfb_teams = cfb_teams.rename(columns={0: "school", 2: "conference"})
    cfb_teams = cfb_teams.drop(cfb_teams.index[0])
    cfb_teams.insert(loc=0, column="logo", value='')

    # CONSTANTS
    YEAR = year
    DIVISION = division

    # get original working directory
    os.chdir(config.owd)

    teams_html = cfb_teams.to_html(index=False, escape=False)

    # TODO add logos
    
    os.chdir(f"{YEAR}/data")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {YEAR} Teams - {DIVISION} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {YEAR} Teams - {DIVISION} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"{YEAR}_{DIVISION}_teams.html", "w") as f:
        f.write(title_html)
        f.write(timestamp)
        f.write(teams_html)
    os.chdir(config.owd)

    return cfb_teams