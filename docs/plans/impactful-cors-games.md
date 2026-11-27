# Most Impactful CORS Games Pages (CFB & CBB)

**Status:** Parked — waiting on `feat/dynamic-sqlite-web` (SQLite + server-rendered web migration, ADR-0033) tracked in [Assess SportsRank HTML Link Structure](t3-thread://v2/9ccec8bd-f4be-4f24-816e-751e0a0c3e00/9587f48d-f239-4b86-b9ce-908406bfe31d).

Once the dynamic SQLite site lands on `main`, implement these two pages directly in `web/` backed by `sportsrank.sqlite3` rather than adding more generated pages to the retired static `website/` HTML tree. Serving from SQLite also unlocks clean filtering by season, era, team, and conference with zero static-build overhead.

---

## Goal

Add two dedicated record-book pages — one for **College Football** (`/cfb/games`) and one for **Men’s College Basketball** (`/cbb/games`) — highlighting the most impactful games in CORS history across both **Current Season** and **All-Time** views, cross-linked to each other and to the existing all-time BEV/AEV excitement leaderboards (`alltime_excitement.py`).

---

## Verified Model & Data Baseline (`origin/main` @ `9a994513ee`)

- **College Football (`cfb/cors_v1.py`, [ADR-0029](../adr/0029-adopt-kalman-two-scale-football-cors-v1.md))**:
  - **CORS v1.0.0** two-timescale Bayesian state-space Kalman filter (`kalman-2scale (+Other)`).
  - 130 seasons (1897–2026), 83,217 scored games (83,206 with exact Kalman `home_cors_delta` / `away_cors_delta` via `cfb/football_impact.py`).
  - Adaptive league-wide HFA (`hfa_init = 3.0`, memory `0.65`; no per-team HFA in football v1.0.0).
  - Learned latent tier nodes for `FCS` (2003+, `-26.0` init) and `Other` (`-22.0` init); non-FBS games have no published pregame spread (`home_handicap = None`).
  - BEV/AEV live across 85,170 games with calibrated A+–F grades (`alltime_excitement.py`, `excitement_grades.py`).

- **Men’s Basketball (`cbb/cors_v1.py`, [ADR-0030](../adr/0030-adopt-kalman-two-scale-basketball-cors-v1.md) & [ADR-0031](../adr/0031-weight-basketball-outcomes-by-pregame-spread.md))**:
  - **CORS v1.1.0** two-timescale Bayesian state-space Kalman filter with pregame-spread-weighted game credit (`L = 400 / (400 + d²)`).
  - 79 seasons (1948–49 through 2026–27) from CollegeBasketballData.
  - Adaptive league HFA plus per-team custom home-court effects (`HFA_SHRINK = 40.0`, `HFA_TEAM_MEM = 0.95`, shrunk and zero-centered).
  - Exact per-game Kalman impacts (`home_cors_delta` / `away_cors_delta`) computed in `cbb/cors_v1.py:_update()`.
  - BEV/AEV live across 285,733 games with calibrated A+–F grades.

---

## Owner-Aligned Page Blueprint

Each sport page (`/cfb/games` and `/cbb/games`) shows **Current Season** (using latest checkpoint ratings) and **All-Time** (using `FINAL` ratings for completed seasons), organized into four core sections:

### 1. Best Wins
- **Formula:** `opp_FINAL_CORS + 4 * log10(1 + MOV)` (using the opponent's latest CORS rating for the active season).
- **Why this formula:**
  - Combines *who you beat* with *dominance* using a diminishing-returns log curve (`+1.20` for a 1-pt win, `+4.17` for 10 pts, `+5.29` for 20 pts, `+6.45` for 40 pts), so opponent strength stays primary while emphatic wins over elite teams rise above 1-point squeakers.
  - Deliberately omits a historical venue bonus because legacy CFBD bowl records sometimes carry `neutral_site = False` on neutral bowls (e.g., 2004 USC–Oklahoma, 2006 Florida–Ohio State).
  - Displays each game's exact Kalman `+ΔCORS` as a context column (consistent with conference pages in PR #186).
- **Why not rank all-time Best Wins purely by raw Kalman `+ΔCORS`?**
  - Empirical check on CFB v1.0.0 showed raw `+ΔCORS` is dominated by **first-year FBS entrants in Week 0–1** (e.g., 2012 Texas State `+11.97`, 2022 JMU `+11.38`, 2018 Liberty `+10.22`) because new entrants start with high prior variance (`var = 144.0`). Raw `±ΔCORS` is still valuable as a column or optional 5th "Biggest Rating Swings" view.

### 2. Worst Losses (Settled `FINAL` CORS Gap)
- **Formula:** `loser_FINAL_CORS - winner_FINAL_CORS + HFA` (using season `home_advantage` on non-neutral sites, `0` on neutral sites; latest rating for active seasons).
- **Scope:** Strictly **FBS-vs-FBS** (CFB) and **D-I-vs-D-I** (CBB), where both teams have individual CORS ratings. Displays Kalman `−ΔCORS` as context.
- **Why this works:** Captures "worst loss in hindsight" once the full season reveals true team strength — surfacing early-season disasters that stayed disasters (e.g., 2024 #2 Notre Dame losing to #82 NIU; 2020 Week 1 #15 Kentucky losing to #284 Evansville) while filtering out early-week mirages between evenly matched teams.
- **CFB-only companion table — Non-FBS losses by highest-rated FBS teams:**
  - Ranked purely by the **FBS loser's `FINAL` CORS rating** (no synthetic FCS spread or `-10.0` / `-26.0` distortion).
  - Preserves iconic CFB upsets (2010 #11 Virginia Tech vs. JMU, 2013 #21 Kansas State vs. NDSU, 2007 #17 Michigan vs. App State) honestly without skewing the main FBS-vs-FBS leaderboard.

### 3. Biggest Pregame CORS Upsets
- **Formula:** Pregame `Model Spread` favoring the losing team (`CORS Upset` in `CONTEXT.md`).
- **Key empirical finding under CORS v1.0.0 / v1.1.0:** **No minimum-games-played cutoff is needed.**
  - Under the retired v0.4.0 model, Week 2 games produced artificial 35–39 pt spreads (`1–0` vs. `0–1`).
  - Under Kalman v1.0.0 / v1.1.0, preseason carryover is smooth and **every top-10 all-weeks upset in both sports is genuine**. Adding a min-games cutoff would wrongly drop iconic upsets like 2020 Stephen F. Austin over #1 Duke (4g/6g), 2018 Old Dominion over #17 Virginia Tech (3g/2g), and 2021 Bowling Green over Minnesota (3g/3g).

### 4. Marquee Matchups
- **Formula:** Combined `FINAL` CORS (`winner_FINAL_CORS + loser_FINAL_CORS`).
- **Relationship to BEV / AEV:** Complementary, not duplicate. BEV measures pregame appeal (quality + expected closeness) and AEV measures postgame drama (lead changes, comebacks, late suspense); Marquee Matchups isolates **pure team-quality clashes** regardless of whether the game was close or a blowout (e.g., 2018 #1 Clemson 44–16 #2 Alabama).
- **Integration:** Link each matchup to its existing excitement guide anchor (`#fixture-...` / `#game-...`) and display its BEV/AEV scores and letter grades alongside.

---

## Verified CORS v1.0.0 / v1.1.0 Sanity Benchmarks

Useful as ground-truth spot checks when implementing against `sportsrank.sqlite3`:

### College Football (v1.0.0, 2000–2025/2026 sample)
- **Best Wins (`opp_FINAL_CORS + 4·log10(1 + MOV)`):**
  1. 2019 W18: #3 Clemson def. #1 Ohio State (`+37.63`) 29–23 → `41.01`
  2. 2019 W21: #2 LSU def. #3 Clemson (`+34.65`) 42–25 → `39.67`
  3. 2021 W14: #2 Alabama def. #1 Georgia (`+34.46`) 41–24 → `39.48`
  4. 2018 W20: #1 Clemson def. #2 Alabama (`+33.23`) 44–16 → `39.08`
  5. 2008 W20: #1 Florida def. #2 Oklahoma (`+32.83`) 24–14 → `37.00`
- **Worst Losses (`FINAL` gap, FBS-vs-FBS):**
  1. 2024 W2: #2 Notre Dame (`+27.55`) lost to #82 Northern Illinois (`-4.37`) 14–16 → `34.92`
  2. 2021 W4: #23 Minnesota (`+11.41`) lost to #123 Bowling Green (`-20.11`) 10–14 → `34.52`
  3. 2016 W2: #15 Oklahoma State (`+15.24`) lost to #101 Central Michigan (`-11.33`) 27–30 → `29.57`
  4. 2007 W6: #2 USC (`+23.11`) lost to #73 Stanford (`-3.24`) 23–24 → `29.35`
- **Non-FBS Losses (highest `FINAL` FBS CORS):**
  1. 2010 W2: #11 Virginia Tech (`+17.85`) lost to James Madison 16–21
  2. 2013 W1: #21 Kansas State (`+15.09`) lost to North Dakota State 21–24
  3. 2007 W1: #17 Michigan (`+12.45`) lost to App State 32–34
- **Pregame CORS Upsets (all weeks):**
  1. 2021 W11: #123 Kansas def. #21 Texas 57–56 (`36.47` underdog)
  2. 2019 W8: #101 Illinois def. #3 Wisconsin 24–23 (`35.47`)
  3. 2000 W12: #114 Central Michigan def. #26 Western Michigan 21–17 (`35.42`)
  4. 2018 W4: #126 Old Dominion def. #17 Virginia Tech 49–35 (`35.21`)
- **Marquee Matchups (combined `FINAL` CORS):**
  1. 2019 W18: #3 Clemson (`+34.65`) def. #1 Ohio State (`+37.63`) 29–23 → `+72.28`
  2. 2019 W21: #2 LSU (`+36.26`) def. #3 Clemson (`+34.65`) 42–25 → `+70.91`
  3. 2020 W20: #1 Alabama (`+41.36`) def. #2 Ohio State (`+29.09`) 52–24 → `+70.45`
  4. 2005 W19: #1 Texas (`+35.50`) def. #2 USC (`+34.28`) 41–38 → `+69.78`

### Men’s Basketball (v1.1.0, 2015–2026/2027 sample)
- **Best Wins (`opp_FINAL_CORS + 4·log10(1 + MOV)`):**
  1. 2026 W18: #9 Purdue def. #1 Michigan (`+27.34`) 80–72 → `31.16`
  2. 2024 W15: #12 Creighton def. #1 UConn (`+25.70`) 85–66 → `30.90`
  3. 2026 W21: #1 Michigan def. #3 Arizona (`+25.72`) 91–73 → `30.84`
  4. 2024 W6: #38 Seton Hall def. #1 UConn (`+25.70`) 75–60 → `30.52`
  5. 2026 W15: #2 Duke def. #1 Michigan (`+27.34`) 68–63 → `30.45`
- **Worst Losses (`FINAL` gap, D-I-vs-D-I):**
  1. 2023 W6: #37 Iowa (`+11.99`) lost to #346 Eastern Illinois (`-12.10`) 83–92 → `27.59`
  2. 2023 W7: #36 Michigan (`+12.06`) lost to #331 Central Michigan (`-10.30`) 61–63 → `25.86`
  3. 2021 W2: #49 Oregon State (`+10.06`) lost to #335 Portland (`-12.03`) 86–87 → `25.59`
  4. 2020 W1: #15 Kentucky (`+14.45`) lost to #284 Evansville (`-7.18`) 64–67 → `25.13`
- **Pregame CORS Upsets (all weeks):**
  1. 2023 W6: Eastern Illinois def. Iowa 92–83 (`32.06` underdog)
  2. 2016 W0: Western Illinois def. Wisconsin 69–67 (`31.37`)
  3. 2020 W8: Florida A&M def. Iowa State 70–68 (`27.76`)
  4. 2020 W3: Stephen F. Austin def. Duke 85–83 (`27.64`)
- **Marquee Matchups (combined `FINAL` CORS):**
  1. 2026 W15: #2 Duke (`+26.37`) def. #1 Michigan (`+27.34`) 68–63 → `+53.71`
  2. 2026 W21: #1 Michigan (`+27.34`) def. #3 Arizona (`+25.72`) 91–73 → `+53.06`
  3. 2025 W21: #3 Houston (`+23.90`) def. #1 Duke (`+26.58`) 70–67 → `+50.48`
  4. 2026 W4: #2 Duke (`+26.37`) def. #4 Florida (`+23.78`) 67–66 → `+50.15`

---

## Execution Plan (Once `feat/dynamic-sqlite-web` Merges)

1. **Rebase onto `main`** after ADR-0033 (`feat/dynamic-sqlite-web`) merges.
2. **Confirm remaining minor choices:**
   - Whether to include an optional 5th **"Biggest Single-Game Rating Swings (`±ΔCORS`)"** table alongside the 4 core sections.
   - Default table length (e.g., top 20–25) and query filters to expose on the dynamic pages (season / era / team / conference).
3. **Record a short ADR** formalizing the Best Win (`opp_FINAL_CORS + 4·log10(1 + MOV)`), Worst Loss (`FINAL` gap + CFB non-FBS companion), Pregame Upset, and Marquee Matchup definitions.
4. **Schema & query support in `web/` + SQLite exporter:**
   - Ensure `sportsrank.sqlite3` indexes/exposes each game's `home_score`, `away_score`, `home_handicap`, `neutral_site`, `home_division`, `away_division`, `home_cors_delta`, `away_cors_delta`, team `FINAL` (and latest) ratings/ranks, season `home_advantage`, and excitement anchor + BEV/AEV grades.
   - Precompute or query the four leaderboards cleanly for both `cfb` and `cbb`.
5. **Routes & navigation:**
   - Add `/cfb/games` (with `/cfb/games/index.html` alias if needed for consistency) and `/cbb/games`, linked from both homepages' Record Book sections and cross-linked to each other and `/excitement/`.
6. **Tests:**
   - Focused unit and route tests covering exact metric arithmetic, tie handling, active-season vs. `FINAL` separation, CFB non-FBS companion table, CBB D-I parity, and BEV/AEV links.
