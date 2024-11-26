import os
import config
import pandas as pd

def get_end_week(year):
    # get original working directory
    os.chdir(config.owd)
    
    # Read the slate file for the year
    slate = pd.read_html(f"{year}/data/slate/{year}_FBS_slate.html")[0]
    
    # Get highest week number from slate
    end_week = slate["week"].max()
    
    # Cap at config.week_count if needed
    if end_week > config.week_count:
        end_week = config.week_count
        
    return end_week
