# database.py
import asyncpg
from datetime import datetime
from config import DATABASE_URL

db_pool = None

async def create_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

async def init_db():
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id BIGINT PRIMARY KEY,
                chat_type TEXT,
                first_name TEXT,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

async def add_chat(chat_id, chat_type, name):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO chats (chat_id, chat_type, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (chat_id) DO UPDATE SET first_name = $3
        """, chat_id, chat_type, name)

async def get_stats_data():
    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM chats WHERE chat_type = 'private'")
        groups = await conn.fetchval("SELECT COUNT(*) FROM chats WHERE chat_type != 'private'")
        new_today = await conn.fetchval("SELECT COUNT(*) FROM chats WHERE date_added > NOW() - INTERVAL '1 day'")
        db_size = await conn.fetchval("SELECT pg_size_pretty(pg_database_size(current_database()))")
        return users, groups, new_today, db_size

async def get_all_chats(target):
    async with db_pool.acquire() as conn:
        query = "SELECT chat_id FROM chats WHERE chat_type = 'private'" if target == "users" else "SELECT chat_id FROM chats WHERE chat_type != 'private'"
        return await conn.fetch(query)
