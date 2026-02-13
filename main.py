import asyncio
import logging
import math
from datetime import datetime
import asyncpg

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- CONFIG ---
TOKEN = "8368723938:AAGjgK3u0mkmcLw8D7Az511d29S1bXRm86Y"
ADMIN_IDS = [5716292610, 7708811819]
DATABASE_URL = "postgresql://axb:h_9dhhH5KF_5c0xumLMziA@axb-bots-12453.jxf.gcp-asia-south1.cockroachlabs.cloud:26257/defaultdb?sslmode=require"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()

# Database Pool
db_pool = None

# ================= DATABASE INITIALIZATION =================

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id BIGINT PRIMARY KEY,
                chat_type TEXT,
                first_name TEXT,
                date_added TEXT
            )
        """)

# ================= HANDLERS =================

@router.message(Command("start"))
async def start_handler(message: Message):
    # Save user to DB
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO chats (chat_id, chat_type, first_name, date_added) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (chat_id) DO NOTHING",
            message.chat.id, message.chat.type, message.chat.full_name,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

    # 🎨 COLORED BUTTONS (API 2026 UPDATE)
    builder = InlineKeyboardBuilder()
    
    # Blue Button
    builder.row(InlineKeyboardButton(
        text="➕ Add to Group", 
        url=f"https://t.me/{(await bot.get_me()).username}?startgroup=true",
        style="primary" 
    ))
    
    # Green and Red Buttons
    builder.row(
        InlineKeyboardButton(text="💬 Support", url="https://t.me/axbsupport", style="success"),
        InlineKeyboardButton(text="👑 Owner", url="https://t.me/axbowners", style="danger")
    )

    await message.answer(
        f"Hҽყ **{message.from_user.first_name}**\n"
        "Welcome to the Calculator Bot!\n\n"
        "⚡ Powered by AxB Bots",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

@router.message(Command("stats"))
async def stats_handler(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM chats WHERE chat_type = 'private'")
        groups = await conn.fetchval("SELECT COUNT(*) FROM chats WHERE chat_type != 'private'")

    # Primary (Blue) button for refresh
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh Stats", callback_data="refresh", style="primary")]
    ])
    
    await message.answer(
        f"📊 **System Stats**\n\n👤 Users: `{users}`\n👥 Groups: `{groups}`",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@router.message()
async def calculator(message: Message):
    if not message.text:
        return

    # Basic safety check and calculation
    expression = message.text.replace('^', '**')
    if any(char.isdigit() for char in expression):
        try:
            safe_dict = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
            result = eval(expression, {"__builtins__": {}}, safe_dict)
            await message.reply(f"🔢 **Result:** `{result}`", parse_mode="Markdown")
        except:
            pass

# ================= RUNNER =================

async def main():
    await init_db()
    dp.include_router(router)
    print("Bot is running with 2026 Colored Buttons update...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
