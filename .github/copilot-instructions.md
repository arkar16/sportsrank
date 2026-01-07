# SportsRank Development Guide

## Project Overview

SportsRank is a sports ranking system that calculates **CORS (Composite Opponent Rating System)** rankings for college football (CFB) and generates game spread predictions. Historical rankings span from 1897 to present, with outputs published to a Firebase-hosted website at sportsrank.top.

### Core Architecture

- **CFB module** (`cfb/`): Main ranking engine implementing CORS algorithm
- **Website** (`website/`): Static HTML output organized by sport → year → week
- **Data flow**: CFBD API → Game data → CORS calculation → Rankings/Spreads → HTML generation

## CORS Algorithm Structure

**CORS** = Weighted composite of win percentage (50 pts max), margin of victory (with logarithmic diminishing returns), and strength of schedule, combined with previous week's rating for stability.

Key components in [`cfb/cors.py`](cfb/cors.py):
- `cors_calc()`: Main CORS calculation for a single team
- `current_sos()`: Strength of schedule based on opponent CORS ratings
- `margin_of_victory()`: Average point differential with diminishing returns via `log10`
- `weekly_cors()`: Batch calculation for all teams in a week

## Critical Workflows

### Running Rankings

**Primary entry point**: [`cfb/main.py`](cfb/main.py)

```bash
# From cfb/ directory, with venv activated:
python main.py [calc_type] [year] [week]

# Examples:
python main.py single_week 2025 5    # Calculate week 5 only
python main.py full_season 2025      # Calculate entire 2025 season
python main.py history               # Recalculate historical range (uses START_YEAR/END_YEAR constants)
```

**Calculation flow** ([`cfb/calc.py`](cfb/calc.py)):
1. Week 0: Initialize teams → Copy previous year's final rankings OR create blank template (1897) → Readjust for roster changes
2. Regular weeks: Fetch games → Calculate records → Run CORS → Generate spreads → Analyze previous week's accuracy
3. Final week: Skip spread generation for next week

### Week Zero Special Case

Week 0 uses previous season's final rankings as baseline, adjusted via [`readjust.py`](cfb/readjust.py) to account for new/departed teams. The 1897 season (first year) creates a blank slate instead of copying prior year.

### Output Structure

All outputs go to `website/cfb/years/{YEAR}/`:
- `data/`: Raw API data (teams, games, records)
- `rankings/`: Weekly CORS rankings (`{YEAR}_W{WEEK}_FBS_cors.html`)
- `spread/`: Game predictions and accuracy analysis

**Path configuration** lives in [`cfb/config.py`](cfb/config.py):
- `owd` (output working directory) should point to `website/cfb/years/`
- Most modules use `os.chdir(config.owd)` before file ops; prefer joining paths rather than relying on cwd
- Cross‑platform tip: avoid hardcoded absolute paths (macOS) found in `config.py` and [`mainpage.py`](mainpage.py); use `pathlib.Path`/`os.path.join` and an environment override (e.g., `SPORTSRANK_OWD`)

## Key Patterns & Conventions

### Data Sources
- **CFBD API**: Primary data via `cfbd` Python package ([`cfb/api.py`](cfb/api.py) contains API key)
- API calls in [`teams.py`](cfb/teams.py), [`games.py`](cfb/games.py), [`records.py`](cfb/records.py)
- FCS teams (non-FBS opponents) assigned constant CORS value of `-10` (see `fcs_constant` in `config.py`)

### Constants & Configuration
- [`cfb/config.py`](cfb/config.py): CORS version, sport type, working directory, FCS constant, week count
- [`webconfig.py`](webconfig.py): Website-level settings (sports list, CORS version)
- [`cfb/main.py`](cfb/main.py): Season parameters (YEAR, START_YEAR, END_YEAR, HFA=2 for home field advantage)

### HTML Generation
- All outputs are HTML tables generated via `pandas.to_html()` with custom headers
- [`html_grab.py`](cfb/html_grab.py): Creates year index pages linking to all weekly rankings
- [`nc_wt_clean.py`](cfb/nc_wt_clean.py): Processes national champions and worst teams into history pages with jQuery DataTables

