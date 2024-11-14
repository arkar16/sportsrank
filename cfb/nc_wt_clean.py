import pandas as pd
from bs4 import BeautifulSoup
import config
import os
import glob

def process_rankings(division, timestamp):
    """Process all FINAL rankings files to extract national champions and worst teams in one pass."""
    sport_upper = config.sport.upper()
    division = division.upper()
    
    # Lists to store national champions and worst teams data
    nc_data = []
    wt_data = []
    
    # Get all FINAL rankings files
    years_path = f"{config.owd}/website/cfb/years"
    for year_dir in sorted(glob.glob(f"{years_path}/*/"), reverse=True):
        year = os.path.basename(os.path.dirname(year_dir))
        if not year.isdigit():
            continue
            
        final_ranking_file = f"{year_dir}/rankings/{year}_FINAL_{division}_cors.html"
        if not os.path.exists(final_ranking_file):
            continue
            
        with open(final_ranking_file) as f:
            soup = BeautifulSoup(f, "html.parser")
            
        # Find the rankings table
        tables = soup.find_all("table")
        if not tables:
            continue
            
        df = pd.read_html(str(tables[0]))[0]
        
        if len(df) == 0:
            continue
            
        # Extract national champion (first row) and worst team (last row)
        national_champion = df.iloc[0]
        worst_team = df.iloc[-1]
        
        # Add to national champions data
        nc_data.append({
            "Year": year,
            "School": national_champion["school"],
            "Conference": national_champion["conference"],
            "Record": national_champion["record"],
            "Win%": national_champion["win_pct"],
            "CORS": national_champion["cors"]
        })
        
        # Add to worst teams data
        wt_data.append({
            "Year": year,
            "School": worst_team["school"],
            "Conference": worst_team["conference"],
            "Record": worst_team["record"],
            "Win%": worst_team["win_pct"],
            "CORS": worst_team["cors"]
        })
    
    # Create DataFrames
    nc_df = pd.DataFrame(nc_data)
    wt_df = pd.DataFrame(wt_data)
    
    # Add HTML links to years
    years_html = []
    for year in nc_df["Year"]:
        years_html.append(f"<center><a href='../{year}/{year}_CFB.html'>{year}</a></center>")
    nc_df["Year"] = years_html
    wt_df["Year"] = years_html
    
    # Generate HTML for national champions
    nc_html = nc_df.to_html(justify="left", escape=False, index=False, table_id="nationalchamp", classes="display")
    nc_jquery = generate_jquery_code("nationalchamp", division, sport_upper, "National Champions", timestamp)
    
    # Generate HTML for worst teams
    wt_html = wt_df.to_html(justify="left", escape=False, index=False, table_id="worstteam", classes="display")
    wt_jquery = generate_jquery_code("worstteam", division, sport_upper, "Worst Teams", timestamp)
    
    # Write output files
    os.chdir(f"{config.owd}/history")
    with open(f"nc_{division}_{sport_upper}_output.html", "w") as f:
        f.write(nc_jquery)
        
    with open(f"wt_{division}_{sport_upper}_output.html", "w") as f:
        f.write(wt_jquery)

def generate_jquery_code(table_id, division, sport_upper, title_text, timestamp):
    """Generate the jQuery and HTML wrapper code for tables."""
    jquery_code = "<script src='https://code.jquery.com/jquery-3.5.1.js'></script>\n"
    jquery_code += "<script src='https://cdn.datatables.net/1.10.22/js/jquery.dataTables.min.js'></script>\n"
    jquery_code += "<script>\n"
    jquery_code += "$(document).ready( function () {\n"
    jquery_code += f"$('#{table_id}').DataTable({{\n"
    jquery_code += "paging: false});\n"
    jquery_code += "});\n"
    jquery_code += "</script>\n"
    
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - {title_text} - {division} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - {title_text} - {division} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    
    timestamp_html = f"Last updated: {timestamp}<hr>\n"
    
    if table_id == "nationalchamp":
        table_html = nc_html
    else:
        table_html = wt_html
        
    return jquery_code + title_html + timestamp_html + table_html + jquery_code
