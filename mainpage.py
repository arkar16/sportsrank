import os
import webconfig
import time

timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
start_time = time.time()

def mainpage(timestamp): 
    os.chdir("/Users/aryak/Projects/sportsrank/website")
    title_html = "<html>\n"
    title_html += "<head>\n"
    title_html += f"<title>sportsrank - CORS {webconfig.cors_version}</title>\n"
    title_html += "</head>\n"
    title_html += "<body>\n"
    title_html += f"<h1>CORS {webconfig.cors_version} Results</h1>\n"
    title_html += "</body>\n"
    title_html += "</html>\n"
    timestamp = f"Last updated: {timestamp}<hr>\n"
    with open("index.html", "w") as f: # TODO might need to change this in the future for different sports
        f.write(title_html)
        f.write(timestamp) 
        f.close()
    for sport in webconfig.sports.split(", "):
        sport_upper = sport.upper()
        sport_html = f'<a href="{sport}/{sport}.html">{sport_upper} - CORS</a><br>\n'
        with open("index.html", "a") as f: # TODO might need to change this in the future for different sports
            f.write(sport_html)
            f.close()

mainpage(timestamp)
print("mainpage.py Mainpage updated in: --- %s seconds ---" % (time.time() - start_time))