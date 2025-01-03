import config
import os
from end_week import get_end_week

sport_upper = config.sport.upper()

def html_grab(start_year, end_year, week, end_week, division, timestamp):
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
        os.chdir("/Users/aryak/Projects/sportsrank/website")
        with open(f"{config.sport}/years/{year}/{year}_{sport_upper}.html", "w") as f:
            f.write(year_title_html)
            #f.write(timestamp)
            f.write(final_template)
            for week in range(0, end_week):
                f.write(f'<a href="rankings/{year}_W{week}_{division}_cors.html">Link to {year} W{week} {division} {sport_upper} rankings</a><br>\n')
            for week in range(1, end_week):
                f.write(f'<a href="spread/{year}_W{week}_{division}_spread.html">Link to {year} W{week} {division} {sport_upper} spread</a><br>\n')
            f.close()
        #os.chdir("/Users/aryak/Projects/sportsrank/website")
        #with open(f"{config.sport}/{config.sport}.html", "a") as f:
            #f.write(year_template)
            #f.close()
        #print(f"{year} End week: {end_week}")
        #print(f"{year} HTML files created")

