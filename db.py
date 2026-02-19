import psycopg2
from psycopg2 import pool
import json
from config import DATABASE_URL, OWNER_ID

# Initialize Connection Pool (Min: 1 connection, Max: 10 connections)
db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, DATABASE_URL)

def execute_query(query, params=(), fetch=False, fetchall=False):
    """Helper function to execute queries and manage connection pooling safely."""
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch:
                return cur.fetchone()
            if fetchall:
                return cur.fetchall()
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Database Error: {e}")
        raise e
    finally:
        db_pool.putconn(conn)

def init_db():
    execute_query('CREATE TABLE IF NOT EXISTS pending (item_id TEXT PRIMARY KEY, item_data TEXT)')
    execute_query('CREATE TABLE IF NOT EXISTS active (item_id TEXT PRIMARY KEY, item_data TEXT)')
    execute_query('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    execute_query('CREATE TABLE IF NOT EXISTS bot_admins (user_id BIGINT PRIMARY KEY)')
    
    # Insert default settings
    execute_query("INSERT INTO settings (key, value) VALUES ('submissions', 'on') ON CONFLICT (key) DO NOTHING")
    execute_query("INSERT INTO settings (key, value) VALUES ('bidding', 'on') ON CONFLICT (key) DO NOTHING")

# --- Pending Items ---
def add_pending(item_id, data):
    query = "INSERT INTO pending (item_id, item_data) VALUES (%s, %s) ON CONFLICT (item_id) DO UPDATE SET item_data = EXCLUDED.item_data"
    execute_query(query, (item_id, json.dumps(data)))

def get_pending(item_id):
    row = execute_query('SELECT item_data FROM pending WHERE item_id = %s', (item_id,), fetch=True)
    return json.loads(row[0]) if row else None

def delete_pending(item_id):
    execute_query('DELETE FROM pending WHERE item_id = %s', (item_id,))

# --- Active Auctions ---
def add_active(item_id, data):
    query = "INSERT INTO active (item_id, item_data) VALUES (%s, %s) ON CONFLICT (item_id) DO UPDATE SET item_data = EXCLUDED.item_data"
    execute_query(query, (item_id, json.dumps(data)))

def get_active(item_id):
    row = execute_query('SELECT item_data FROM active WHERE item_id = %s', (item_id,), fetch=True)
    return json.loads(row[0]) if row else None

def update_active(item_id, data):
    add_active(item_id, data)

def delete_active(item_id):
    execute_query('DELETE FROM active WHERE item_id = %s', (item_id,))

def get_all_active():
    rows = execute_query('SELECT item_data FROM active', fetchall=True)
    return [json.loads(row[0]) for row in rows]

def get_user_pending(user_id):
    rows = execute_query('SELECT item_data FROM pending', fetchall=True)
    items = [json.loads(row[0]) for row in rows]
    return [i for i in items if i['seller_id'] == user_id]

def get_user_active(user_id):
    rows = execute_query('SELECT item_data FROM active', fetchall=True)
    items = [json.loads(row[0]) for row in rows]
    return [i for i in items if i['seller_id'] == user_id]

# --- Global Settings ---
def get_setting(key):
    row = execute_query('SELECT value FROM settings WHERE key = %s', (key,), fetch=True)
    return row[0] if row else "on"

def set_setting(key, value):
    query = "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    execute_query(query, (key, value))

# --- Bot Admin Management ---
def is_bot_admin(user_id):
    if user_id == OWNER_ID:
        return True
    row = execute_query('SELECT 1 FROM bot_admins WHERE user_id = %s', (user_id,), fetch=True)
    return row is not None

def add_admin(user_id):
    execute_query('INSERT INTO bot_admins (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING', (user_id,))

def remove_admin(user_id):
    execute_query('DELETE FROM bot_admins WHERE user_id = %s', (user_id,))

def get_all_admins():
    rows = execute_query('SELECT user_id FROM bot_admins', fetchall=True)
    return [row[0] for row in rows]

# Initialize the database tables on startup
init_db()
