import sqlite3
import json

def get_conn():
    return sqlite3.connect('auctions.db')

def init_db():
    with get_conn() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS pending (item_id TEXT PRIMARY KEY, item_data TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS active (item_id TEXT PRIMARY KEY, item_data TEXT)')

def add_pending(item_id, data):
    with get_conn() as conn:
        conn.execute('INSERT OR REPLACE INTO pending VALUES (?, ?)', (item_id, json.dumps(data)))

def get_pending(item_id):
    with get_conn() as conn:
        cur = conn.execute('SELECT item_data FROM pending WHERE item_id = ?', (item_id,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

def delete_pending(item_id):
    with get_conn() as conn:
        conn.execute('DELETE FROM pending WHERE item_id = ?', (item_id,))

def add_active(item_id, data):
    with get_conn() as conn:
        conn.execute('INSERT OR REPLACE INTO active VALUES (?, ?)', (item_id, json.dumps(data)))

def get_active(item_id):
    with get_conn() as conn:
        cur = conn.execute('SELECT item_data FROM active WHERE item_id = ?', (item_id,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

def update_active(item_id, data):
    add_active(item_id, data)

def get_all_active():
    with get_conn() as conn:
        cur = conn.execute('SELECT item_data FROM active')
        return [json.loads(row[0]) for row in cur.fetchall()]

def get_user_pending(user_id):
    with get_conn() as conn:
        cur = conn.execute('SELECT item_data FROM pending')
        items = [json.loads(row[0]) for row in cur.fetchall()]
        return [i for i in items if i['seller_id'] == user_id]

def get_user_active(user_id):
    with get_conn() as conn:
        cur = conn.execute('SELECT item_data FROM active')
        items = [json.loads(row[0]) for row in cur.fetchall()]
        return [i for i in items if i['seller_id'] == user_id]

# Initialize the database when this file is imported
init_db()
