import random
import asyncio
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ChatType, ParseMode

# Import configuration, state databases, and engine instances
import config
from config import (
    bot, dp, main_router, check_autoleave, is_ghost_banned, check_spam,
    is_shadow_banned, ensure_group, periodic_save, backup_to_group,
    load_from_group, load_settings
)

# Import handlers to register them on the router
import handlers
import a_handlers
import store 
import market 

from handlers import trigger_drop
from market import market_engine_loop

# ==========================================
# AIOGRAM HANDLER & CONTROL MIDDLEWARE
# ==========================================
class GlobalGuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data: dict):
        # 1. Route the event type (Message or CallbackQuery)
        is_msg = isinstance(event, Message)
        is_callback = isinstance(event, CallbackQuery)
        
        if not is_msg and not is_callback:
            return await handler(event, data)
            
        user = event.from_user
        uid = user.id if user else None
        if not uid: return await handler(event, data)

        # 2. Increment global message log counter (Messages only)
        if is_msg:
            config.total_messages += 1
            if event.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                if await check_autoleave(event.chat.id): return

        # 3. ADMIN IMMUNITY: Skip all ban/spam filtering blocks
        if uid not in config.ADMIN_IDS:
            
            # Hard restrict: globally (ghost) banned users
            if is_ghost_banned(uid):
                if is_msg:
                    try: await event.delete()
                    except Exception: pass
                return
            
            # Anti-Spam throttle execution (Catches BOTH text and buttons)
            if check_spam(uid):
                if is_msg:
                    try: 
                        # 🔧 FIX: Use event.reply() and HTML escape the name
                        safe_name = str(user.first_name).replace("<", "&lt;").replace(">", "&gt;")
                        await event.reply(
                            f"⚠️ <b><a href='tg://user?id={uid}'>{safe_name}</a></b>, you have been shadow-banned for spamming.\n"
                            f"🔇 You are muted for 10 minutes.", 
                            parse_mode=ParseMode.HTML
                        )
                        await event.delete()
                    except Exception: pass
                elif is_callback:
                    try:
                        # Show an immediate pop-up alert for button spammers
                        await event.answer("⚠️ You have been shadow-banned for 10 minutes due to button spamming!", show_alert=True)
                    except Exception: pass
                return

            # Shadow ban: block user dynamically
            if is_shadow_banned(uid):
                if is_callback:
                    try: await event.answer("🔇 You are currently shadow-banned. Please wait.", show_alert=True)
                    except Exception: pass
                return

        # ==========================================
        # CARD DROP SPAWNER ENGINE (FOR GROUPS - Messages Only)
        # ==========================================
        if is_msg and event.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            chat_id = str(event.chat.id)
            ensure_group(chat_id, event.chat.title)
            
            # Get specific spawn target configuration
            s_min = config.load_db()["groups"].get(chat_id, {}).get("spawn_min", 100)
            s_max = config.load_db()["groups"].get(chat_id, {}).get("spawn_max", 110)
            
            # Spawn logic counter increment
            config.group_counters.setdefault(chat_id, {"count": 0, "target": random.randint(s_min, s_max)})
            config.group_counters[chat_id]["count"] += 1
            if config.group_counters[chat_id]["count"] >= config.group_counters[chat_id]["target"]:
                config.group_counters[chat_id] = {"count": 0, "target": random.randint(s_min, s_max)}
                asyncio.create_task(trigger_drop(event.chat.id))

        return await handler(event, data)

# ==========================================
# MAIN EXECUTION ENTRY POINT
# ==========================================
async def main():
    # Initial settings verification on startup
    load_settings()
    
    # Setup middlewares for BOTH Messages and Callbacks
    dp.message.outer_middleware(GlobalGuardMiddleware())
    dp.callback_query.outer_middleware(GlobalGuardMiddleware())
    
    # Attach unified main routers
    dp.include_router(main_router)
    
    try:
        # Check and restore database from pinned backup if needed
        await load_from_group()
        
        # Initiate scheduled background microtasks
        asyncio.create_task(periodic_save())
        asyncio.create_task(backup_to_group())
        
        # Start the stock market simulation engine
        asyncio.create_task(market_engine_loop())
        
        print("🌸 Anime Nexus is running over high speed aiogram v3 engines...")
        
        # Drop pending update queues to avoid start-up spam bursts
        await bot.delete_webhook(drop_pending_updates=True) 
        
        # Start bot polling loop
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())