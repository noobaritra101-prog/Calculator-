import json
import random
import uuid
import os
import sys
import time
import asyncio
import difflib
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery, InlineQuery, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQueryResultPhoto, InlineQueryResultArticle, InputTextMessageContent,
    BufferedInputFile, FSInputFile
)
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode, ChatType

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN   = "7658617809:AAGEYNtWaLh-859dyn4pLcd_7Rdw3mLtWeM"
ADMIN_ID    = 5716292610
DB_GROUP_ID = -1003799799158

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
main_router = Router()

DB_FILE = "database.json"

# ── In-memory DB cache ───────────────────────────────────────────────────────
_db_cache        = None
_db_dirty        = False
_db_last_save    = 0
DB_SAVE_INTERVAL = 5

# ── In-memory state ──────────────────────────────────────────────────────────
group_counters = {}
active_drops   = {}
bot_start_time = time.time()
total_messages = 0

spam_tracker = {}
shadow_banned = {}
ghost_banned  = set()

# 🔥 PERFORMANCE FEATURE: Caches spoilered images to keep drop events instant
spoiler_cache = {}

# Cache to prevent rate-limiting on get_chat member counts
group_member_cache = {}
MEMBER_CACHE_TTL   = 3600  # 1 hour

SPAM_WINDOW           = 10
SPAM_THRESHOLD        = 8
SHADOW_BAN_DUR        = 600
AUTOLEAVE_MIN_MEMBERS = 40
autoleave_enabled     = True

RARITIES     = ["Divine ❄️", "Elite ⚓", "Basic 🃏"]
RARITY_ORDER = {"Divine ❄️": 0, "Elite ⚓": 1, "Basic 🃏": 2}
RARITY_SAFE  = {"Divine ❄️": "divine", "Elite ⚓": "elite", "Basic 🃏": "basic"}
SAFE_RARITY  = {v: k for k, v in RARITY_SAFE.items()}

# ==========================================
# DATABASE HELPERS (Async Background Flusher)
# ==========================================
def load_db() -> dict:
    global _db_cache
    if _db_cache is not None:
        return _db_cache
    if not os.path.exists(DB_FILE):
        _db_cache = {"users": {}, "global_cards": {}, "groups": {}, "settings": {}}
        return _db_cache
    with open(DB_FILE, "r", encoding="utf-8") as f:
        _db_cache = json.load(f)
    if "settings" not in _db_cache:
        _db_cache["settings"] = {}
    return _db_cache

def save_db(data: dict = None):
    global _db_cache, _db_dirty
    if data is not None:
        _db_cache = data
    _db_dirty = True

def _flush_db(force: bool = False):
    global _db_dirty, _db_last_save
    if (not _db_dirty and not force) or _db_cache is None:
        return
    try:
        tmp = DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_db_cache, f, indent=2, ensure_ascii=False)
        os.replace(tmp, DB_FILE)
        _db_dirty     = False
        _db_last_save = time.time()
    except Exception as e:
        print(f"[DB] Save error: {e}")

async def periodic_save():
    while True:
        await asyncio.sleep(DB_SAVE_INTERVAL)
        if _db_dirty:
            await asyncio.to_thread(_flush_db)

# ── AUTO BACKUP TO GROUP EVERY 20 MINS ──
async def backup_to_group():
    while True:
        await asyncio.sleep(20 * 60) # 20 minutes
        try:
            _flush_db(force=True) # Force save to disk before backing up
            
            # Try to delete the previously pinned backup message
            try:
                chat = await bot.get_chat(DB_GROUP_ID)
                if chat.pinned_message:
                    await bot.delete_message(DB_GROUP_ID, chat.pinned_message.message_id)
            except Exception as e:
                print(f"[BACKUP] Could not delete old pinned message: {e}")
                
            # Send new backup and pin it
            doc = FSInputFile(DB_FILE, filename=f"database_{int(time.time())}.json")
            msg = await bot.send_document(
                DB_GROUP_ID, 
                document=doc, 
                caption=f"📦 Automated DB Backup\n📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
            )
            await bot.pin_chat_message(DB_GROUP_ID, msg.message_id, disable_notification=True)
            print("[BACKUP] Successfully backed up and pinned to group.")
        except Exception as e:
            print(f"[BACKUP] Task failed: {e}")

# ── AUTO LOAD FROM PINNED MESSAGE ON STARTUP ──
async def load_from_group():
    print("🔄 Checking for existing pinned database in group...")
    try:
        chat = await bot.get_chat(DB_GROUP_ID)
        if chat.pinned_message and chat.pinned_message.document:
            doc = chat.pinned_message.document
            if doc.file_name and doc.file_name.endswith(".json"):
                file_info = await bot.get_file(doc.file_id)
                await bot.download_file(file_info.file_path, destination=DB_FILE)
                print("✅ Successfully restored database from pinned message.")
            else:
                print("⚠️ Pinned message is not a JSON file.")
        else:
            print("⚠️ No pinned document found in the DB group.")
    except Exception as e:
        print(f"❌ Failed to restore DB from group: {e}")

def ensure_user(user_id, name, username=None) -> dict:
    db  = load_db()
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "name": name,
            "username": username,
            "balance": 0, "special_card": None,
            "cards": {}, "total_claimed": 0, "joined": int(time.time()),
            "sort_pref": "default"
        }
        save_db()
    else:
        updated = False
        if db["users"][uid].get("name") != name:
            db["users"][uid]["name"] = name
            updated = True
        if db["users"][uid].get("username") != username:
            db["users"][uid]["username"] = username
            updated = True
        if "sort_pref" not in db["users"][uid]:
            db["users"][uid]["sort_pref"] = "default"
            updated = True
        if updated:
            save_db()
    return db

def ensure_group(chat_id, chat_title):
    db  = load_db()
    cid = str(chat_id)
    if cid not in db["groups"]:
        db["groups"][cid] = {
            "title": chat_title, "joined": int(time.time()), "drops": 0, "claims": 0
        }
        save_db()
    return db

def load_settings():
    global autoleave_enabled
    db = load_db()
    autoleave_enabled = db["settings"].get("autoleave", True)

def get_mention(user_id, name):
    safe = str(name).replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'

# ==========================================
# GHOST & SHADOW BAN PROTECTION HELPERS
# ==========================================
def is_ghost_banned(uid: int) -> bool:
    return uid in ghost_banned

def is_shadow_banned(uid: int) -> bool:
    if uid not in shadow_banned:
        return False
    if time.time() > shadow_banned[uid]:
        del shadow_banned[uid]
        return False
    return True

def check_spam(uid: int) -> bool:
    now = time.time()
    spam_tracker.setdefault(uid, [])
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < SPAM_WINDOW]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= SPAM_THRESHOLD:
        spam_tracker[uid] = []
        shadow_banned[uid] = now + SHADOW_BAN_DUR
        return True
    return False

async def check_autoleave(chat_id: int) -> bool:
    if not autoleave_enabled:
        return False
    now = time.time()
    cached_count, last_checked = group_member_cache.get(chat_id, (None, 0))
    if cached_count is not None and (now - last_checked) < MEMBER_CACHE_TTL:
        count = cached_count
    else:
        try:
            count = await bot.get_chat_member_count(chat_id)
            group_member_cache[chat_id] = (count, now)
        except Exception:
            return False

    if count is not None and count < AUTOLEAVE_MIN_MEMBERS:
        try:
            await bot.send_message(
                chat_id,
                "<blockquote><b>「 ANIME NEXUS ぁ 」</b>\n\n"
                "⚠️ This group has fewer than <b>40 members</b>.\n"
                "I'm leaving now — さようなら 👋</blockquote>",
                parse_mode=ParseMode.HTML
            )
            await bot.leave_chat(chat_id)
            return True
        except Exception:
            pass
    return False

