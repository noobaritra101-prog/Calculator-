import logging
import asyncio
import math
import time
import asyncpg
from datetime import datetime
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================= CONFIG =================

API_ID = 123456  # your api_id
API_HASH = "your_api_hash"
BOT_TOKEN = "8368723938:AAGjgK3u0mkmcLw8D7Az511d29S1bXRm86Y"

ADMIN_IDS = [5716292610, 7708811819]

DATABASE_URL = "postgresql://axb:h_9dhhH5KF_5c0xumLMziA@axb-bots-12453.jxf.gcp-asia-south1.cockroachlabs.cloud:26257/defaultdb?sslmode=require"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client(
    "calculator_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

db_pool = None
ram_chats = {}
dirty_chats = set()

# ================= DATABASE =================

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
                date_added TEXT
            )
        """)

def add_chat(chat_id, chat_type, first_name):
    if chat_id not in ram_chats:
        ram_chats[chat_id] = (chat_type, first_name)
        dirty_chats.add(chat_id)

async def sync_to_db():
    while True:
        if dirty_chats:
            async with db_pool.acquire() as conn:
                for chat_id in list(dirty_chats):
                    data = ram_chats.get(chat_id)
                    if data:
                        chat_type, name = data
                        await conn.execute("""
                            INSERT INTO chats (chat_id, chat_type, first_name, date_added)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (chat_id) DO NOTHING
                        """, chat_id, chat_type, name,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        dirty_chats.discard(chat_id)
        await asyncio.sleep(30)

async def get_stats_data():
    async with db_pool.acquire() as conn:
        users = await conn.fetchval(
            "SELECT COUNT(*) FROM chats WHERE chat_type = 'private'"
        )
        groups = await conn.fetchval(
            "SELECT COUNT(*) FROM chats WHERE chat_type != 'private'"
        )
    return users, groups

# ================= CALCULATOR =================

def safe_calculate(expression):
    expression = expression.replace("^", "**")

    if not any(c.isdigit() for c in expression):
        return None

    safe_dict = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
    safe_dict["abs"] = abs
    safe_dict["round"] = round

    try:
        result = eval(expression, {"__builtins__": {}}, safe_dict)
        return str(result)
    except:
        return None

# ================= START =================

@app.on_message(filters.command("start"))
async def start_handler(client, message):
    chat = message.chat
    add_chat(chat.id, chat.type, chat.title or chat.first_name)

    # Added colored button styles: primary (blue), success (green), danger (red)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Me To Group", url=f"https://t.me/{client.me.username}?startgroup=true", style="primary")],
        [
            InlineKeyboardButton("💬 Support", url="https://t.me/axbsupport", style="success"),
            InlineKeyboardButton("🏢 HQ", url="https://t.me/your_channel", style="success")
        ],
        [InlineKeyboardButton("👑 Owner", url="https://t.me/axbowners", style="danger")]
    ])

    await message.reply_text(
        f"Hҽყ {message.from_user.first_name}\n"
        "Wᴇʟᴄσɱᴇ ᴛσ Tʜᴇ Cᴀʟᴄᴜʟᴀᴛᴏʀ Bᴏᴛ\n\n"
        "⚡ Pᴏᴡᴇʀᴇᴅ ʙʏ AｘB Bᴏᴛs",
        reply_markup=keyboard
    )

# ================= STATS =================

@app.on_message(filters.command("stats"))
async def stats_handler(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return

    users, groups = await get_stats_data()

    # Styled the refresh button to be blue
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats", style="primary")]
    ])

    await message.reply_text(
        f"📊 System Status\n\n"
        f"👥 Users: {users}\n"
        f"🌐 Groups: {groups}",
        reply_markup=keyboard
    )

@app.on_callback_query(filters.regex("refresh_stats"))
async def refresh_callback(client, callback_query):
    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("Not admin!", show_alert=True)
        return

    users, groups = await get_stats_data()

    await callback_query.message.edit_text(
        f"📊 System Status\n\n"
        f"👥 Users: {users}\n"
        f"🌐 Groups: {groups}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats", style="primary")]
        ])
    )

# ================= CALCULATOR MESSAGE =================

@app.on_message(filters.text & ~filters.command(["start", "stats"]))
async def calculator_handler(client, message):
    chat = message.chat
    add_chat(chat.id, chat.type, chat.title or chat.first_name)

    result = safe_calculate(message.text)
    if result:
        await message.reply_text(result)

# ================= RUN =================

async def main():
    await create_pool()
    await init_db()
    asyncio.create_task(sync_to_db())
    await app.start()
    print("Bot running...")
    await idle()

if __name__ == "__main__":
    asyncio.run(main())
