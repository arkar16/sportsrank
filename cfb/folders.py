import os
import config

os.chdir(config.owd) # puts program in cfb/years

start_year = 1901
end_year = 2011


data_folder = "data"
records_folder = f"{data_folder}/records"
results_folder = f"{data_folder}/results"
weekly_results_folder = f"{results_folder}/weekly_results"
slate_folder = f"{data_folder}/slate"
weekly_slate_folder = f"{slate_folder}/weekly_slate"
rankings_folder = "rankings"
spread_folder = "spread"

for year in range(start_year, end_year + 1):
    os.chdir(config.owd)
    os.mkdir(f"{year}")
    os.chdir(f"{year}")
    os.mkdir(f"{data_folder}")
    os.mkdir(f"{records_folder}")
    os.mkdir(f"{results_folder}")
    os.mkdir(f"{weekly_results_folder}")
    os.mkdir(f"{slate_folder}")
    os.mkdir(f"{weekly_slate_folder}")
    os.mkdir(f"{rankings_folder}")
    os.mkdir(f"{spread_folder}")