# ==========================================
# AIOGRAM HANDLER & CONTROL MIDDLEWARE
# ==========================================
class GlobalGuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        if not isinstance(event, Message):
            return await handler(event, data)
            
        global total_messages
        total_messages += 1

        if event.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            if await check_autoleave(event.chat.id):
                return

        uid = event.from_user.id if event.from_user else None
        if not uid:
            return await handler(event, data)

        if is_ghost_banned(uid):
            try:
                await event.delete()
            except Exception: pass
            return

        if check_spam(uid):
            try:
                await event.reply(
                    "<blockquote><b>⚠️ Shadow Banned ぁ</b>\n"
                    "You are sending messages too fast.\n"
                    "Restricted for <b>10 minutes</b>. 🔇</blockquote>",
                    parse_mode=ParseMode.HTML
                )
            except Exception: pass
            return

        if is_shadow_banned(uid):
            try:
                await event.delete()
            except Exception: pass
            return

        # Handle Message Counter & Card Drops Interception
        if event.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP] and not event.from_user.is_bot:
            chat_id = str(event.chat.id)
            ensure_group(event.chat.id, event.chat.title or "Unknown")
            group_counters.setdefault(chat_id, {"count": 0, "target": random.randint(100, 500)})
            group_counters[chat_id]["count"] += 1
            if group_counters[chat_id]["count"] >= group_counters[chat_id]["target"]:
                group_counters[chat_id] = {"count": 0, "target": random.randint(100, 500)}
                asyncio.create_task(trigger_drop(event.chat.id))

        return await handler(event, data)

# ==========================================
# DROP ENGINE (High Speed Implementation)
# ==========================================
async def trigger_drop(chat_id: int):
    db = load_db()
    if not db["global_cards"]:
        return

    roll = random.randint(1, 100)
    if roll <= 80:
        target_rarity = "Basic 🃏"
    elif roll <= 98:
        target_rarity = "Elite ⚓"
    else:
        target_rarity = "Divine ❄️"

    pool = {k: v for k, v in db["global_cards"].items() if v["rarity"] == target_rarity}
    if not pool:
        pool = db["global_cards"]

    card_id, card_data = random.choice(list(pool.items()))

    caption = (
        "<blockquote><b>「 ANIME NEXUS : CARD DROP ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✦ A wild card has appeared!\n\n"
        f"🌟 Rarity ┊ <b>{card_data['rarity']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💮 Tap below to claim it!</blockquote>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💮  C L A I M  C A R D  💮", callback_data=f"claim_{card_id}")
    ]])

    try:
        original_file_id = card_data["file_id"]
        
        # FAST PATH: Using the cached spoiler ID
        if original_file_id in spoiler_cache:
            msg = await bot.send_photo(
                chat_id=chat_id,
                photo=spoiler_cache[original_file_id], 
                caption=caption,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                has_spoiler=True
            )
        # SLOW PATH: Core processing on raw asset fallback
        else:
            file_info = await bot.get_file(original_file_id)
            file_bytes = await bot.download_file(file_info.file_path)
            photo_input = BufferedInputFile(file_bytes.read(), filename="card.jpg")
            
            msg = await bot.send_photo(
                chat_id=chat_id,
                photo=photo_input, 
                caption=caption,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                has_spoiler=True
            )
            spoiler_cache[original_file_id] = msg.photo[-1].file_id

        active_drops[msg.message_id] = card_id

        cid = str(chat_id)
        if cid in db["groups"]:
            db["groups"][cid]["drops"] = db["groups"][cid].get("drops", 0) + 1
            save_db()
    except Exception as e:
        print(f"[DROP] Error: {e}")

