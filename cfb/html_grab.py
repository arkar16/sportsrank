import main
import config
import os

start_year = main.YEAR - 1
end_year = main.END_YEAR
week = main.WEEK
division = main.DIVISION.upper()

# TODO Change all CFB to {SPORT} tags

def html_grab(start_year, end_year, division):
    os.chdir("/Users/aryak/Projects/sportsrank/")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} Results</title>\n" # TODO change CFB to SPORT variable
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} Results</h1>\n" # TODO change CFB to SPORT variable
    title_html += "</body>\n"
    title_html += "</html>\n"
    html_nc = f'<a href="/cfb/years/history/CORS_NC_{division}_CFB">Link to all {division} CFB National Champions</a><br>\n'
    html_wt = f'<a href="/cfb/years/history/CORS_WT_{division}_CFB">Link to all {division} CFB Worst Teams</a><br>\n'
    with open("index.html", "a") as f:
            f.write(title_html)
            f.write(html_nc)
            f.write(html_wt)
    for year in range(end_year, start_year, -1):
        html_template = f'<a href="/cfb/years/{year}/rankings/{year}_FINAL_{division}_cors.html">Link to {year} {division} CFB final rankings</a><br>\n'
        with open("index.html", "a") as f:
            f.write(html_template)
    
html_grab(start_year, end_year, division)

