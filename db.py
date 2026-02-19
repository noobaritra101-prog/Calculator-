import asyncpg
import json
from config import DATABASE_URL, OWNER_ID

pool = None

async def init_db():
    global pool
    # Initialize connection pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    
    async with pool.acquire() as conn:
        await conn.execute('CREATE TABLE IF NOT EXISTS pending (item_id TEXT PRIMARY KEY, item_data TEXT)')
        await conn.execute('CREATE TABLE IF NOT EXISTS active (item_id TEXT PRIMARY KEY, item_data TEXT)')
        await conn.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        await conn.execute('CREATE TABLE IF NOT EXISTS bot_admins (user_id BIGINT PRIMARY KEY)')
        
        await conn.execute("INSERT INTO settings (key, value) VALUES ('submissions', 'on') ON CONFLICT (key) DO NOTHING")
        await conn.execute("INSERT INTO settings (key, value) VALUES ('auction', 'on') ON CONFLICT (key) DO NOTHING")

async def execute_query(query, *args, fetch=False, fetchall=False):
    async with pool.acquire() as conn:
        if fetch:
            return await conn.fetchrow(query, *args)
        if fetchall:
            return await conn.fetch(query, *args)
        return await conn.execute(query, *args)

# --- Pending Items ---
async def add_pending(item_id, data):
    query = "INSERT INTO pending (item_id, item_data) VALUES ($1, $2) ON CONFLICT (item_id) DO UPDATE SET item_data = EXCLUDED.item_data"
    await execute_query(query, item_id, json.dumps(data))

async def get_pending(item_id):
    row = await execute_query('SELECT item_data FROM pending WHERE item_id = $1', item_id, fetch=True)
    return json.loads(row[0]) if row else None

async def delete_pending(item_id):
    await execute_query('DELETE FROM pending WHERE item_id = $1', item_id)

# --- Active Auctions ---
async def add_active(item_id, data):
    query = "INSERT INTO active (item_id, item_data) VALUES ($1, $2) ON CONFLICT (item_id) DO UPDATE SET item_data = EXCLUDED.item_data"
    await execute_query(query, item_id, json.dumps(data))

async def get_active(item_id):
    row = await execute_query('SELECT item_data FROM active WHERE item_id = $1', item_id, fetch=True)
    return json.loads(row[0]) if row else None

async def update_active(item_id, data):
    await add_active(item_id, data)

async def delete_active(item_id):
    await execute_query('DELETE FROM active WHERE item_id = $1', item_id)

async def get_all_active():
    rows = await execute_query('SELECT item_data FROM active', fetchall=True)
    return [json.loads(row[0]) for row in rows]

async def get_user_pending(user_id):
    rows = await execute_query('SELECT item_data FROM pending', fetchall=True)
    items = [json.loads(row[0]) for row in rows]
    return [i for i in items if i['seller_id'] == user_id]

async def get_user_active(user_id):
    rows = await execute_query('SELECT item_data FROM active', fetchall=True)
    items = [json.loads(row[0]) for row in rows]
    return [i for i in items if i['seller_id'] == user_id]

# --- Global Settings ---
async def get_setting(key):
    row = await execute_query('SELECT value FROM settings WHERE key = $1', key, fetch=True)
    return row[0] if row else "on"

async def set_setting(key, value):
    query = "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    await execute_query(query, key, value)

# --- Bot Admin Management ---
async def is_bot_admin(user_id):
    if user_id == OWNER_ID:
        return True
    row = await execute_query('SELECT 1 FROM bot_admins WHERE user_id = $1', user_id, fetch=True)
    return row is not None

async def add_admin(user_id):
    await execute_query('INSERT INTO bot_admins (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING', user_id)

async def remove_admin(user_id):
    await execute_query('DELETE FROM bot_admins WHERE user_id = $1', user_id)

async def get_all_admins():
    rows = await execute_query('SELECT user_id FROM bot_admins', fetchall=True)
    return [row[0] for row in rows]
