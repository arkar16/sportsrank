# this is the file that holds all constants for the formulas
from pathlib import Path

cors_version = "v0.4.0" # version of CORS
sport = "cfb"
repo_root = Path(__file__).resolve().parent.parent
website_root = repo_root / "website"
owd = str(website_root / sport / "years") # working directory for output
fcs_constant = -10 # base value for FCS teams and new teams
week_count = 16 # number of max weeks in a season for sport

