import config
import os

# TODO Change all CFB to {SPORT} tags

def html_grab(start_year, end_year, division):
    division = division.upper()
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} Results</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} Results</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    cfb_html = f'<a href="cfb.html">Link to all {division} CFB CORS results</a><br>\n' # TODO CFB to {SPORT}
    os.chdir("/Users/aryak/Projects/sportsrank/")
    with open("index.html", "w") as f: # TODO might need to change this in the future for different sports
            f.write(title_html)
            f.write(cfb_html)
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} CFB Results</title>\n" # TODO change CFB to SPORT variable
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} CFB Results</h1>\n" # TODO change CFB to SPORT variable
    title_html += "</body>\n"
    title_html += "</html>\n"
    html_nc = f'<a href="cfb/years/history/nc_{division}_CFB_output.html">Link to all-time {division} CFB National Champions</a><br>\n' # TODO CFB to {SPORT}
    html_wt = f'<a href="cfb/years/history/wt_{division}_CFB_output.html">Link to all-time {division} CFB Worst Teams</a><br>\n' # TODO CFB to {SPORT}
    with open("cfb.html", "a") as f:
            f.write(title_html)
            f.write(html_nc)
            f.write(html_wt)
    for year in range(end_year, start_year - 1, -1):
        html_template = f'<a href="cfb/years/{year}/rankings/{year}_FINAL_{division}_cors.html">Link to {year} {division} CFB final rankings</a><br>\n'
        with open("cfb.html", "a") as f:
            f.write(html_template)

