import os
import config
import pandas as pd

def get_end_week(year, division="FBS", snapshot_service=None):
    # get original working directory
    os.chdir(config.owd)
    
    if snapshot_service is not None:
        snapshot = snapshot_service.get(year, division)
        end_week = max((game.week for game in snapshot.games), default=0)
    else:
        # Compatibility path for legacy generated archives; it performs no CFBD I/O.
        slate = pd.read_html(
            f"{year}/data/slate/{year}_{division}_slate.html"
        )[0]
        end_week = slate["week"].max()
    
    # Cap at config.week_count if needed
    if end_week > config.week_count:
        end_week = config.week_count
        
    return end_week
