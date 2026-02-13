import logging
import math
import time
import asyncio
import asyncpg
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters
)

# --- CONFIGURATION ---

ADMIN_IDS = [5716292610, 7708811819]
TOKEN = "8368723938:AAGjgK3u0mkmcLw8D7Az511d29S1bXRm86Y"

DATABASE_URL = "postgresql://axb:h_9dhhH5KF_5c0xumLMziA@axb-bots-12453.jxf.gcp-asia-south1.cockroachlabs.cloud:26257/defaultdb?sslmode=require"

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- GLOBALS ---
db_pool = None
ram_chats = {}
dirty_chats = set()

# ================= DATABASE =================

async def init_db():
    """Initializes the database table."""
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id BIGINT PRIMARY KEY,
                chat_type TEXT,
                first_name TEXT,
                date_added TEXT
            )
        """)

async def create_pool():
    """Creates the database connection pool."""
    global db_pool
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10
    )

def add_chat(chat_id, chat_type, first_name):
    """Adds a chat to the RAM buffer."""
    if chat_id not in ram_chats:
        ram_chats[chat_id] = (chat_type, first_name)
        dirty_chats.add(chat_id)

async def sync_to_db():
    """Background task to sync RAM buffer to Database."""
    logger.info("Background DB sync started.")
    while True:
        try:
            if dirty_chats:
                async with db_pool.acquire() as conn:
                    # Create a copy of the set to iterate safely
                    chats_to_sync = list(dirty_chats)
                    
                    for chat_id in chats_to_sync:
                        if chat_id in ram_chats:
                            chat_type, name = ram_chats.get(chat_id)
                            
                            await conn.execute("""
                                INSERT INTO chats (chat_id, chat_type, first_name, date_added)
                                VALUES ($1, $2, $3, $4)
                                ON CONFLICT (chat_id) DO NOTHING
                            """, chat_id, chat_type, name,
                                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

                            dirty_chats.discard(chat_id)
            
            # Sleep for 30 seconds before next sync
            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"Error in sync_to_db: {e}")
            await asyncio.sleep(30)  # Sleep even on error to prevent rapid looping

async def get_stats_data():
    async with db_pool.acquire() as conn:
        users = await conn.fetchval(
            "SELECT COUNT(*) FROM chats WHERE chat_type = 'private'"
        )
        groups = await conn.fetchval(
            "SELECT COUNT(*) FROM chats WHERE chat_type != 'private'"
        )
    return users, groups

async def get_all_chat_ids():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT chat_id FROM chats")
    return [row["chat_id"] for row in rows]

# ================= CALCULATOR =================

def safe_calculate(expression):
    # Basic cleanup
    expression = expression.replace('^', '**')
    
    # Security check: must contain at least one digit
    if not any(char.isdigit() for char in expression):
        return None

    # Allow specific math functions
    safe_dict = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
    safe_dict['abs'] = abs
    safe_dict['round'] = round

    try:
        # Evaluate safely
        result = eval(expression, {"__builtins__": {}}, safe_dict)
        return str(result)
    except:
        return None

# ================= ADMIN HANDLERS =================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    start_time = time.time()
    msg = await update.message.reply_text("⚡ Calculating...")
    latency = round((time.time() - start_time) * 1000, 2)

    try:
        users, groups = await get_stats_data()
    except Exception as e:
        await msg.edit_text(f"❌ DB Error: {e}")
        return

    owner_text = ""
    for admin_id in ADMIN_IDS:
        owner_text += f"➥ <a href='tg://user?id={admin_id}'>{admin_id}</a>\n"

    stats_text = (
        f"📊 <b>sʏsᴛᴇᴍ sᴛᴀᴛᴜs</b>\n"
        f"────────────────\n"
        f"📡 ᴅʙ: 🟢 Connected\n"
        f"📶 ʟᴀᴛ: {latency}ms\n"
        f"👥 ᴜsᴇʀs: {users}\n"
        f"🌐 ɢʀᴏᴜᴘs: {groups}\n"
        f"🆘 <b>Owners</b>\n{owner_text}"
        f"───────────────"
    )

    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats")]]
    await msg.edit_text(stats_text, parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(keyboard))

async def stats_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("❌ You are not admin!", show_alert=True)
        return

    await query.answer("Refreshing...")

    try:
        users, groups = await get_stats_data()
    except Exception as e:
        await query.edit_message_text(f"❌ DB Error: {e}")
        return

    stats_text = (
        f"📊 <b>sʏsᴛᴇᴍ sᴛᴀᴛᴜs</b>\n"
        f"────────────────\n"
        f"📡 ᴅʙ: 🟢 Connected\n"
        f"👥 ᴜsᴇʀs: {users}\n"
        f"🌐 ɢʀᴏᴜᴘs: {groups}\n"
        f"───────────────"
    )

    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats")]]
    
    # Try/Except to catch "Message is not modified" error from Telegram
    try:
        await query.edit_message_text(stats_text, parse_mode='HTML',
                                      reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        pass 

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    if context.args:
        text_to_send = " ".join(context.args)
    else:
        await update.message.reply_text("Reply or type text to broadcast.")
        return

    status_msg = await update.message.reply_text("⏳ Broadcast started...")

    try:
        all_chats = await get_all_chat_ids()
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to fetch chats: {e}")
        return

    success = 0
    failed = 0

    for chat_id in all_chats:
        try:
            await context.bot.send_message(chat_id, text_to_send)
            success += 1
        except:
            failed += 1
        
        # Small delay to avoid hitting Telegram rate limits
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete</b>\n\n"
        f"👥 Total: {len(all_chats)}\n"
        f"🟢 Success: {success}\n"
        f"🔴 Failed: {failed}",
        parse_mode='HTML'
    )

# ================= USER HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    add_chat(chat.id, chat.type,
             chat.title if chat.title else chat.first_name)

    user_name = update.effective_user.first_name

    welcome_text = (
        f"Hҽყ {user_name}\n"
        "Wᴇʟᴄσɱᴇ ᴛσ Tʜᴇ Cᴀʟᴄᴜʟᴀᴛᴏʀ Bᴏᴛ\n\n"
        "⚡ Pᴏᴡᴇʀᴇᴅ ʙʏ AｘB Bᴏᴛs"
    )

    bot_username = context.bot.username
    add_group_url = f"https://t.me/{bot_username}?startgroup=true"

    support_link = "https://t.me/axbsupport"
    hq_link = "https://t.me/your_channel"
    owner_link = f"https://t.me/axbowners"

    keyboard = [
        [InlineKeyboardButton("➕ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ➕", url=add_group_url)],
        [
            InlineKeyboardButton("💬 Support", url=support_link),
            InlineKeyboardButton("🏢 HQ", url=hq_link)
        ],
        [
            InlineKeyboardButton("👑 Owner", url=owner_link)
        ]
    ]

    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    add_chat(chat.id, chat.type,
             chat.title if chat.title else chat.first_name)

    if not update.message or not update.message.text:
        return

    answer = safe_calculate(update.message.text)

    if answer:
        await update.message.reply_text(answer)

# ================= LIFECYCLE =================

async def on_startup(application):
    """Runs when the bot starts up."""
    logger.info("Connecting to Database...")
    await create_pool()
    await init_db()
    
    # Start the background sync task natively with asyncio
    asyncio.create_task(sync_to_db())
    logger.info("Bot started successfully.")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(
        CallbackQueryHandler(stats_refresh_callback, pattern="refresh_stats"))
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    )

    print("Bot is running...")
    application.run_polling()
