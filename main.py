# main.py
import logging
import math
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

import database as db
import keyboards as kb
from config import TOKEN, ADMIN_IDS

logging.basicConfig(level=logging.INFO)

# ================= MATH LOGIC =================

def safe_calculate(expression):
    expression = expression.replace("^", "**").replace("×", "*").replace("÷", "/")
    if not any(char.isdigit() for char in expression): return None
    
    safe_dict = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
    safe_dict.update({"abs": abs, "round": round})
    
    try:
        result = eval(expression, {"__builtins__": {}}, safe_dict)
        return f"{result:,}"
    except:
        return None

# ================= HANDLERS =================

async def start_cmd(message: Message, bot: Bot):
    await db.add_chat(message.chat.id, message.chat.type, message.chat.title or message.from_user.first_name)
    me = await bot.get_me()
    text = (
        f"Hҽყ {message.from_user.first_name}\n"
        "Wᴇʟᴄσɱᴇ ᴛσ Tʜᴇ Cᴀʟᴄᴜʟᴀᴛᴏʀ Bᴏᴛ\n\n"
        "⚡ Pᴏᴡᴇʀᴇᴅ ʙʏ AｘB Bᴏᴛs"
    )
    await message.answer(text, reply_markup=kb.start_keyboard(me.username))

async def stats_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    u, g, nt, sz = await db.get_stats_data()
    text = (
        "📊 <b>System Status</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👤 Users: <code>{u}</code>\n"
        f"🌐 Groups: <code>{g}</code>\n"
        f"📈 Today: <code>+{nt}</code>\n"
        f"📂 DB Size: <code>{sz}</code>"
    )
    await message.answer(text, reply_markup=kb.stats_keyboard())

async def refresh_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    u, g, nt, sz = await db.get_stats_data()
    text = (
        "📊 <b>System Status</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👤 Users: <code>{u}</code>\n"
        f"🌐 Groups: <code>{g}</code>\n"
        f"📈 Today: <code>+{nt}</code>\n"
        f"📂 DB Size: <code>{sz}</code>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb.stats_keyboard())
    except: pass
    await callback.answer("Stats Updated")

async def broadcast(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS or not message.reply_to_message: return
    
    target = "users" if message.text.startswith("/broad") else "groups"
    chats = await db.get_all_chats(target)
    
    sent = 0
    status = await message.answer(f"📢 Starting broadcast to {len(chats)} {target}...")
    
    for i, row in enumerate(chats, 1):
        try:
            await bot.copy_message(row['chat_id'], message.chat.id, message.reply_to_message.message_id)
            sent += 1
        except: pass
        if i % 20 == 0:
            await status.edit_text(f"📢 Broadcasting... {i}/{len(chats)}")
        await asyncio.sleep(0.05)
        
    await status.edit_text(f"✅ Broadcast Done!\nSent to: {sent}")

async def on_message(message: Message):
    if message.chat.type != 'private':
        await db.add_chat(message.chat.id, message.chat.type, message.chat.title)
    
    res = safe_calculate(message.text)
    if res: await message.reply(f"<b>Result:</b> <code>{res}</code>")

# ================= RUN =================

async def main():
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    
    await db.create_pool()
    await db.init_db()

    dp.message.register(start_cmd, Command("start"))
    dp.message.register(stats_cmd, Command("stats"))
    dp.message.register(broadcast, Command("broadcast", "broad"))
    dp.callback_query.register(refresh_stats, F.data == "refresh_stats")
    dp.message.register(on_message)

    print("Bot is active with multi-file structure.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
