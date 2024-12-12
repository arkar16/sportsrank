import pandas as pd
import os
import config
from games import get_weekly_results
from io import StringIO

def calculate_spread_result(predicted_spread_str, actual_home_score, actual_away_score):
    """Calculate if the spread prediction was correct"""
    try:
        # Extract spread value from string like "Alabama -7.5" or "Georgia +3"
        team_name, spread_str = predicted_spread_str.rsplit(' ', 1)
        spread_value = float(spread_str)
        
        # Calculate actual margin (positive means home team won by that much)
        if spread_str.startswith('-'):
            actual_margin = actual_home_score - actual_away_score
            ats_correct = actual_margin > abs(spread_value)
        else:
            actual_margin = actual_away_score - actual_home_score
            ats_correct = actual_margin < spread_value
            
        # Calculate if straight up winner prediction was correct
        # If spread is negative, home team was predicted to win
        # If spread is positive, away team was predicted to win
        predicted_home_win = spread_str.startswith('-')
        actual_home_win = (actual_home_score - actual_away_score) > 0
        su_correct = predicted_home_win == actual_home_win
            
        return ats_correct, su_correct
        
    except Exception as e:
        print(f"Error in calculate_spread_result: {e}")
        return None, None

def analyze_last_week_spreads(year, week, division, weekly_results, timestamp):
    """Analyze spread predictions from the previous week"""
    os.chdir(config.owd)
    
    # Get last week's spread predictions
    last_week = week - 1
    
    # Skip if trying to analyze week 0 results
    if last_week == 0:
        print("Skipping spread analysis for week 0")
        return
    
    try:
        spread_dir = f"{year}/spread"
        os.chdir(spread_dir)
        print(f"\nAnalyzing spreads for {year} Week {last_week}")
        
        # Read last week's spread predictions
        spread_file = f"{year}_W{last_week}_{division}_spread.html"
        #print(f"Reading spread predictions from: {spread_file}")
        
        # Properly read HTML file using StringIO
        with open(spread_file, 'r') as f:
            html_content = f.read()
        spread_df = pd.read_html(StringIO(html_content))[0]
        #print("\nSpread predictions loaded:")
        #print(spread_df.head())
        
        # Clean up spread dataframe
        spread_df = spread_df[['home_team', 'away_team', 'neutral_site', 'spread']]
        
        # Clean up results dataframe - keep only FBS vs FBS games
        results_df = weekly_results[
            (weekly_results['home_division'] == 'fbs') & 
            (weekly_results['away_division'] == 'fbs')
        ]
        #print("\nResults loaded:")
        #print(results_df.head())
        
        # Merge predictions with results
        analysis_df = pd.merge(
            spread_df,
            results_df[['home_team', 'away_team', 'home_score', 'away_score', 'neutral_site']],
            how='left',
            on=['home_team', 'away_team']
        )
        #print("\nMerged data:")
        #print(analysis_df.head())
        
        # Calculate predictions
        predictions = []
        for _, row in analysis_df.iterrows():
            if pd.notnull(row['home_score']):
                ats_result, su_result = calculate_spread_result(
                    row['spread'], 
                    row['home_score'], 
                    row['away_score']
                )
                predictions.append((ats_result, su_result))
            else:
                predictions.append((None, None))
        
        analysis_df['ats_correct'] = [p[0] for p in predictions]
        analysis_df['su_correct'] = [p[1] for p in predictions]
        
        # Calculate summary statistics
        total_games = len(analysis_df.dropna(subset=['home_score']))
        ats_correct = analysis_df['ats_correct'].sum()
        su_correct = analysis_df['su_correct'].sum()
        ats_accuracy = (ats_correct / total_games) * 100 if total_games > 0 else 0
        su_accuracy = (su_correct / total_games) * 100 if total_games > 0 else 0
        
        #print(f"\nAnalysis complete:")
        #print(f"Total games: {total_games}")
        #print(f"ATS correct: {ats_correct}")
        #print(f"SU correct: {su_correct}")
        
        # Create HTML report
        html_content = f"""
        <html>
        <head>
        <title>CORS {config.cors_version} - {year} W{last_week} Spread Results - {division}</title>
        </head>
        <body>
        <h1>CORS {config.cors_version} - {year} W{last_week} Spread Results - {division}</h1>
        <h2>Summary</h2>
        <p>Total Games: {total_games}</p>
        <p>Against The Spread:</p>
        <ul>
            <li>Correct Predictions: {ats_correct}</li>
            <li>Accuracy: {ats_accuracy:.1f}%</li>
        </ul>
        <p>Straight Up:</p>
        <ul>
            <li>Correct Predictions: {su_correct}</li>
            <li>Accuracy: {su_accuracy:.1f}%</li>
        </ul>
        <hr>
        <h2>Results</h2>
        {analysis_df.to_html(index=False)}
        <hr>
        Last updated: {timestamp}
        </body>
        </html>
        """
        
        # Save results in the spread directory
        results_file = f"{year}_W{last_week}_{division}_spread_results.html"
        print(f"\nSaving results to: {results_file}")
        with open(results_file, "w") as f:
            f.write(html_content)
            
        print(f"Spread results analysis complete for Week {last_week}")
        print(f"Week {last_week} ATS Accuracy: {ats_accuracy:.1f}%")
        print(f"Week {last_week} SU Accuracy: {su_accuracy:.1f}%")
        
        # Append results to spread_results file
        os.chdir(config.owd)
        spread_results_file = "spread_analysis.csv"
        with open(spread_results_file, 'a') as f:
            f.write(f"{year},{last_week},{ats_accuracy:.1f},{ats_correct},{su_accuracy:.1f},{su_correct},{total_games},{config.cors_version}\n")
        
    except Exception as e:
        print(f"Error analyzing spreads: {str(e)}")
        print(f"No spreads to analyse for {year} Week {last_week}")
        #raise  # Re-raise to see full traceback
    finally:
        os.chdir(config.owd)

if __name__ == "__main__":
    # Example usage
    import sys
    if len(sys.argv) > 3:
        year = int(sys.argv[1])
        week = int(sys.argv[2])
        division = sys.argv[3]
        timestamp = sys.argv[4] if len(sys.argv) > 4 else None
        
        # Get weekly results
        weekly_results = get_weekly_results(year, week-1, division, timestamp)
        analyze_last_week_spreads(year, week, division, weekly_results, timestamp)
    else:
        print("Usage: python spread_results.py <year> <current_week> <division> [timestamp]")
