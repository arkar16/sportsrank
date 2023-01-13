import config
import os

# TODO Change all CFB to {SPORT} tags

def html_grab(start_year, end_year, division):
    division = division.upper()
    os.chdir("/Users/aryak/Projects/sportsrank/")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>CORS {config.cors_version} Results</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {config.cors_version} Results</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    cfb_html = f'<a href="website/cfb/cfb.html"><h1>{division} CFB - CORS<h1></a><br>\n' # TODO CFB to {SPORT}
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
    html_nc = f'<a href="../../../cfb/years/history/nc_{division}_CFB_output.html"><h1>Link to all-time {division} CFB National Champions</h1></a>\n' # TODO CFB to {SPORT}
    html_wt = f'<a href="../../../cfb/years/history/wt_{division}_CFB_output.html"><h1>Link to all-time {division} CFB Worst Teams</h1></a>\n' # TODO CFB to {SPORT}
    html_years = f"<h1>Yearly CFB Results:</h1>\n" # TODO change CFB to SPORT variable
    with open("website/cfb/cfb.html", "w") as f: # TODO change CFB to {SPORT}
            f.write(title_html)
            f.write(html_nc)
            f.write(html_wt)
            f.write(html_years)
    for year in range(end_year, start_year - 1, -1): # FIXME links are broken from here down
        html_template = f'<a href="../../../cfb/years/{year}/rankings/{year}_FINAL_{division}_cors.html">Link to {year} {division} CFB final rankings</a><br>\n'
        year_template = f'<a href="years/{year}_CFB.html">Link to {year} {division} CFB</a><br>\n'
        with open(f"website/cfb/years/{year}_CFB.html", "w") as f: # TODO change CFB to {SPORT}
            f.write(html_template)
        with open("website/cfb/cfb.html", "a") as f: # TODO change CFB to {SPORT}
            f.write(year_template)