# ==========================================
# CLAIM LOGIC OVER ROUTERS
# ==========================================
@main_router.callback_query(F.data.startswith("claim_"))
async def claim_card_callback(callback_query: CallbackQuery):
    msg_id  = callback_query.message.message_id
    uid_int = callback_query.from_user.id
    user_id = str(uid_int)
    name    = callback_query.from_user.first_name
    uname   = callback_query.from_user.username

    if is_ghost_banned(uid_int):
        await callback_query.answer("❌ You are banned.", show_alert=True)
        return
    if is_shadow_banned(uid_int):
        await callback_query.answer("🔇 You are temporarily restricted.", show_alert=True)
        return
    if msg_id not in active_drops:
        await callback_query.answer("⚠️ Already claimed!", show_alert=True)
        return

    card_id   = active_drops.pop(msg_id)
    db        = ensure_user(user_id, name, uname)
    card_data = db["global_cards"].get(card_id)
    if not card_data:
        await callback_query.answer("❌ Card not found!", show_alert=True)
        return

    if card_id not in db["users"][user_id]["cards"]:
        db["users"][user_id]["cards"][card_id] = {
            "name": card_data["name"], "rarity": card_data["rarity"], "amount": 0
        }
    db["users"][user_id]["cards"][card_id]["amount"] += 1
    db["users"][user_id]["total_claimed"] = db["users"][user_id].get("total_claimed", 0) + 1

    cid = str(callback_query.message.chat.id)
    if cid in db["groups"]:
        db["groups"][cid]["claims"] = db["groups"][cid].get("claims", 0) + 1

    save_db()
    await callback_query.answer("🎉 Card claimed!", show_alert=True)

    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        pass

    winner_text = (
        "<blockquote><b>「 ANIME NEXUS : CARD CLAIMED ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎉 <b>{get_mention(user_id, name)}</b> claimed the card!\n\n"
        f"👤 Character ┊ <b>{card_data['name']}</b>\n"
        f"📺 Anime     ┊ {card_data['anime']}\n"
        f"🌟 Rarity    ┊ {card_data['rarity']}\n\n"
        "📖 Use /deck to view your collection.</blockquote>"
    )
    try:
        await callback_query.message.reply(winner_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        pass

# ==========================================
# PLAYER INTERFACES & PARSERS (/flex & /check)
# ==========================================
@main_router.message(Command("check"))
async def check_cmd(message: Message, command: CommandObject):
    db = load_db()
    if not db.get("global_cards"):
        await message.reply("<blockquote>⚠️ No cards in the database yet.</blockquote>", parse_mode=ParseMode.HTML)
        return

    if not command.args:
        await message.reply("<blockquote>⚠️ <b>Usage:</b> <code>/check <card name></code>\nExample: <code>/check goku</code></blockquote>", parse_mode=ParseMode.HTML)
        return

    query = command.args.lower().strip()
    best_match = None
    best_ratio = 0.0

    for cid, cdata in db["global_cards"].items():
        name_lower = cdata["name"].lower()
        if query == name_lower:
            best_match = (cid, cdata)
            break
        if query in name_lower:
            ratio = 0.8 + (len(query) / len(name_lower)) * 0.1
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = (cid, cdata)
        else:
            ratio = difflib.SequenceMatcher(None, query, name_lower).ratio()
            if ratio > 0.6 and ratio > best_ratio:
                best_ratio = ratio
                best_match = (cid, cdata)

    if not best_match:
        await message.reply(f"<blockquote>❌ No cards found globally matching <b>{command.args}</b>.</blockquote>", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match

    total_owned = 0
    for udata in db["users"].values():
        if "cards" in udata and matched_cid in udata["cards"]:
            total_owned += udata["cards"][matched_cid]["amount"]

    caption = (
        f"<blockquote><b>「 GLOBAL CARD LOOKUP ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <code>{matched_cid}</code>\n"
        f"👤 <b>{matched_data['name']}</b>\n"
        f"📺 {matched_data['anime']}\n"
        f"🌟 {matched_data['rarity']}\n\n"
        f"👥 In Circulation: <b>{total_owned}</b> copies\n"
        f"━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    )

    await message.reply_photo(photo=matched_data["file_id"], caption=caption, parse_mode=ParseMode.HTML)

@main_router.message(Command("flex"))
async def flex_cmd(message: Message, command: CommandObject):
    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    
    if not command.args:
        await message.reply("<blockquote>⚠️ <b>Usage:</b> <code>/flex <card name></code></blockquote>", parse_mode=ParseMode.HTML)
        return

    query = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})
    
    if not my_cards:
        await message.reply("<blockquote>❌ You don't own any cards yet!</blockquote>", parse_mode=ParseMode.HTML)
        return

    best_match = None
    best_ratio = 0.0

    for cid, cdata in my_cards.items():
        name_lower = cdata["name"].lower()
        if query == name_lower:
            best_match = (cid, cdata)
            break
        if query in name_lower:
            ratio = 0.8 + (len(query) / len(name_lower)) * 0.1
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = (cid, cdata)
        else:
            ratio = difflib.SequenceMatcher(None, query, name_lower).ratio()
            if ratio > 0.6 and ratio > best_ratio:
                best_ratio = ratio
                best_match = (cid, cdata)

    if not best_match:
        await message.reply(f"<blockquote>❌ You do not own a card matching <b>{command.args}</b>.</blockquote>", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data = db["global_cards"].get(matched_cid, {})

    caption = (
        f"<blockquote><b>「 CARD FLEX ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{matched_data['name']}</b>\n"
        f"📺 {global_data.get('anime', '?')}\n"
        f"🌟 {matched_data['rarity']}\n\n"
        f"📦 <b>You own:</b> ×{matched_data['amount']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    )

    await message.reply_photo(photo=global_data.get("file_id"), caption=caption, parse_mode=ParseMode.HTML)

# ==========================================
# /forcedrop
# ==========================================
@main_router.message(Command("forcedrop"))
async def force_drop_cmd(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        await message.delete()
    except Exception:
        pass

    db = load_db()
    if not db.get("global_cards"):
        await message.reply("<blockquote>⚠️ No cards in database. Use <code>/add_card</code> first.</blockquote>", parse_mode=ParseMode.HTML)
        return

    if message.chat.type == ChatType.PRIVATE:
        if not command.args:
            await message.reply(
                "<blockquote><b>「 FORCE DROP ぁ 」</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "In DM, provide the group ID:\n"
                "<code>/forcedrop -100XXXXXXXXXX</code>\n"
                "━━━━━━━━━━━━━━━━━━━━━━</blockquote>",
                parse_mode=ParseMode.HTML
            )
            return
        try:
            target_chat = int(command.args.split()[0])
        except ValueError:
            await message.reply("<blockquote>⚠️ Invalid chat ID.</blockquote>", parse_mode=ParseMode.HTML)
            return
        await trigger_drop(target_chat)
        await message.reply(f"<blockquote>✅ Drop triggered in <code>{target_chat}</code></blockquote>", parse_mode=ParseMode.HTML)
    else:
        await trigger_drop(message.chat.id)

# ==========================================
# DECK DISPLAY LAYER (/deck & /card)
# ==========================================
DECK_PER_PAGE = 10
CARDS_PER_PAGE = 8

async def send_deck_page(message: Message, db: dict, user_id: str, page=0, edit=False):
    user_data = db["users"][user_id]
    cards     = user_data.get("cards", {})
    items     = list(cards.items())
    user_name = user_data.get('name', 'User')

    if not items:
        text = "<blockquote><b>「 COLLECTION EMPTY ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\nYou haven't collected any cards yet!\nWait for a drop in the group.</blockquote>"
        if edit and isinstance(message, CallbackQuery):
            await message.message.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            await message.reply(text, parse_mode=ParseMode.HTML)
        return

    sort_pref = user_data.get("sort_pref", "default")
    if sort_pref == "rarity":
        items.sort(key=lambda x: RARITY_ORDER.get(x[1]["rarity"], 99))
    elif sort_pref == "name":
        items.sort(key=lambda x: x[1]["name"].lower())
    elif sort_pref == "amount":
        items.sort(key=lambda x: x[1]["amount"], reverse=True)

    special_card_id = user_data.get("special_card")
    special_item = None
    if special_card_id and special_card_id in cards:
        for i, item in enumerate(items):
            if item[0] == special_card_id:
                special_item = items.pop(i)
                break

    total       = len(items) + (1 if special_item else 0)
    start       = page * DECK_PER_PAGE
    end         = min(start + DECK_PER_PAGE, len(items))
    page_items  = items[start:end]
    total_pages = max(1, (total - 1) // DECK_PER_PAGE + 1)

    if page >= total_pages:
        page = total_pages - 1
        start = page * DECK_PER_PAGE
        end = len(items)
        page_items = items[start:end]

    safe_name = str(user_name).replace("<", "&lt;").replace(">", "&gt;")
    text = f"『  ぁ 𝘾𝘼𝙍𝘿 𝘿𝙀𝘾𝙆  - {safe_name} 』\n━━━━━━━━━━━━━━━━━━━\n"

    if page == 0 and special_item:
        scid, scdata = special_item
        text += f"✨ {scdata['name']} - [{scdata['rarity']}]  ×{scdata['amount']}\n"

    for cid, cdata in page_items:
        text += f"✦ {cdata['name']} - [{cdata['rarity']}]  ×{cdata['amount']}\n"

    text += "\n━━━━━━━━━━━━━━━━━━━"

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="❮", callback_data=f"deck_prev_{user_id}_{page-1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text="❮", callback_data="noop"))

    if end < len(items):
        nav_buttons.append(InlineKeyboardButton(text="❯", callback_data=f"deck_next_{user_id}_{page+1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text="❯", callback_data="noop"))

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⌈ 𝗣𝗮𝗴𝗲 {page+1}/{total_pages} ⌋", callback_data=f"page_alert_{page+1}")],
        nav_buttons,
        [InlineKeyboardButton(text="View collection", switch_inline_query_current_chat="")]
    ])

    if edit and isinstance(message, CallbackQuery):
        await message.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await message.reply(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@main_router.message(Command("deck"))
async def view_deck_cmd(message: Message):
    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    await send_deck_page(message, db, user_id, page=0, edit=False)

@main_router.callback_query(F.data.startswith("deck_"))
async def deck_nav_cb(callback_query: CallbackQuery):
    parts = callback_query.data.split("_")
    direction, owner_id, page_str = parts[1], parts[2], parts[3]
    if str(callback_query.from_user.id) != owner_id:
        await callback_query.answer("❌ Not your deck!", show_alert=True)
        return
    db = load_db()
    await send_deck_page(callback_query, db, owner_id, int(page_str), edit=True)
    await callback_query.answer()

async def send_card_grid_page(message: Message, db: dict, user_id: str, page=0, edit=False):
    user_data = db["users"][user_id]
    cards     = user_data.get("cards", {})
    items     = list(cards.items())
    
    if not items:
        text = "<blockquote><b>「 COLLECTION EMPTY ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\nYou haven't collected any cards yet!</blockquote>"
        if edit and isinstance(message, CallbackQuery):
            await message.message.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            await message.reply(text, parse_mode=ParseMode.HTML)
        return

    sort_pref = user_data.get("sort_pref", "default")
    if sort_pref == "rarity": items.sort(key=lambda x: RARITY_ORDER.get(x[1]["rarity"], 99))
    elif sort_pref == "name": items.sort(key=lambda x: x[1]["name"].lower())
    elif sort_pref == "amount": items.sort(key=lambda x: x[1]["amount"], reverse=True)

    total       = len(items)
    start       = page * CARDS_PER_PAGE
    end         = min(start + CARDS_PER_PAGE, total)
    page_items  = items[start:end]
    total_pages = max(1, (total - 1) // CARDS_PER_PAGE + 1)

    special_text = "None"
    if user_data.get("special_card") and user_data["special_card"] in cards:
        sp = cards[user_data["special_card"]]
        special_text = f"✨ {sp['name']}  [{sp['rarity']}]"

    text = (
        f"<blockquote><b>「 ANIME NEXUS : GRID COLLECTION ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✦ Player  ┊ <b>{get_mention(user_id, user_data.get('name','User'))}</b>\n"
        f"✨ Special ┊ {special_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for cid, cdata in page_items:
        text += f"✦ <code>{cid}</code> — <b>{cdata['name']}</b>  [{cdata['rarity']}]  ×{cdata['amount']}\n"

    text += f"\n━━━━━━━━━━━━━━━━━━━━━━\n🎴 <b>{total}</b> unique  ·  Page <b>{page+1}/{total_pages}</b>  ·  Sort: <i>{sort_pref}</i></blockquote>"

    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"cgrid_{user_id}_{page-1}"))
    if end < total: nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"cgrid_{user_id}_{page+1}"))

    keyboard = InlineKeyboardMarkup(inline_keyboard=[nav] if nav else [])

    if edit and isinstance(message, CallbackQuery):
        await message.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await message.reply(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@main_router.message(Command("card"))
async def view_card_grid(message: Message):
    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    await send_card_grid_page(message, db, user_id, page=0, edit=False)

@main_router.callback_query(F.data.startswith("cgrid_"))
async def card_grid_nav_cb(callback_query: CallbackQuery):
    parts = callback_query.data.split("_")
    owner_id, page_str = parts[1], parts[2]
    if str(callback_query.from_user.id) != owner_id:
        await callback_query.answer("❌ Not your collection!", show_alert=True)
        return
    db = load_db()
    await send_card_grid_page(callback_query, db, owner_id, int(page_str), edit=True)
    await callback_query.answer()

@main_router.callback_query(F.data.startswith("page_alert_"))
async def page_indicator_alert(callback_query: CallbackQuery):
    page_num = callback_query.data.split("_")[2]
    await callback_query.answer(f"ℹ️ You are currently on page {page_num}.", show_alert=True)

@main_router.callback_query(F.data == "noop")
async def noop_cb(callback_query: CallbackQuery):
    await callback_query.answer()

# ==========================================
# /sortcards INTERFACE PRESETS
# ==========================================
@main_router.message(Command("sortcards"))
async def sort_cards(message: Message):
    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    current_sort = db["users"][user_id].get("sort_pref", "default").title()
    
    text = (
        f"<b>「 SORTING ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>🌟 Rarity  — Divine → Elite → Basic\n"
        f"🔤 Name    — A → Z\n"
        f"📦 Amount  — Most owned first\n"
        f"🔄 Default — Claim order </blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Current sorting order </b>- {current_sort}"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌟 Rarity", callback_data=f"setsort_{user_id}_rarity"),
            InlineKeyboardButton(text="🔤 Name", callback_data=f"setsort_{user_id}_name")
        ],
        [
            InlineKeyboardButton(text="📦 Amount", callback_data=f"setsort_{user_id}_amount"),
            InlineKeyboardButton(text="🔄 Default", callback_data=f"setsort_{user_id}_default")
        ]
    ])
    await message.reply(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data.startswith("setsort_"))
async def set_sort_cb(callback_query: CallbackQuery):
    parts = callback_query.data.split("_")
    owner_id, mode = parts[1], parts[2]
    
    if str(callback_query.from_user.id) != owner_id:
        await callback_query.answer("❌ Not your sorting menu!", show_alert=True)
        return
        
    db = load_db()
    db["users"][owner_id]["sort_pref"] = mode
    save_db()
    
    await callback_query.answer(f"✅ Sorting order saved: {mode.title()}")
    current_sort = mode.title()
    
    text = (
        f"<b>「 SORTING ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>🌟 Rarity  — Divine → Elite → Basic\n"
        f"🔤 Name    — A → Z\n"
        f"📦 Amount  — Most owned first\n"
        f"🔄 Default — Claim order </blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Current sorting order </b>- {current_sort}"
    )
    await callback_query.message.edit_text(text, reply_markup=callback_query.message.reply_markup, parse_mode=ParseMode.HTML)

# ==========================================
# /profile ENGINE PARSER DESIGN LAYOUTS
# ==========================================
@main_router.message(Command("profile"))
async def view_profile(message: Message):
    user_id   = str(message.from_user.id)
    name      = message.from_user.first_name
    username  = message.from_user.username
    db        = ensure_user(user_id, name, username)
    user_data = db["users"][user_id]
    cards     = user_data.get("cards", {})

    unique_cards  = len(cards)
    joined_year   = datetime.fromtimestamp(user_data.get("joined", int(time.time())), tz=timezone.utc).strftime("%Y")
    
    sorted_users = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)
    rank = 9999
    for i, (uid, udata) in enumerate(sorted_users):
        if uid == user_id:
            rank = i + 1
            break

    uname_display = f"@{username}" if username else "None"

    profile_text = (
        "「 𝙉𝙀𝙓𝙐𝙎 : 𝙋𝙍𝙊𝙁𝙄𝙇𝙀 ぁ 」\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"❖ 𝙉𝙖𝙢𝙚          ➜ {name}\n"
        f"❖ 𝙐𝙨𝙚𝙧𝙣𝙖𝙢𝙚     ➜ {uname_display}\n"
        f"❖ 𝙐𝙨𝙚𝙧 𝙄𝘿       ➜ <code>{user_id}</code>\n"
        f"❖ 𝙔𝙚𝙖𝙧 𝙅𝙤𝙞𝙣𝙚𝙙   ➜ {joined_year}\n\n"
        f"❖ 𝙏𝙤𝙩𝙖𝙡 𝘾𝙖𝙧𝙙𝙨   ➜ {unique_cards}\n"
        f"❖ 𝙍𝙖𝙣𝙠  ➜ #{rank}\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Close", callback_data="close_msg")]])

    photo_sent = False
    try:
        photos = await bot.get_user_profile_photos(int(user_id), limit=1)
        if photos.total_count > 0:
            await message.reply_photo(photo=photos.photos[0][0].file_id, caption=profile_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            photo_sent = True
    except Exception:
        pass

    if not photo_sent:
        await message.reply(profile_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "close_msg")
async def close_msg_cb(callback_query: CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception: pass

# ==========================================
# SPECIAL SYSTEM INTERACTION PRESETS (/special)
# ==========================================
@main_router.message(Command("special"))
async def set_special(message: Message, command: CommandObject):
    user_id = str(message.from_user.id)
    db      = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    if not command.args:
        await message.reply("<blockquote>⚠️ Format: <code>/special DB-XXXXXX</code></blockquote>", parse_mode=ParseMode.HTML)
        return

    target_card = command.args.split()[0].strip().upper()
    if target_card not in db["users"][user_id]["cards"]:
        await message.reply("<blockquote>❌ You don't own that card!</blockquote>", parse_mode=ParseMode.HTML)
        return

    db["users"][user_id]["special_card"] = target_card
    save_db()
    cdata = db["users"][user_id]["cards"][target_card]
    await message.reply(
        f"<blockquote><b>「 SPECIAL CARD SET ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Character ┊ <b>{cdata['name']}</b>\n"
        f"🌟 Rarity    ┊ {cdata['rarity']}\n\n"
        f"✨ Pinned to the top of your deck!</blockquote>",
        parse_mode=ParseMode.HTML
    )

# ==========================================
# /leaderboard WRAPPERS
# ==========================================
@main_router.message(Command(commands=["leaderboard", "top"]))
async def leaderboard(message: Message):
    db  = load_db()
    top = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)[:10]
    medals = ["🥇","🥈","🥉"] + ["🏅"]*7
    text   = (
        "<blockquote><b>「 ANIME NEXUS : LEADERBOARD ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✦ Top Collectors (Unique Cards)\n\n"
    )
    for i, (uid, ud) in enumerate(top):
        text += f"{medals[i]} <b>{get_mention(uid, ud.get('name','Unknown'))}</b> — 🎴 {len(ud.get('cards', {}))}\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    await message.reply(text, parse_mode=ParseMode.HTML)

# ==========================================
# GLOBAL ENGINES DRILLED DATASET (/cards)
# ==========================================
@main_router.message(Command(commands=["cards", "total_cards", "all_cards"]))
async def cards_browser(message: Message):
    db = load_db()
    if not db.get("global_cards"):
        await message.reply("<blockquote>⚠️ Database is empty.</blockquote>", parse_mode=ParseMode.HTML)
        return
    await show_anime_list(message, edit=False)

async def show_anime_list(message: Message, edit=False):
    db    = load_db()
    cards = db.get("global_cards", {})

    anime_map = {}
    for cd in cards.values():
        anime_map[cd["anime"]] = anime_map.get(cd["anime"], 0) + 1

    sorted_animes = sorted(anime_map.items(), key=lambda x: x[1], reverse=True)
    rarity_lines = []
    for r in RARITIES:
        n = sum(1 for c in cards.values() if c["rarity"].strip() == r.strip())
        rarity_lines.append(f"  ✦ {r} ┊ <b>{n}</b>")

    text = (
        f"<blockquote><b>「 ANIME NEXUS : CARD DATABASE ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎴 Total Cards  ┊ <b>{len(cards)}</b>\n"
        f"📺 Anime Series ┊ <b>{len(sorted_animes)}</b>\n\n"
        f"── Rarity Breakdown ──\n"
        f"{chr(10).join(rarity_lines)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Choose an anime series:</blockquote>"
    )

    buttons = []
    row = []
    for anime, count in sorted_animes[:18]:
        label = (anime[:16] + "…" if len(anime) > 16 else anime) + f" ({count})"
        safe  = anime.replace("|", "¦")[:35]
        row.append(InlineKeyboardButton(text=f"📺 {label}", callback_data=f"an|{safe}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(text="✦ All Divine ❄️", callback_data="gr|divine"),
        InlineKeyboardButton(text="✦ All Elite ⚓",  callback_data="gr|elite"),
    ])
    buttons.append([
        InlineKeyboardButton(text="✦ All Basic 🃏",  callback_data="gr|basic"),
        InlineKeyboardButton(text="📋 Full List",    callback_data="gr|all"),
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit and isinstance(message, CallbackQuery):
        await message.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        await message.reply(text, reply_markup=markup, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data.startswith("an|"))
async def anime_rarity_picker(cq: CallbackQuery):
    await cq.answer()
    anime_name = cq.data[3:].replace("¦", "|")
    db    = load_db()
    cards = db.get("global_cards", {})

    rarity_count = {}
    for cd in cards.values():
        if cd["anime"] == anime_name:
            rk = cd["rarity"].strip()
            rarity_count[rk] = rarity_count.get(rk, 0) + 1

    total = sum(rarity_count.values())
    if not total:
        await cq.answer("No cards found!", show_alert=True)
        return

    lines = [f"  ✦ <b>{r}</b>  ┊  {rarity_count.get(r.strip(), 0)} card{'s' if rarity_count.get(r.strip(), 0)!=1 else ''}" for r in RARITIES]
    text = (
        f"<blockquote><b>「 {anime_name.upper()} ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📺 <b>{anime_name}</b>\n"
        f"🎴 Total: <b>{total}</b> cards\n\n"
        f"Choose a rarity to browse:\n\n"
        f"{chr(10).join(lines)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    )

    safe_anime = anime_name.replace("|", "¦")[:35]
    buttons = []
    for r in RARITIES:
        n = rarity_count.get(r.strip(), 0)
        if n > 0:
            safe_r = RARITY_SAFE[r]
            buttons.append([InlineKeyboardButton(text=f"✦ {r}  ({n} cards)", callback_data=f"acl|{safe_anime}|{safe_r}|0")])

    buttons.append([InlineKeyboardButton(text="◀️ Back to Anime List", callback_data="back_anime")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "back_anime")
async def back_to_anime(cq: CallbackQuery):
    await cq.answer()
    await show_anime_list(cq, edit=True)

@main_router.callback_query(F.data.startswith("acl|"))
async def anime_card_list(cq: CallbackQuery):
    await cq.answer()
    parts      = cq.data.split("|")
    safe_anime = parts[1]
    safe_r     = parts[2]
    page       = int(parts[3])

    anime_name  = safe_anime.replace("¦", "|")
    rarity_name = SAFE_RARITY.get(safe_r, safe_r)

    db    = load_db()
    cards = db.get("global_cards", {})

    matched = sorted(
        [(cid, cd) for cid, cd in cards.items()
         if cd["anime"] == anime_name and cd["rarity"].strip() == rarity_name.strip()],
        key=lambda x: x[1]["name"]
    )

    if not matched:
        await cq.answer("No cards found for this rarity!", show_alert=True)
        return

    total_m     = len(matched)
    total_pages = max(1, (total_m - 1) // BROWSE_PER_PAGE + 1)
    start       = page * BROWSE_PER_PAGE
    end         = min(start + BROWSE_PER_PAGE, total_m)

    lines = "\n".join(f"  ✦ <b>{cd['name']}</b>  <code>{cid}</code>" for cid, cd in matched[start:end])
    text = (
        f"<blockquote><b>「 {anime_name.upper()} ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📺 {anime_name}\n"
        f"🌟 <b>{rarity_name}</b>\n"
        f"🎴 <b>{total_m}</b> cards  ·  Page <b>{page+1}/{total_pages}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"acl|{safe_anime}|{safe_r}|{page-1}"))
    if end < total_m:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"acl|{safe_anime}|{safe_r}|{page+1}"))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="◀️ Back to Rarity", callback_data=f"an|{safe_anime}")])
    buttons.append([InlineKeyboardButton(text="🏠 Anime List",      callback_data="back_anime")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data.startswith("gr|"))
async def global_rarity(cq: CallbackQuery):
    await cq.answer()
    key   = cq.data[3:]
    db    = load_db()
    cards = db.get("global_cards", {})

    if key == "all":
        items = sorted(cards.items(), key=lambda x: (x[1]["anime"], x[1]["name"]))
        lines = "\n".join(f"  ✦ <b>{cd['name']}</b> — <i>{cd['anime']}</i>  [{cd['rarity']}]" for _, cd in items[:80])
        extra = f"\n<i>...and {len(items)-80} more. Use anime filter.</i>" if len(items) > 80 else ""
        text  = (
            f"<blockquote><b>「 ALL CARDS ぁ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎴 Total: <b>{len(items)}</b>\n\n{lines}{extra}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
        )
    else:
        rarity_name = SAFE_RARITY.get(key)
        matched = sorted(
            [(cid, cd) for cid, cd in cards.items() if cd["rarity"].strip() == rarity_name.strip()],
            key=lambda x: (x[1]["anime"], x[1]["name"])
        )
        lines = "\n".join(f"  ✦ <b>{cd['name']}</b> — <i>{cd['anime']}</i>" for _, cd in matched[:80])
        extra = f"\n<i>...and {len(matched)-80} more.</i>" if len(matched) > 80 else ""
        text  = (
            f"<blockquote><b>「 {rarity_name.upper()} ぁ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌟 <b>{rarity_name}</b>\n"
            f"🎴 Total: <b>{len(matched)}</b>\n\n{lines}{extra}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
        )

    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Back", callback_data="back_anime")]]), parse_mode=ParseMode.HTML)

# ==========================================
# INLINE BROWSER EXECUTION
# ==========================================
@main_router.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    user_id      = str(inline_query.from_user.id)
    db           = ensure_user(user_id, inline_query.from_user.first_name, inline_query.from_user.username)
    query        = inline_query.query.strip().lower()
    cards        = db["users"][user_id].get("cards", {})
    global_cards = db.get("global_cards", {})

    results = []
    sort_pref = db["users"][user_id].get("sort_pref", "default")
    items = list(cards.items())
    if sort_pref == "rarity":
        items.sort(key=lambda x: RARITY_ORDER.get(x[1]["rarity"], 99))
    elif sort_pref == "amount":
        items.sort(key=lambda x: x[1]["amount"], reverse=True)
    else:
        items.sort(key=lambda x: x[1]["name"].lower())

    for cid, cdata in items[:50]:
        if query and query not in cdata["name"].lower() and query not in cdata["rarity"].lower():
            continue

        full    = global_cards.get(cid, {})
        file_id = full.get("file_id", "")
        if not file_id or len(file_id) < 10:
            continue

        caption_text = (
            f"<blockquote>🆔 <b>{cid}</b>\n"
            f"👤 <b>{cdata['name']}</b>\n"
            f"📺 {full.get('anime', '?')}\n"
            f"🌟 {cdata['rarity']}\n"
            f"📦 ×{cdata['amount']}</blockquote>"
        )

        results.append(
            InlineQueryResultPhoto(
                id=cid,
                photo_url=file_id if (file_id.startswith("http://") or file_id.startswith("https://")) else f"https://t.me/i/placeholder.jpg",
                thumbnail_url=file_id if (file_id.startswith("http://") or file_id.startswith("https://")) else f"https://t.me/i/placeholder.jpg",
                caption=caption_text,
                parse_mode=ParseMode.HTML
            )
        )

    if not results:
        results.append(InlineQueryResultArticle(
            id="empty",
            title="No cards found",
            description="Try a different search or claim cards first!",
            input_message_content=InputTextMessageContent(
                message_text="No cards match your search. Claim some in the group!",
                parse_mode=ParseMode.HTML
            )
        ))

    try:
        await inline_query.answer(results, cache_time=10, is_personal=True)
    except Exception as e:
        print(f"[INLINE] Error: {e}")

# ==========================================
# ADMINISTRATIVE HANDLERS & UPDATERS
# ==========================================
@main_router.message(Command("add_card"))
async def add_card(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply("<blockquote>⚠️ Reply to an image.\n<code>/add_card Name | Anime | Rarity</code></blockquote>", parse_mode=ParseMode.HTML)
        return
    try:
        args = command.args.split("|")
        char_name, anime_name, rarity = args[0].strip(), args[1].strip(), args[2].strip()
    except Exception:
        await message.reply("<blockquote>⚠️ Format: <code>/add_card Character | Anime | Rarity</code></blockquote>", parse_mode=ParseMode.HTML)
        return

    if rarity not in RARITIES:
        await message.reply(f"<blockquote>⚠️ Invalid rarity! Use one of:\n" + "\n".join(f"  <code>{r}</code>" for r in RARITIES) + "</blockquote>", parse_mode=ParseMode.HTML)
        return

    file_id = message.reply_to_message.photo[-1].file_id
    card_id = f"DB-{str(uuid.uuid4())[:6].upper()}"
    db = load_db()
    db["global_cards"][card_id] = {"name": char_name, "anime": anime_name, "rarity": rarity, "file_id": file_id}
    save_db()

    await message.reply(
        f"<blockquote><b>「 CARD ADDED ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n🆔 <code>{card_id}</code>\n👤 <b>{char_name}</b>\n📺 {anime_name}\n🌟 {rarity}\n━━━━━━━━━━━━━━━━━━━━━━\n✅ Saved!</blockquote>",
        parse_mode=ParseMode.HTML
    )
    try:
        await bot.send_photo(DB_GROUP_ID, photo=file_id, caption=f"<blockquote>🆔 <code>{card_id}</code> | {char_name} | {anime_name} | {rarity}</blockquote>", parse_mode=ParseMode.HTML)
    except Exception: pass

@main_router.message(Command("remove_card"))
async def remove_card(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args:
        await message.reply("<blockquote>⚠️ Format: <code>/remove_card DB-XXXXXX</code></blockquote>", parse_mode=ParseMode.HTML)
        return
    card_id = command.args.split()[0].strip().upper()
    db = load_db()
    if card_id not in db["global_cards"]:
        await message.reply(f"<blockquote>❌ Card <code>{card_id}</code> not found.</blockquote>", parse_mode=ParseMode.HTML)
        return
    removed = db["global_cards"].pop(card_id)
    save_db()
    await message.reply(f"<blockquote>🗑️ Removed: <b>{removed['name']}</b> (<code>{card_id}</code>)</blockquote>", parse_mode=ParseMode.HTML)

@main_router.message(Command("import"))
async def import_cmd(message: Message):
    """Manually imports a database.json file"""
    if message.from_user.id != ADMIN_ID: return
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply("<blockquote>⚠️ Reply to a database.json document to import.</blockquote>", parse_mode=ParseMode.HTML)
        return
        
    doc = message.reply_to_message.document
    if not doc.file_name.endswith(".json"):
        await message.reply("<blockquote>⚠️ File must be a JSON document.</blockquote>", parse_mode=ParseMode.HTML)
        return
        
    msg = await message.reply("<blockquote>📥 Downloading and importing database...</blockquote>", parse_mode=ParseMode.HTML)
    try:
        file_info = await bot.get_file(doc.file_id)
        await bot.download_file(file_info.file_path, destination=DB_FILE)
        
        # Flush the local memory to force reload
        global _db_cache
        _db_cache = None
        load_db()
        
        await msg.edit_text("<blockquote>✅ Database successfully imported and loaded into memory!</blockquote>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"<blockquote>❌ Import failed: {e}</blockquote>", parse_mode=ParseMode.HTML)

@main_router.message(Command("update"))
async def update_cmd(message: Message):
    """Pulls the latest code from GitHub and restarts the bot"""
    if message.from_user.id != ADMIN_ID: return
    
    msg = await message.reply("<blockquote>🔄 Pulling updates from GitHub...</blockquote>", parse_mode=ParseMode.HTML)
    
    process = await asyncio.create_subprocess_shell(
        "git pull", 
        stdout=asyncio.subprocess.PIPE, 
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    out = stdout.decode().strip()
    err = stderr.decode().strip()
    
    res = f"<b>Output:</b>\n<code>{out}</code>\n"
    if err:
        res += f"\n<b>Errors:</b>\n<code>{err}</code>"
        
    await msg.edit_text(f"<blockquote>{res}\n\n🔄 Restarting engine...</blockquote>", parse_mode=ParseMode.HTML)
    
    # Save the database and restart the python process
    _flush_db(force=True)
    os.execv(sys.executable, ['python'] + sys.argv)

@main_router.message(Command("dbcheck"))
async def db_check(message: Message):
    if message.from_user.id != ADMIN_ID: return
    db = load_db()
    rarity_count = {}
    for c in db["global_cards"].values():
        rarity_count[c["rarity"]] = rarity_count.get(c["rarity"], 0) + 1
    top = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards",{})), reverse=True)[:5]
    top_text = "\n".join(f"  {i+1}. {get_mention(uid, v.get('name','User'))} — {len(v.get('cards',{}))} cards" for i,(uid,v) in enumerate(top)) or "  None"
    rarity_text = "\n".join(f"  ✦ {r}: {n}" for r,n in rarity_count.items()) or "  None"
    await message.reply(
        f"<blockquote><b>「 DB OVERVIEW ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n📦 Cards   ┊ <b>{len(db['global_cards'])}</b>\n👥 Users   ┊ <b>{len(db['users'])}</b>\n🏘️ Groups  ┊ <b>{len(db['groups'])}</b>\n🚫 Ghost   ┊ {len(ghost_banned)}\n🔇 Shadow  ┊ {len(shadow_banned)}\n\n🌟 <b>By Rarity:</b>\n{rarity_text}\n\n🏆 <b>Top Collectors:</b>\n{top_text}\n━━━━━━━━━━━━━━━━━━━━━━</blockquote>",
        parse_mode=ParseMode.HTML
    )

@main_router.message(Command("botstats"))
async def bot_stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    db  = load_db()
    sec = int(time.time() - bot_start_time)
    h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    rarity_count = {}
    for c in db["global_cards"].values():
        rarity_count[c["rarity"]] = rarity_count.get(c["rarity"], 0) + 1
    await message.reply(
        f"<blockquote><b>「 BOT STATS ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n⏱️ Uptime    ┊ {h}h {m}m {s}s\n📨 Messages  ┊ {total_messages}\n🎴 Cards     ┊ {len(db['global_cards'])}\n👥 Users     ┊ {len(db['users'])}\n🏘️ Groups    ┊ {len(db['groups'])}\n🔄 AutoLeave ┊ {'✅ ON' if autoleave_enabled else '❌ OFF'}\n\n" + "\n".join(f"  ✦ {r}: <b>{n}</b>" for r,n in rarity_count.items()) + "\n━━━━━━━━━━━━━━━━━━━━━━</blockquote>",
        parse_mode=ParseMode.HTML
    )

@main_router.message(Command("check_db_dupes"))
async def check_db_dupes(message: Message):
    if message.from_user.id != ADMIN_ID: return
    db = load_db()
    seen, dupes = {}, []
    for cid, data in db.get("global_cards",{}).items():
        key = (data["name"].lower().strip(), data["anime"].lower().strip())
        if key in seen: dupes.append((cid, data["name"], data["anime"], seen[key]))
        else: seen[key] = cid
    if not dupes:
        await message.reply("<blockquote>✅ No duplicates found.</blockquote>", parse_mode=ParseMode.HTML)
        return
    text = "<blockquote><b>「 DUPES ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for d in dupes[:15]: text += f"⚠️ <b>{d[1]}</b> ({d[2]})\n  ├ <code>{d[3]}</code>\n  └ <code>{d[0]}</code>\n"
    text += "...and more.</blockquote>" if len(dupes) > 15 else "━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    await message.reply(text, parse_mode=ParseMode.HTML)

@main_router.message(Command("info"))
async def info_cmd(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    db   = load_db()
    if not command.args:
        await message.reply(f"<blockquote><b>「 INFO ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n👥 Users: <b>{len(db['users'])}</b>\n🏘️ Groups: <b>{len(db['groups'])}</b>\n💡 /info &lt;id&gt;</blockquote>", parse_mode=ParseMode.HTML)
        return
    target = command.args.split()[0].strip()
    if target in db["users"]:
        u = db["users"][target]
        await message.reply(f"<blockquote><b>「 USER INFO ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n🆔 <code>{target}</code>\n👤 {get_mention(target, u.get('name','User'))}\n🎴 Cards: {len(u.get('cards',{}))}\n📦 Claimed: {u.get('total_claimed',0)}\n🚫 Ghost: {'Yes' if int(target) in ghost_banned else 'No'}\n🔇 Shadow: {'Yes' if is_shadow_banned(int(target)) else 'No'}</blockquote>", parse_mode=ParseMode.HTML)
        return
    if target in db["groups"]:
        g = db["groups"][target]
        await message.reply(f"<blockquote><b>「 GROUP INFO ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n🆔 <code>{target}</code>\n🏘️ {g.get('title','?')}\n🎴 Drops: {g.get('drops',0)}\n🏆 Claims: {g.get('claims',0)}</blockquote>", parse_mode=ParseMode.HTML)
        return
    await message.reply(f"<blockquote>❌ ID <code>{target}</code> not found.</blockquote>", parse_mode=ParseMode.HTML)

@main_router.message(Command("autoleave"))
async def autoleave_toggle(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    global autoleave_enabled
    if not command.args or command.args.lower() not in ["on", "off"]:
        await message.reply("<blockquote>⚠️ Usage: <code>/autoleave on</code> or <code>off</code></blockquote>", parse_mode=ParseMode.HTML)
        return
    autoleave_enabled = (command.args.lower() == "on")
    db = load_db()
    db["settings"]["autoleave"] = autoleave_enabled
    save_db()
    await message.reply(f"<blockquote>🔄 Auto-leave: {'✅ ON' if autoleave_enabled else '❌ OFF'}\nMin: {AUTOLEAVE_MIN_MEMBERS} members</blockquote>", parse_mode=ParseMode.HTML)

@main_router.message(Command("ghostban"))
async def ghost_ban(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try: target = int(command.args.split()[0].strip())
    except Exception:
        await message.reply("<blockquote>⚠️ Format: <code>/ghostban &lt;id&gt;</code></blockquote>", parse_mode=ParseMode.HTML)
        return
    ghost_banned.add(target)
    await message.reply(f"<blockquote>👻 <code>{target}</code> ghost-banned.</blockquote>", parse_mode=ParseMode.HTML)

@main_router.message(Command("unghostban"))
async def un_ghost_ban(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try: target = int(command.args.split()[0].strip())
    except Exception:
        await message.reply("<blockquote>⚠️ Format: <code>/unghostban &lt;id&gt;</code></blockquote>", parse_mode=ParseMode.HTML)
        return
    ghost_banned.discard(target)
    await message.reply(f"<blockquote>✅ <code>{target}</code> un-ghost-banned.</blockquote>", parse_mode=ParseMode.HTML)

@main_router.message(Command("shadowban"))
async def shadow_ban_cmd(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try: target = int(command.args.split()[0].strip())
    except Exception:
        await message.reply("<blockquote>⚠️ Format: <code>/shadowban &lt;id&gt;</code></blockquote>", parse_mode=ParseMode.HTML)
        return
    shadow_banned[target] = time.time() + SHADOW_BAN_DUR
    await message.reply(f"<blockquote>🔇 <code>{target}</code> shadow-banned 10 min.</blockquote>", parse_mode=ParseMode.HTML)

# ==========================================
# WELCOME CONTROLLERS (/start & /help)
# ==========================================
FINAL_START = (
    "<blockquote><b>「 ANIME NEXUS ぁ 」</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🌸 Welcome to <b>Anime Nexus</b>!\n"
    "The ultimate anime card collecting game.\n\n"
    "⚡ Cards drop in groups every few hundred messages.\n"
    "🎴 Claim them before anyone else does!\n\n"
    "✦ <b>Divine ❄️</b>  — Ultra Rare (2%)\n"
    "✦ <b>Elite ⚓</b>   — Rare (18%)\n"
    "✦ <b>Basic 🃏</b>   — Common (80%)\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "Use /help to see all commands!</blockquote>"
)

@main_router.message(Command("start"))
async def start_cmd(message: Message):
    sent = await message.reply("<blockquote>⠀\n　　　✦\n⠀\n　　<b>Loading...</b></blockquote>", parse_mode=ParseMode.HTML)
    await asyncio.sleep(0.3)
    await sent.edit_text("<blockquote>⠀\n　🌸  <b>A N I M E</b>\n⠀\n　　<b>Initializing...</b></blockquote>", parse_mode=ParseMode.HTML)
    await asyncio.sleep(0.3)
    await sent.edit_text("<blockquote>⠀\n　🌸  <b>A N I M E  N E X U S</b>  🌸\n⠀\n　　<b>Starting engine...</b></blockquote>", parse_mode=ParseMode.HTML)
    await asyncio.sleep(0.4)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📖 Commands", callback_data="show_help"),
        InlineKeyboardButton(text="🎴 My Deck",  callback_data=f"deck_prev_{message.from_user.id}_0"),
    ],[
        InlineKeyboardButton(text="🏆 Leaderboard", callback_data="show_lb"),
    ]])
    await sent.edit_text(FINAL_START, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "show_help")
async def show_help_cb(cq: CallbackQuery):
    await cq.answer()
    await cq.message.edit_text(build_help_text(cq.from_user.id), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Back", callback_data="show_start")]]), parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "show_start")
async def show_start_cb(cq: CallbackQuery):
    await cq.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📖 Commands", callback_data="show_help"),
        InlineKeyboardButton(text="🎴 My Deck",  callback_data=f"deck_prev_{cq.from_user.id}_0"),
    ],[
        InlineKeyboardButton(text="🏆 Leaderboard", callback_data="show_lb"),
    ]])
    await cq.message.edit_text(FINAL_START, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "show_lb")
async def show_lb_cb(cq: CallbackQuery):
    await cq.answer()
    db  = load_db()
    top = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards",{})), reverse=True)[:10]
    medals = ["🥇","🥈","🥉"] + ["🏅"]*7
    text   = "<blockquote><b>「 ANIME NEXUS : LEADERBOARD ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n✦ Top Collectors (Unique Cards)\n\n"
    for i, (uid, ud) in enumerate(top): text += f"{medals[i]} <b>{get_mention(uid, ud.get('name','Unknown'))}</b> — 🎴 {len(ud.get('cards',{}))}\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Back", callback_data="show_start")]]), parse_mode=ParseMode.HTML)

# ==========================================
# /help — Command Reference
# ==========================================
def build_help_text(user_id: int) -> str:
    text = (
        "<blockquote><b>「 ANIME NEXUS : COMMANDS ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👤 <b>Player Commands</b>\n"
        "┊ /check [Name] — Search global database\n"
        "┊ /flex [Name] — Showcase a card you own\n"
        "┊ /profile — Your profile & stats\n"
        "┊ /deck — View your deck summary\n"
        "┊ /card — View classic grid collection\n"
        "┊ /sortcards — Change how your deck is sorted\n"
        "┊ /special [ID] — Set your featured card\n"
        "┊ /leaderboard — Top 10 collectors\n"
        "┊ /cards — Browse database by anime\n\n"
        "🎴 <b>How it works</b>\n"
        "┊ Cards drop every 100–500 messages\n"
        "┊ Tap 💮 CLAIM before anyone else!\n\n"
        "🌟 <b>Rarities</b>\n"
        "┊ ✦ Divine ❄️  — 2% (Ultra Rare)\n"
        "┊ ✦ Elite ⚓   — 18% (Rare)\n"
        "┊ ✦ Basic 🃏   — 80% (Common)\n"
        "━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    )
    if user_id == ADMIN_ID:
        text += (
            "\n<blockquote><b>🛡️ Admin Commands</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "┊ /add_card Name|Anime|Rarity (reply img)\n"
            "┊ /remove_card [ID]\n"
            "┊ /forcedrop — Force a drop\n"
            "┊ /import — Reply to .json file to load\n"
            "┊ /update — Git pull latest code & restart\n"
            "┊ /dbcheck — Database overview\n"
            "┊ /check_db_dupes — Find duplicates\n"
            "┊ /botstats — Bot statistics\n"
            "┊ /info [user/group id]\n"
            "┊ /ghostban /unghostban [id]\n"
            "┊ /shadowban [id]\n"
            "┊ /autoleave on|off\n"
            "━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
        )
    return text

@main_router.message(Command("help"))
async def help_cmd(message: Message):
    await message.reply(build_help_text(message.from_user.id), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Start Menu", callback_data="show_start")]]), parse_mode=ParseMode.HTML)

# ==========================================
# STARTER SYSTEM DEPLOYMENT RUNNERS
# ==========================================
if __name__ == "__main__":
    load_settings()
    dp.message.outer_middleware(GlobalGuardMiddleware())
    dp.include_router(main_router)

    async def main():
        # First, try to restore the DB from the group's pinned messages
        await load_from_group()
        
        # Then, start our background tasks
        asyncio.create_task(periodic_save())
        asyncio.create_task(backup_to_group())
        
        print("🌸 Anime Nexus is running over high speed aiogram v3 engines...")
        await dp.start_polling(bot)

    asyncio.run(main())
