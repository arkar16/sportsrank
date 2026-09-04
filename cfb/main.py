import sys
import time
import logging
from pathlib import Path
try:
    from .cfbd_client import get_snapshot_service
except ImportError:  # direct execution compatibility
    from cfbd_client import get_snapshot_service

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# CONSTANTS
YEAR = 2025  # define current year (cannot be earlier than 1897)
START_YEAR = 2021 # define start year
END_YEAR = 2023 # define end year
WEEK = 0  # define current week
START_WEEK = 0  # define start week
end_week = 15  # define end week (max 15)
DIVISION = "FBS"  # define division (currently only supporting FBS)
HFA = 2  # define HFA constant for spread
BASE_CORS = 0

# Filled lazily for the legacy direct-file path.  Keeping placeholders makes
# the integration seam patchable without importing modules that still use
# cfb/ as their working-directory package.
single_week_calc = None
full_season_calc = None
history_calc = None

def get_current_year_and_week():
    """Reject the removed calendar guess used by the old no-argument path."""

    raise ValueError(
        "legacy calculations require explicit season/week arguments; "
        "use `python -m cfb.recovery --help` for staged recovery commands"
    )

def run_calculations(calc_type, year, week, start_week, division, hfa, base_cors, timestamp, snapshot_service=None):
    if single_week_calc is None:
        cfb_directory = str(Path(__file__).resolve().parent)
        if cfb_directory not in sys.path:
            sys.path.insert(0, cfb_directory)
        from calc import single_week_calc as loaded_single_week_calc
        from calc import full_season_calc as loaded_full_season_calc
        from calc import history_calc as loaded_history_calc
        globals().update(
            single_week_calc=loaded_single_week_calc,
            full_season_calc=loaded_full_season_calc,
            history_calc=loaded_history_calc,
        )
    try:
        # A Snapshot is the supported source of the scheduled season end.
        # The no-service branch remains only for the legacy direct-file
        # interface and therefore retains its caller-supplied boundary.
        effective_end_week = end_week
        if snapshot_service is not None and calc_type != "history":
            snapshot = snapshot_service.get(year, division)
            effective_end_week = max(
                (int(game.week) for game in snapshot.games),
                default=0,
            )
        
        if calc_type == "single_week":
            if snapshot_service is None:
                single_week_calc(year, week, end_week, division, hfa, base_cors, timestamp)
            else:
                single_week_calc(year, week, effective_end_week, division, hfa, base_cors, timestamp, snapshot_service)
        elif calc_type == "full_season":
            if snapshot_service is None:
                full_season_calc(year, start_week, end_week, division, hfa, base_cors, timestamp)
            else:
                full_season_calc(year, start_week, effective_end_week, division, hfa, base_cors, timestamp, snapshot_service)
        elif calc_type == "history":
            if snapshot_service is None:
                history_calc(START_YEAR, END_YEAR, start_week, end_week, division, hfa, base_cors, timestamp)
            else:
                history_calc(START_YEAR, END_YEAR, start_week, end_week, division, hfa, base_cors, timestamp, snapshot_service)
        else:
            raise ValueError(f"Invalid calculation type: {calc_type}")
    except Exception as e:
        logging.error(f"Error during {calc_type} calculation: {str(e)}")
        raise

def main():
    # Recovery commands are intentionally explicit and offline-capable.  Keep
    # the historic positional calculation interface below for compatibility.
    if len(sys.argv) > 1 and sys.argv[1] in {"smoke", "fetch", "refresh", "build", "validate", "promote", "--help", "-h"}:
        try:
            from .recovery import main as recovery_main
        except ImportError:
            from recovery import main as recovery_main
        return recovery_main(sys.argv[1:])
    if len(sys.argv) == 1:
        logging.error(
            "stage=legacy_calculation cause=arguments: explicit calc_type/year/week required; "
            "use `python -m cfb.recovery --help` for staged recovery commands"
        )
        return 2
    # Legacy generators retain their historical direct-file interface.  Lazy
    # importing keeps recovery help/build/validation independent of those
    # modules' old absolute imports.
    try:
        from .calc import single_week_calc, full_season_calc, history_calc
        from .end_week import get_end_week
        from .html_grab import html_grab
        from .nc_wt_clean import process_rankings
    except ImportError:
        # The original generators use absolute imports while being run from
        # inside cfb/.  Add that directory only for this legacy path; recovery
        # commands above never import it.
        cfb_directory = str(Path(__file__).resolve().parent)
        if cfb_directory not in sys.path:
            sys.path.insert(0, cfb_directory)
        from calc import single_week_calc, full_season_calc, history_calc
        from end_week import get_end_week
        from html_grab import html_grab
        from nc_wt_clean import process_rankings
    globals().update(
        single_week_calc=single_week_calc,
        full_season_calc=full_season_calc,
        history_calc=history_calc,
    )
    start_time = time.time()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    
    # Legacy calculations are explicit by construction.  A no-argument call
    # is rejected above rather than guessing season/week from today's date.
    calc_type = sys.argv[1]
    if len(sys.argv) < 3:
        logging.error("stage=legacy_calculation cause=arguments: year is required")
        return 2
    year = int(sys.argv[2])
    week = int(sys.argv[3]) if len(sys.argv) > 3 else WEEK

    # if week == 0:
        # Get dynamic end week for html_grab
        # end_week = get_end_week(year)
        # html_grab(year, year, START_WEEK, end_week, DIVISION, timestamp)
    if calc_type == "full_season":
        logging.info(f"Starting {calc_type} calculation for year {year}")
    elif calc_type == "single_week":
        logging.info(f"Starting {calc_type} calculation for year {year}, week {week}")
    else:
        logging.info(f"Starting {calc_type} calculation for history {START_YEAR} - {END_YEAR}")
    
    category = "historical" if calc_type == "history" else "scheduled"
    snapshot_service = get_snapshot_service(category)
    run_calculations(calc_type, year, week, START_WEEK, DIVISION, HFA, BASE_CORS, timestamp, snapshot_service)
    
    if calc_type == "history":
        try:
            # Process both national champions and worst teams in one pass
            process_rankings(DIVISION, timestamp)
            # Get dynamic end week for html_grab
            end_week = get_end_week(END_YEAR, DIVISION, snapshot_service)
            html_grab(START_YEAR, END_YEAR, START_WEEK, end_week, DIVISION, timestamp)
        except Exception as e:
            logging.error(f"Error during additional processes: {str(e)}")
            raise
    

    
    logging.info(f"Process finished in {time.time() - start_time:.2f} seconds")

if __name__ == "__main__":
    try:
        raise SystemExit(main() or 0)
    except SystemExit:
        raise
    except Exception as exc:
        logging.error("stage=legacy_calculation cause=%s: %s", type(exc).__name__, exc)
        raise SystemExit(1)
