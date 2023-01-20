import config
import os

# TODO need to change this file to be outside of the CFB folder to allow for different sports
sport_upper = config.sport.upper()

def html_grab(start_year, end_year, week, end_week, division, timestamp):
    os.chdir(f"/Users/aryak/Projects/sportsrank/website")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} {sport_upper} Results</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} {sport_upper} Results</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    html_nc = f'<a href="years/history/nc_{division}_{sport_upper}_output.html"><h1>Link to all-time {division} {sport_upper} National Champions</h1></a>\n'
    html_wt = f'<a href="years/history/wt_{division}_{sport_upper}_output.html"><h1>Link to all-time {division} {sport_upper} Worst Teams</h1></a>\n'
    html_years = f"<h1>Yearly {sport_upper} Results:</h1>\n"
    with open(f"{config.sport}/{config.sport}.html", "w") as f:
            f.write(title_html)
            f.write(html_nc)
            f.write(html_wt)
            f.write(html_years)
            f.close()
    for year in range(end_year, start_year - 1, -1):
        final_template = f'<a href="rankings/{year}_FINAL_{division}_cors.html">Link to {year} {division} {sport_upper} final rankings</a><br>\n'
        year_title_html = "<html>\n"
        year_title_html += "<head>\n"
        year_title_html += f"<title>{year} CORS {config.cors_version} {sport_upper} Results</title>\n"
        year_title_html += "</head>\n"
        year_title_html += "<body>\n"
        year_title_html += f"<h1>{year} CORS {config.cors_version} {sport_upper} Results</h1>\n"
        year_title_html += "</body>\n"
        year_title_html += "</html>\n"
        year_template = f'<a href="years/{year}/{year}_{sport_upper}.html">Link to {year} {division} CFB</a><br>\n'
        timestamp = f"Last updated: {timestamp}<hr>\n" 
        with open(f"{config.sport}/years/{year}/{year}_{sport_upper}.html", "w") as f:
            f.write(year_title_html)
            f.write(timestamp)
            f.write(final_template)
            for week in range(0, config.week_count + 1):
                f.write(f'<a href="rankings/{year}_W{week}_{division}_cors.html">Link to {year} W{week} {division} {sport_upper} rankings</a><br>\n')
            f.close()
        os.chdir("/Users/aryak/Projects/sportsrank/website")
        with open(f"{config.sport}/{config.sport}.html", "a") as f:
            f.write(year_template)
            f.close()
        print(f"{year} done")

