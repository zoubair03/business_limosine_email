"""
Wipes the SQLite database and re-initializes fresh empty schema.
Run with:
    .\\venv\\Scripts\\python reset_database.py
"""
import os
from pathlib import Path
from config import Config
from database import init_db

def reset():
    db_file = Path(Config.DB_PATH)
    if db_file.exists():
        try:
            os.remove(db_file)
            print(f"Removed existing database at {db_file}")
        except Exception as e:
            print(f"Error removing {db_file}: {e}")
    
    init_db()
    print("Database successfully re-initialized with fresh empty schema!")

if __name__ == "__main__":
    reset()
