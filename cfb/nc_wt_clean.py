import pandas as pd
from bs4 import BeautifulSoup
import config
import os


def nc_clean(division, timestamp):
    sport_upper = config.sport.upper()
    division = division.upper()
    os.chdir(f"{config.owd}/history")
    with open(f"CORS_NC_{division}_{sport_upper}.html") as f:
        soup = BeautifulSoup(f, "html.parser")

    tables = soup.find_all("table")
    
    nc_dfs = []
    for table in tables:
        nc_df = pd.read_html(str(table))[0]
        nc_df = nc_df.rename(columns={0: "Year", 1: "Logo", 2: "School", 3: "Conference", 4: "Record", 5: "Win%", 6: "CORS"})
        imgs = table.find_all("img")
        srcs = [img["src"] for img in imgs]
        src_str = " ".join(srcs)  # Concatenate the srcs list into a single string
        nc_df["Logo"] = f"<center><img src='{src_str}' style='width: 20px; height: 20px;'></center>"
        nc_df.style.set_properties(**{"width": "100px"})
        nc_dfs.append(nc_df)

    nc_df = pd.concat(nc_dfs)

    nc_df = nc_df.sort_values(by=["Year"], ascending=False)

    years = nc_df["Year"].tolist()
    years_html = []
    for year in years:
        years_html.append(f"<center><a href='../{year}/{year}_CFB.html'>{year}</a></center>")
    nc_df["Year"] = years_html

    nc_df = nc_df.drop_duplicates(subset=["Year"])

    nc_html = nc_df.to_html(justify="left", escape=False, index=False, table_id="nationalchamp", classes="display")
    jquery_top = "<script src='https://code.jquery.com/jquery-3.5.1.js'></script>\n"
    jquery_bottom = "<script src='https://cdn.datatables.net/1.10.22/js/jquery.dataTables.min.js'></script>\n"
    jquery_b2 = "<script>\n"
    jquery_b2 += "$(document).ready( function () {\n"
    jquery_b2 += "$('#nationalchamp').DataTable({\n"
    jquery_b2 += "paging: false});\n"
    jquery_b2 += "});\n"
    jquery_b2 += "</script>\n"
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - National Champions - {division} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - National Champions - {division} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"nc_{division}_{sport_upper}_output.html", "w") as f:
        f.write(jquery_top)
        f.write(jquery_bottom)
        f.write(title_html)
        f.write(timestamp)
        f.write(nc_html)
        f.write(jquery_b2)

def wt_clean(division, timestamp):
    sport_upper = config.sport.upper()
    division = division.upper()
    os.chdir(f"{config.owd}/history")
    with open(f"CORS_WT_{division}_{sport_upper}.html") as f:
        soup = BeautifulSoup(f, "html.parser")

    tables = soup.find_all("table")
    
    wt_dfs = []
    for table in tables:
        wt_df = pd.read_html(str(table))[0]
        wt_df = wt_df.rename(columns={0: "Rank", 1: "Year", 2: "Logo", 3: "School", 4: "Conference", 5: "Record", 6: "Win%", 7: "CORS"})
        wt_df = wt_df.drop(columns={"Rank"})
        imgs = table.find_all("img")
        srcs = [img["src"] for img in imgs]
        src_str = " ".join(srcs)  # Concatenate the srcs list into a single string
        wt_df["Logo"] = f"<center><img src='{src_str}' style='width: 20px; height: 20px;'></center>"
        wt_df.style.set_properties(**{"width": "100px"})
        wt_dfs.append(wt_df)

    wt_df = pd.concat(wt_dfs)
    
    wt_df = wt_df.sort_values(by=["Year"], ascending=False)

    years = wt_df["Year"].tolist()
    years_html = []
    for year in years:
        years_html.append(f"<center><a href='../{year}/{year}_CFB.html'>{year}</a></center>")
    wt_df["Year"] = years_html

    wt_df = wt_df.drop_duplicates(subset=["Year"])

    wt_html = wt_df.to_html(justify="left", escape=False, index=False, table_id="worstteam", classes="display")
    jquery_top = "<script src='https://code.jquery.com/jquery-3.5.1.js'></script>\n"
    jquery_bottom = "<script src='https://cdn.datatables.net/1.10.22/js/jquery.dataTables.min.js'></script>\n"
    jquery_b2 = "<script>\n"
    jquery_b2 += "$(document).ready( function () {\n"
    jquery_b2 += "$('#worstteam').DataTable({\n"
    jquery_b2 += "paging: false});\n"
    jquery_b2 += "});\n"
    jquery_b2 += "</script>\n"
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} - Worst Teams - {division} {sport_upper}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} - Worst Teams - {division} {sport_upper}</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n" 
    with open(f"wt_{division}_{sport_upper}_output.html", "w") as f:
        f.write(jquery_top)
        f.write(jquery_bottom)
        f.write(title_html)
        f.write(timestamp)
        f.write(wt_html)
        f.write(jquery_b2)
