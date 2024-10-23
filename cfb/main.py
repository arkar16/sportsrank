import sys
import time
import logging
from datetime import datetime, timedelta
from calc import single_week_calc, full_season_calc, history_calc
from end_week import get_end_week
from html_grab import html_grab
from nc_wt_clean import nc_clean, wt_clean

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# CONSTANTS
YEAR = 2024  # define current year (cannot be earlier than 1897)
START_YEAR = 1897  # define start year
END_YEAR = 2024 # define end year
WEEK = 9  # define current week
START_WEEK = 0  # define start week
END_WEEK = 9  # define last week of season
DIVISION = "FBS"  # define division (currently only supporting FBS)
HFA = 2  # define HFA constant for spread
BASE_CORS = 0

def get_current_year_and_week():
    current_date = datetime.now()
    current_year = current_date.year
    
    # Assuming the season starts on the first Saturday of September
    season_start = datetime(current_year, 9, 1)
    while season_start.weekday() != 5:  # 5 is Saturday
        season_start += timedelta(days=1)
    
    weeks_since_start = (current_date - season_start).days // 7
    current_week = max(0, min(weeks_since_start, END_WEEK))
    
    return current_year, current_week

def run_calculations(calc_type, year, start_week, end_week, division, hfa, base_cors, timestamp):
    try:
        if calc_type == "single_week":
            single_week_calc(year, WEEK, end_week, division, hfa, base_cors, timestamp)
        elif calc_type == "full_season":
            full_season_calc(year, start_week, end_week, division, hfa, base_cors, timestamp)
        elif calc_type == "history":
            history_calc(START_YEAR, END_YEAR, start_week, end_week, division, hfa, base_cors, timestamp)
        else:
            logging.error(f"Invalid calculation type: {calc_type}")
    except Exception as e:
        logging.error(f"Error during {calc_type} calculation: {str(e)}")

def main():
    start_time = time.time()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    
    # Use command-line arguments to override default settings
    if len(sys.argv) > 1:
        calc_type = sys.argv[1]
        year = int(sys.argv[2]) if len(sys.argv) > 2 else YEAR
        week = int(sys.argv[3]) if len(sys.argv) > 3 else WEEK
    else:
        calc_type = "single_week"  # Default to weekly calculation
        year, week = get_current_year_and_week()
    
    logging.info(f"Starting {calc_type} calculation for year {year}, week {week}")
    
    run_calculations(calc_type, year, START_WEEK, END_WEEK, DIVISION, HFA, BASE_CORS, timestamp)
    
    # Run additional processes
    if calc_type == "history":
        try:
            nc_clean(DIVISION, timestamp)
            wt_clean(DIVISION, timestamp)
            html_grab(START_YEAR, END_YEAR, START_WEEK, END_WEEK, DIVISION, timestamp)
        except Exception as e:
            logging.error(f"Error during additional processes: {str(e)}")
    
    if week == 0:
        html_grab(year, year, START_WEEK, END_WEEK, DIVISION, timestamp)
    
    logging.info(f"Process finished in {time.time() - start_time:.2f} seconds")

if __name__ == "__main__":
    main()