### Spread Calculation
- [`spread.py`](cfb/spread.py): `spread_calc()` uses formula: `(home_cors + HFA - away_cors)`, rounded to nearest 0.5
- Neutral site games exclude HFA (Home Field Advantage = 2 points)
- [`spread_results.py`](cfb/spread_results.py): Tracks against-the-spread (ATS) and straight-up (SU) accuracy

## Common Development Tasks

### Adding a New Sport
1. Create module directory (e.g., `nfl/`) with similar structure to `cfb/`
2. Update [`webconfig.py`](webconfig.py) `sports` list
3. Implement sport-specific API calls and season structure
4. Run [`mainpage.py`](mainpage.py) to generate updated index

### Updating CORS Algorithm
- Modify [`cfb/cors.py`](cfb/cors.py) `cors_calc()` function
- Increment `cors_version` in both [`config.py`](cfb/config.py) and [`webconfig.py`](webconfig.py)
- Rerun history calculations: `python main.py history`

### Creating Year Folders
Run [`folders.py`](cfb/folders.py) to create directory structure for new years (update `start_year`/`end_year` variables first)

## Dependencies & Environment

- **Python 3.12+** required (see [`pyproject.toml`](pyproject.toml))
- Key packages: `cfbd`, `pandas`, `beautifulsoup4`, `numpy<2.0`
- Virtual environment: `.venv/` (activate before running scripts)
- Firebase hosting configured via [`firebase.json`](firebase.json) pointing to `website/` public directory

## Important Notes

- **No automated tests** exist - validate rankings manually by inspecting HTML output
- **Path assumptions**: Code expects to run from `cfb/` directory; uses `os.chdir()` extensively
- **Week numbering**: 0-indexed (Week 0 = preseason, Week 1 = first games)
- **Dynamic end week**: [`end_week.py`](cfb/end_week.py) determines season length from game data (caps at 15)
- **Pythagorean expectation**: [`pyth.py`](cfb/pyth.py) calculates expected wins using 2.37 exponent for comparison to actual record

## Cross‑Platform Paths

- Prefer `pathlib.Path` or `os.path.join` for building paths; do not embed absolute user paths.
- Centralize output root in `cfb/config.py`:
	- Recommended: read `SPORTSRANK_OWD` env var; else default to repo‑relative `website/{sport}/years`.
- Avoid relying on `os.chdir()`; pass explicit paths to `open()` and pandas IO where feasible.
- Windows quick start:
	- Activate venv: `PowerShell` → `.venv\Scripts\Activate.ps1`
	- Run from `cfb/`: `python main.py single_week 2025 1`

## Secrets Handling

- `cfb/api.py` is ignored via `.gitignore` (do not commit). Keep the CFBD key local.
- Suggested runtime pattern: load from `CFBD_API_KEY` environment variable in `cfb/api.py`, with optional local fallback. Do not add secrets to the repo.

## CFBD v2 API Migration (Next Major Step)

- Scope: update data access layer to v2 format across `cfb/teams.py`, `cfb/games.py`, `cfb/records.py`, and `cfb/api.py`.
- Actions:
	- Upgrade `cfbd` client in `pyproject.toml` to a version compatible with v2.
	- Adjust request parameters and response parsing to new schemas (teams, schedules, results).
	- Normalize weekly dataframes so downstream (`cors.py`, `spread.py`, `spread_results.py`) receive consistent columns: `home_team`, `away_team`, `home_score`, `away_score`, `week`, `neutral_site`, `division`.
	- Verify `end_week.py` continues to derive season length correctly from the v2 slate.
- Output contracts: keep HTML and CSV shapes stable under `website/cfb/years/{YEAR}/` to avoid breaking published pages.

## Notes for AI Agents

- Run jobs from `cfb/` and rely on the documented entrypoints; do not modify ignore rules for secrets.
- When touching paths, prefer platform‑neutral constructs and avoid changing file layout or public names.
- For v2 migration, limit refactors to the data fetch/parse layer; do not alter CORS formulas unless explicitly requested.
