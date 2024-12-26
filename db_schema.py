import sqlite3

def create_database():
    conn = sqlite3.connect('sportsrank.db')
    cursor = conn.cursor()

    # Teams table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        division TEXT NOT NULL,
        year INTEGER NOT NULL,
        logo_url TEXT,
        UNIQUE(name, year)
    )
    ''')

    # Games table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY,
        year INTEGER NOT NULL,
        week INTEGER NOT NULL,
        home_team_id INTEGER NOT NULL,
        away_team_id INTEGER NOT NULL,
        home_score INTEGER,
        away_score INTEGER,
        game_date DATE,
        FOREIGN KEY (home_team_id) REFERENCES teams (id),
        FOREIGN KEY (away_team_id) REFERENCES teams (id)
    )
    ''')

    # Rankings table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS rankings (
        id INTEGER PRIMARY KEY,
        team_id INTEGER NOT NULL,
        year INTEGER NOT NULL,
        week INTEGER NOT NULL,
        rank INTEGER NOT NULL,
        cors_rating REAL NOT NULL,
        record TEXT NOT NULL,
        win_pct REAL,
        timestamp DATETIME NOT NULL,
        FOREIGN KEY (team_id) REFERENCES teams (id),
        UNIQUE(team_id, year, week)
    )
    ''')

    # Spreads table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS spreads (
        id INTEGER PRIMARY KEY,
        game_id INTEGER NOT NULL,
        predicted_spread REAL NOT NULL,
        timestamp DATETIME NOT NULL,
        FOREIGN KEY (game_id) REFERENCES games (id)
    )
    ''')

    conn.commit()
    conn.close()

if __name__ == "__main__":
    create_database()
    #print("Database schema created successfully!")
