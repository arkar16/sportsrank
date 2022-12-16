# this file calls all other functions to create rankings for a particular season or week

"""
here, import each files
"""
import os
from teams import get_teams
from cors import weekly_cors

# CONSTANTS
DIVISION = "fbs"  # define division (currently only supporting FBS)
YEAR = 2022  # define year
WEEK = 1  # define week

"""
other constants here
"""

get_teams(YEAR, DIVISION)  # sets team file for year and division, creates HTML output
weekly_cors(YEAR, WEEK, DIVISION) # runs CORS calculation for set year and week, creates HTML output