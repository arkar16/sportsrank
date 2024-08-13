# CFB Rankings

This project calculates and generates rankings for College Football (CFB) games.

## Setup

1. Ensure you have Python 3.7+ installed on your system.
2. Install the required packages by running:
   ```
   pip install -r requirements.txt
   ```

## Running the Script

You can run the script using the provided batch file or directly using Python.

### Using the Batch File

1. Double-click on `run_cfb_rankings.bat`, or
2. Open a command prompt in the project directory and run:
   ```
   run_cfb_rankings.bat [calculation_type] [year] [week]
   ```

### Using Python Directly

Open a command prompt in the project directory and run:

```
python main.py [calculation_type] [year] [week]
```

### Command-line Arguments

- `calculation_type`: Type of calculation to perform. Options are:
  - `single_week`: Calculate rankings for a single week
  - `full_season`: Calculate rankings for the full season (default)
  - `history`: Calculate historical rankings
- `year`: The year to calculate rankings for (default is the current year)
- `week`: The week to calculate rankings for (default is the current week)

If no arguments are provided, the script will default to a full season calculation for the current year and week.

## Output

The script will generate various output files in the project directory, including:
- Ranking files
- HTML output for web display
- Cleaned data files

Check the console output or log files for any errors or additional information during the execution of the script.