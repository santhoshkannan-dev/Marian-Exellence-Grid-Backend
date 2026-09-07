import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def setup_postgres():
    print("Checking PostgreSQL setup for Marian Best Class Evaluation system...")
    
    db_host = os.environ.get('DATABASE_HOST', 'localhost')
    db_port = os.environ.get('DATABASE_PORT', '5432')
    db_user = os.environ.get('DATABASE_USER', 'postgres')
    db_pass = os.environ.get('DATABASE_PASSWORD', 'postgres')
    db_name = os.environ.get('DATABASE_NAME', 'marian_best_class')
    
    print(f"Connecting to PostgreSQL server at {db_host}:{db_port} as user '{db_user}'...")
    try:
        conn = psycopg2.connect(
            dbname='postgres',
            user=db_user,
            password=db_pass,
            host=db_host,
            port=db_port
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Check if database exists
        cursor.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s;", (db_name,))
        exists = cursor.fetchone()
        if not exists:
            print(f"Database '{db_name}' does not exist. Creating database...")
            cursor.execute(f'CREATE DATABASE "{db_name}";')
            print(f"Database '{db_name}' created successfully.")
        else:
            print(f"Database '{db_name}' already exists.")
            
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"\n[ERROR] Unable to connect to PostgreSQL server: {e}")
        print("\nPlease ensure:")
        print("1. PostgreSQL server is installed and running on your system.")
        print(f"2. Connection parameters in backend/.env are correct (Host: {db_host}, Port: {db_port}, User: {db_user}).")
        return False

if __name__ == '__main__':
    # Load backend/.env if present
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_file):
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
                    
    success = setup_postgres()
    if not success:
        sys.exit(1)
