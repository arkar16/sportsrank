import os
import config

def nc_to_history(year, division, final_cors_df): # national champ to history
    os.chdir(f"{config.owd}/history")
    national_champ_df = final_cors_df.head(n=1)
    national_champ_df.insert(0, 'year', year)
    division = division.upper()

    if year == 1897: # first year
        title_html = "<html>\n"
        title_html += "<head>\n"
        title_html += f"<title>CORS {config.cors_version} - National Champions - {division} CFB</title>\n" # TODO change CFB to SPORT variable
        title_html += "</head>\n"
        title_html += "<body>\n"
        title_html += f"<h1>CORS {config.cors_version} - National Champions - {division} CFB</h1>\n" # TODO change CFB to SPORT variable
        title_html += "</body>\n"
        title_html += "</html>\n"
        nc_html = national_champ_df.to_html(escape=False, index=False, header=False)
        with open(f"CORS_NC_{division}_CFB.html", "w") as f: # TODO change CFB to SPORT variable
            f.write(title_html)
            f.write(nc_html)
        os.chdir(config.owd)
    else:
        nc_html = national_champ_df.to_html(escape=False, index=False, header=False)
        with open(f"CORS_NC_{division}_CFB.html", "a") as f:
            f.write(nc_html)
        os.chdir(config.owd)

def worst_to_history(year, division, final_cors_df): # worst team to history
    os.chdir(f"{config.owd}/history")
    worst_team_df = final_cors_df.tail(n=1)
    worst_team_df.insert(0, 'year', year)
    division = division.upper()

    if year == 1897:
        title_html = "<html>\n"
        title_html += "<head>\n"
        title_html += f"<title>CORS {config.cors_version} - Worst Team - {division} CFB</title>\n" # TODO change CFB to SPORT variable
        title_html += "</head>\n"
        title_html += "<body>\n"
        title_html += f"<h1>CORS {config.cors_version} - Worst Team - {division} CFB</h1>\n" # TODO change CFB to SPORT variable
        title_html += "</body>\n"
        title_html += "</html>\n"
        wt_html = worst_team_df.to_html(escape=False, header=False)
        with open(f"CORS_WT_{division}_CFB.html", "w") as f: # TODO change CFB to SPORT variable
            f.write(title_html)
            f.write(wt_html)
        os.chdir(config.owd)
    else:
        wt_html = worst_team_df.to_html(escape=False, header=False)
        with open(f"CORS_WT_{division}_CFB.html", "a") as f:
            f.write(wt_html)
        os.chdir(config.owd)