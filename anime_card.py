import json
import random
import uuid
import os
import time
import asyncio
import difflib
from datetime import datetime, timezone
from pyrogram import Client, filters, enums, idle
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    InlineQueryResultArticle, InputTextMessageContent,
    InlineQueryResultCachedPhoto, InlineQueryResultPhoto
)

# ==========================================
# CONFIGURATION
# ==========================================
API_ID      = 1747534
API_HASH    = "5a2684512006853f2e48aca9652d83ea"
BOT_TOKEN   = "7658617809:AAGEYNtWaLh-859dyn4pLcd_7Rdw3mLtWeM"
ADMIN_ID    = 5716292610
DB_GROUP_ID = -1003799799158

app = Client(
    "anime_nexus",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=8
)

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

# 🔥 PERFORMANCE FIX: Caches spoilered images to make drops instant!
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
# DATABASE HELPERS (Optimized for speed)
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
    # 🔥 PERFORMANCE FIX: We don't force flush here anymore so bot doesn't freeze

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
        await asyncio.sleep(5)
        if _db_dirty:
            # 🔥 PERFORMANCE FIX: Runs file saving in background thread
            await asyncio.to_thread(_flush_db)

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

@app.on_callback_query(filters.regex(r"^close_msg$"))
async def close_msg_cb(client, callback_query: CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception:
        pass

# ==========================================
# BAN & AUTOLEAVE HELPERS
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

async def check_autoleave(client, chat_id: int) -> bool:
    if not autoleave_enabled:
        return False

    now = time.time()
    cached_count, last_checked = group_member_cache.get(chat_id, (None, 0))

    if cached_count is not None and (now - last_checked) < MEMBER_CACHE_TTL:
        count = cached_count
    else:
        try:
            chat = await client.get_chat(chat_id)
            count = getattr(chat, "members_count", None)
            if count is not None:
                group_member_cache[chat_id] = (count, now)
        except Exception:
            return False

    if count is not None and count < AUTOLEAVE_MIN_MEMBERS:
        try:
            await client.send_message(
                chat_id,
                "<blockquote><b>「 ANIME NEXUS ぁ 」</b>\n\n"
                "⚠️ This group has fewer than <b>40 members</b>.\n"
                "I'm leaving now — さようなら 👋</blockquote>",
                parse_mode=enums.ParseMode.HTML
            )
            await client.leave_chat(chat_id)
            return True
        except Exception:
            pass
    return False

# ==========================================
# SPAM GUARD
# ==========================================
@app.on_message(filters.group & ~filters.bot, group=-1)
async def spam_guard(client, message):
    uid = message.from_user.id if message.from_user else None
    if not uid:
        return

    if await check_autoleave(client, message.chat.id):
        message.stop_propagation()
        return

    if is_ghost_banned(uid):
        try:
            await message.delete()
        except Exception:
            pass
        message.stop_propagation()
        return

    if check_spam(uid):
        try:
            await message.reply(
                "<blockquote><b>⚠️ Shadow Banned ぁ</b>\n"
                "You are sending messages too fast.\n"
                "Restricted for <b>10 minutes</b>. 🔇</blockquote>",
                parse_mode=enums.ParseMode.HTML,
                quote=True
            )
        except Exception:
            pass
        message.stop_propagation()
        return

    if is_shadow_banned(uid):
        try:
            await message.delete()
        except Exception:
            pass
        message.stop_propagation()
        return

# ==========================================
# MESSAGE COUNTER
# ==========================================
@app.on_message(filters.group & ~filters.bot, group=1)
async def message_counter(client, message):
    global total_messages
    total_messages += 1
    if not message.from_user:
        return
    chat_id = str(message.chat.id)
    ensure_group(message.chat.id, message.chat.title or "Unknown")
    group_counters.setdefault(chat_id, {"count": 0, "target": random.randint(100, 500)})
    group_counters[chat_id]["count"] += 1
    if group_counters[chat_id]["count"] >= group_counters[chat_id]["target"]:
        group_counters[chat_id] = {"count": 0, "target": random.randint(100, 500)}
        asyncio.create_task(trigger_drop(client, message.chat.id))

# ==========================================
# DROP ENGINE (High Speed)
# ==========================================
async def trigger_drop(client, chat_id):
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

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("💮  C L A I M  C A R D  💮", callback_data=f"claim_{card_id}")
    ]])

    try:
        original_file_id = card_data["file_id"]
        
        # FAST PATH: Cached spoiler file ID
        if original_file_id in spoiler_cache:
            msg = await client.send_photo(
                chat_id,
                photo=spoiler_cache[original_file_id], 
                caption=caption,
                reply_markup=keyboard,
                parse_mode=enums.ParseMode.HTML,
                has_spoiler=True
            )
        # SLOW PATH: First time dropping since restart
        else:
            downloaded_file = await client.download_media(original_file_id, in_memory=True)
            msg = await client.send_photo(
                chat_id,
                photo=downloaded_file, 
                caption=caption,
                reply_markup=keyboard,
                parse_mode=enums.ParseMode.HTML,
                has_spoiler=True
            )
            # Save for instant drops later
            spoiler_cache[original_file_id] = msg.photo.file_id

        active_drops[msg.id] = card_id

        cid = str(chat_id)
        if cid in db["groups"]:
            db["groups"][cid]["drops"] = db["groups"][cid].get("drops", 0) + 1
            save_db()
    except Exception as e:
        print(f"[DROP] Error: {e}")

# ==========================================
# CLAIM CALLBACK
# ==========================================
@app.on_callback_query(filters.regex(r"^claim_"))
async def claim_card(client, callback_query: CallbackQuery):
    msg_id  = callback_query.message.id
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
        print(f"[CLAIM] Could not remove keyboard: {e}")

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
        await callback_query.message.reply(
            winner_text,
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
    except Exception as e:
        print(f"[CLAIM] Error sending winner message: {e}")

# ==========================================
# /flex (Searches ONLY owned cards)
# ==========================================
@app.on_message(filters.command("flex"))
async def flex_cmd(client, message):
    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.reply("<blockquote>⚠️ <b>Usage:</b> <code>/flex <card name></code></blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
        return

    query = args[1].lower().strip()
    my_cards = db["users"][user_id].get("cards", {})
    
    if not my_cards:
        await message.reply("<blockquote>❌ You don't own any cards yet!</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
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
        await message.reply(f"<blockquote>❌ You do not own a card matching <b>{args[1]}</b>.</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
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

    await message.reply_photo(
        photo=global_data.get("file_id"),
        caption=caption,
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )

# ==========================================
# /forcedrop
# ==========================================
@app.on_message(filters.command("forcedrop") & filters.user(ADMIN_ID))
async def force_drop_cmd(client, message):
    try:
        await message.delete()
    except Exception:
        pass

    db = load_db()
    if not db.get("global_cards"):
        await message.reply(
            "<blockquote>⚠️ No cards in database. Use <code>/add_card</code> first.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
        return

    if message.chat.type == enums.ChatType.PRIVATE:
        args = message.text.split()
        if len(args) < 2:
            await message.reply(
                "<blockquote><b>「 FORCE DROP ぁ 」</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "In DM, provide the group ID:\n"
                "<code>/forcedrop -100XXXXXXXXXX</code>\n"
                "━━━━━━━━━━━━━━━━━━━━━━</blockquote>",
                parse_mode=enums.ParseMode.HTML,
                quote=True
            )
            return
        try:
            target_chat = int(args[1])
        except ValueError:
            await message.reply("<blockquote>⚠️ Invalid chat ID.</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
            return
        await trigger_drop(client, target_chat)
        await message.reply(
            f"<blockquote>✅ Drop triggered in <code>{target_chat}</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
    else:
        await trigger_drop(client, message.chat.id)


# ==========================================
# DECK (/deck)
# ==========================================
DECK_PER_PAGE = 10

async def send_deck_page(client, message, db, user_id, page=0, edit=False):
    user_data = db["users"][user_id]
    cards     = user_data.get("cards", {})
    items     = list(cards.items())
    user_name = user_data.get('name', 'User')

    if not items:
        text = (
            "<blockquote><b>「 COLLECTION EMPTY ぁ 」</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "You haven't collected any cards yet!\n"
            "Wait for a drop in the group.</blockquote>"
        )
        if edit:
            await message.edit_text(text, parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply(text, parse_mode=enums.ParseMode.HTML, quote=True)
        return

    # User's Sorting Preference
    sort_pref = user_data.get("sort_pref", "default")
    if sort_pref == "rarity":
        items.sort(key=lambda x: RARITY_ORDER.get(x[1]["rarity"], 99))
    elif sort_pref == "name":
        items.sort(key=lambda x: x[1]["name"].lower())
    elif sort_pref == "amount":
        items.sort(key=lambda x: x[1]["amount"], reverse=True)

    # Extract special card so we can pin it at the top
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
    text = (
        f"『  ぁ 𝘾𝘼𝙍𝘿 𝘿𝙀𝘾𝙆  - {safe_name} 』\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
    )

    # Pin special card on the very first page
    if page == 0 and special_item:
        scid, scdata = special_item
        text += f"✨ {scdata['name']} - [{scdata['rarity']}]  ×{scdata['amount']}\n"

    for cid, cdata in page_items:
        text += f"✦ {cdata['name']} - [{cdata['rarity']}]  ×{cdata['amount']}\n"

    text += "\n━━━━━━━━━━━━━━━━━━━"

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("❮", callback_data=f"deck_prev_{user_id}_{page-1}"))
    else:
        nav_buttons.append(InlineKeyboardButton("❮", callback_data="noop"))

    if end < len(items):
        nav_buttons.append(InlineKeyboardButton("❯", callback_data=f"deck_next_{user_id}_{page+1}"))
    else:
        nav_buttons.append(InlineKeyboardButton("❯", callback_data="noop"))

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⌈ 𝗣𝗮𝗴𝗲 {page+1}/{total_pages} ⌋", callback_data=f"page_alert_{page+1}")],
        nav_buttons,
        [InlineKeyboardButton("View collection", switch_inline_query_current_chat="")]
    ])

    if edit:
        await message.edit_text(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML, quote=True)

@app.on_message(filters.command("deck"))
async def view_deck_cmd(client, message):
    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    await send_deck_page(client, message, db, user_id, page=0, edit=False)

@app.on_callback_query(filters.regex(r"^deck_(prev|next)_(\d+)_(\d+)$"))
async def deck_nav_cb(client, callback_query: CallbackQuery):
    _, direction, owner_id, page_str = callback_query.data.split("_")
    if str(callback_query.from_user.id) != owner_id:
        await callback_query.answer("❌ Not your deck!", show_alert=True)
        return
    db = load_db()
    await send_deck_page(client, callback_query.message, db, owner_id, int(page_str), edit=True)
    await callback_query.answer()

@app.on_callback_query(filters.regex(r"^page_alert_(\d+)$"))
async def page_indicator_alert(client, callback_query):
    page_num = callback_query.data.split("_")[2]
    await callback_query.answer(f"ℹ️ You are currently on page {page_num}.", show_alert=True)

@app.on_callback_query(filters.regex(r"^noop$"))
async def noop_cb(client, callback_query: CallbackQuery):
    await callback_query.answer()


# ==========================================
# /sortcards (Silently saves preference)
# ==========================================
@app.on_message(filters.command("sortcards"))
async def sort_cards(client, message):
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
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌟 Rarity", callback_data=f"setsort_{user_id}_rarity"),
            InlineKeyboardButton("🔤 Name", callback_data=f"setsort_{user_id}_name")
        ],
        [
            InlineKeyboardButton("📦 Amount", callback_data=f"setsort_{user_id}_amount"),
            InlineKeyboardButton("🔄 Default", callback_data=f"setsort_{user_id}_default")
        ]
    ])
    
    await message.reply(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML, quote=True)

@app.on_callback_query(filters.regex(r"^setsort_(\d+)_(rarity|name|amount|default)$"))
async def set_sort_cb(client, callback_query: CallbackQuery):
    owner_id = callback_query.matches[0].group(1)
    mode     = callback_query.matches[0].group(2)
    
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
    await callback_query.message.edit_text(text, reply_markup=callback_query.message.reply_markup, parse_mode=enums.ParseMode.HTML)


# ==========================================
# /profile
# ==========================================
@app.on_message(filters.command("profile"))
async def view_profile(client, message):
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
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Close", callback_data="close_msg")]])

    photo_sent = False
    try:
        async for photo in client.get_chat_photos(int(user_id), limit=1):
            await message.reply_photo(
                photo=photo.file_id,
                caption=profile_text,
                reply_markup=keyboard,
                parse_mode=enums.ParseMode.HTML,
                quote=True
            )
            photo_sent = True
            break
    except Exception:
        pass

    if not photo_sent:
        await message.reply(profile_text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML, quote=True)


# ==========================================
# /special
# ==========================================
@app.on_message(filters.command("special"))
async def set_special(client, message):
    user_id = str(message.from_user.id)
    db      = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    try:
        target_card = message.text.split()[1].strip().upper()
    except IndexError:
        await message.reply(
            "<blockquote>⚠️ Format: <code>/special DB-XXXXXX</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
        return

    if target_card not in db["users"][user_id]["cards"]:
        await message.reply(
            "<blockquote>❌ You don't own that card!</blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
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
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )

# ==========================================
# /leaderboard / /top
# ==========================================
@app.on_message(filters.command("leaderboard") | filters.command("top"))
async def leaderboard(client, message):
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
    await message.reply(text, parse_mode=enums.ParseMode.HTML, quote=True)


# ==========================================
# /cards — anime browser + rarity drill-down
# ==========================================
BROWSE_PER_PAGE = 15

@app.on_message(filters.command("cards") | filters.command("total_cards") | filters.command("all_cards"))
async def cards_browser(client, message):
    db = load_db()
    if not db.get("global_cards"):
        await message.reply("<blockquote>⚠️ Database is empty.</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
        return
    await show_anime_list(client, message, edit=False)

async def show_anime_list(client, message, edit=False):
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
        row.append(InlineKeyboardButton(f"📺 {label}", callback_data=f"an|{safe}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton("✦ All Divine ❄️", callback_data="gr|divine"),
        InlineKeyboardButton("✦ All Elite ⚓",  callback_data="gr|elite"),
    ])
    buttons.append([
        InlineKeyboardButton("✦ All Basic 🃏",  callback_data="gr|basic"),
        InlineKeyboardButton("📋 Full List",    callback_data="gr|all"),
    ])

    if edit:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML, quote=True)

@app.on_callback_query(filters.regex(r"^an\|"))
async def anime_rarity_picker(client, cq: CallbackQuery):
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

    lines = []
    for r in RARITIES:
        n = rarity_count.get(r.strip(), 0)
        lines.append(f"  ✦ <b>{r}</b>  ┊  {n} card{'s' if n!=1 else ''}")

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
            buttons.append([InlineKeyboardButton(
                f"✦ {r}  ({n} cards)",
                callback_data=f"acl|{safe_anime}|{safe_r}|0"
            )])

    buttons.append([InlineKeyboardButton("◀️ Back to Anime List", callback_data="back_anime")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

@app.on_callback_query(filters.regex(r"^back_anime$"))
async def back_to_anime(client, cq: CallbackQuery):
    await cq.answer()
    await show_anime_list(client, cq.message, edit=True)

@app.on_callback_query(filters.regex(r"^acl\|"))
async def anime_card_list(client, cq: CallbackQuery):
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

    lines = "\n".join(
        f"  ✦ <b>{cd['name']}</b>  <code>{cid}</code>"
        for cid, cd in matched[start:end]
    )

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
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"acl|{safe_anime}|{safe_r}|{page-1}"))
    if end < total_m:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"acl|{safe_anime}|{safe_r}|{page+1}"))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("◀️ Back to Rarity", callback_data=f"an|{safe_anime}")])
    buttons.append([InlineKeyboardButton("🏠 Anime List",      callback_data="back_anime")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

@app.on_callback_query(filters.regex(r"^gr\|"))
async def global_rarity(client, cq: CallbackQuery):
    await cq.answer()
    key   = cq.data[3:]
    db    = load_db()
    cards = db.get("global_cards", {})

    if key == "all":
        items = sorted(cards.items(), key=lambda x: (x[1]["anime"], x[1]["name"]))
        lines = "\n".join(
            f"  ✦ <b>{cd['name']}</b> — <i>{cd['anime']}</i>  [{cd['rarity']}]"
            for _, cd in items[:80]
        )
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
        lines = "\n".join(
            f"  ✦ <b>{cd['name']}</b> — <i>{cd['anime']}</i>"
            for _, cd in matched[:80]
        )
        extra = f"\n<i>...and {len(matched)-80} more.</i>" if len(matched) > 80 else ""
        text  = (
            f"<blockquote><b>「 {rarity_name.upper()} ぁ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌟 <b>{rarity_name}</b>\n"
            f"🎴 Total: <b>{len(matched)}</b>\n\n{lines}{extra}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
        )

    await cq.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="back_anime")]]),
        parse_mode=enums.ParseMode.HTML
    )


# ==========================================
# INLINE QUERY
# ==========================================
@app.on_inline_query()
async def inline_query_handler(client, inline_query):
    user_id      = str(inline_query.from_user.id)
    db           = ensure_user(user_id, inline_query.from_user.first_name, inline_query.from_user.username)
    query        = inline_query.query.strip().lower()
    cards        = db["users"][user_id].get("cards", {})
    global_cards = db.get("global_cards", {})

    results = []

    # Sort matching deck preference (fallback to name if not set)
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

        if file_id.startswith("http://") or file_id.startswith("https://"):
            results.append(
                InlineQueryResultPhoto(
                    id=cid,
                    photo_url=file_id,
                    thumb_url=file_id,
                    caption=caption_text,
                    parse_mode=enums.ParseMode.HTML
                )
            )
        else:
            results.append(
                InlineQueryResultCachedPhoto(
                    id=cid,
                    photo_file_id=file_id,
                    caption=caption_text,
                    parse_mode=enums.ParseMode.HTML
                )
            )

    if not results:
        results.append(InlineQueryResultArticle(
            id="empty",
            title="No cards found",
            description="Try a different search or claim cards first!",
            input_message_content=InputTextMessageContent(
                "No cards match your search. Claim some in the group!",
                parse_mode=enums.ParseMode.HTML
            )
        ))

    try:
        await inline_query.answer(results, cache_time=10, is_personal=True)
    except Exception as e:
        print(f"[INLINE] Error answering inline query: {e}")


# ==========================================
# ADMIN COMMANDS
# ==========================================
@app.on_message(filters.command("add_card") & filters.user(ADMIN_ID))
async def add_card(client, message):
    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply(
            "<blockquote>⚠️ Reply to an image.\n<code>/add_card Name | Anime | Rarity</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
        return
    try:
        args = message.text.split(" ", 1)[1].split("|")
        char_name, anime_name, rarity = args[0].strip(), args[1].strip(), args[2].strip()
    except Exception:
        await message.reply(
            "<blockquote>⚠️ Format: <code>/add_card Character | Anime | Rarity</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
        return

    if rarity not in RARITIES:
        await message.reply(
            f"<blockquote>⚠️ Invalid rarity! Use one of:\n"
            + "\n".join(f"  <code>{r}</code>" for r in RARITIES)
            + "</blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
        return

    file_id = message.reply_to_message.photo.file_id
    card_id = f"DB-{str(uuid.uuid4())[:6].upper()}"
    db = load_db()
    db["global_cards"][card_id] = {
        "name": char_name, "anime": anime_name, "rarity": rarity, "file_id": file_id
    }
    save_db()

    await message.reply(
        f"<blockquote><b>「 CARD ADDED ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <code>{card_id}</code>\n"
        f"👤 <b>{char_name}</b>\n"
        f"📺 {anime_name}\n"
        f"🌟 {rarity}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Saved!</blockquote>",
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )
    try:
        await client.send_photo(
            DB_GROUP_ID, photo=file_id,
            caption=f"<blockquote>🆔 <code>{card_id}</code> | {char_name} | {anime_name} | {rarity}</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass

@app.on_message(filters.command("remove_card") & filters.user(ADMIN_ID))
async def remove_card(client, message):
    try:
        card_id = message.text.split(" ", 1)[1].strip().upper()
    except IndexError:
        await message.reply("<blockquote>⚠️ Format: <code>/remove_card DB-XXXXXX</code></blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
        return
    db = load_db()
    if card_id not in db["global_cards"]:
        await message.reply(f"<blockquote>❌ Card <code>{card_id}</code> not found.</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
        return
    removed = db["global_cards"].pop(card_id)
    save_db()
    await message.reply(f"<blockquote>🗑️ Removed: <b>{removed['name']}</b> (<code>{card_id}</code>)</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)

@app.on_message(filters.command("dbcheck") & filters.user(ADMIN_ID))
async def db_check(client, message):
    db = load_db()
    rarity_count = {}
    for c in db["global_cards"].values():
        rarity_count[c["rarity"]] = rarity_count.get(c["rarity"], 0) + 1
    top = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards",{})), reverse=True)[:5]
    top_text = "\n".join(
        f"  {i+1}. {get_mention(uid, v.get('name','User'))} — {len(v.get('cards',{}))} cards"
        for i,(uid,v) in enumerate(top)
    ) or "  None"
    rarity_text = "\n".join(f"  ✦ {r}: {n}" for r,n in rarity_count.items()) or "  None"
    await message.reply(
        f"<blockquote><b>「 DB OVERVIEW ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Cards   ┊ <b>{len(db['global_cards'])}</b>\n"
        f"👥 Users   ┊ <b>{len(db['users'])}</b>\n"
        f"🏘️ Groups  ┊ <b>{len(db['groups'])}</b>\n"
        f"🚫 Ghost   ┊ {len(ghost_banned)}\n"
        f"🔇 Shadow  ┊ {len(shadow_banned)}\n\n"
        f"🌟 <b>By Rarity:</b>\n{rarity_text}\n\n"
        f"🏆 <b>Top Collectors:</b>\n{top_text}\n━━━━━━━━━━━━━━━━━━━━━━</blockquote>",
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )

@app.on_message(filters.command("botstats") & filters.user(ADMIN_ID))
async def bot_stats(client, message):
    db  = load_db()
    sec = int(time.time() - bot_start_time)
    h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    rarity_count = {}
    for c in db["global_cards"].values():
        rarity_count[c["rarity"]] = rarity_count.get(c["rarity"], 0) + 1
    await message.reply(
        f"<blockquote><b>「 BOT STATS ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Uptime    ┊ {h}h {m}m {s}s\n"
        f"📨 Messages  ┊ {total_messages}\n"
        f"🎴 Cards     ┊ {len(db['global_cards'])}\n"
        f"👥 Users     ┊ {len(db['users'])}\n"
        f"🏘️ Groups    ┊ {len(db['groups'])}\n"
        f"🔄 AutoLeave ┊ {'✅ ON' if autoleave_enabled else '❌ OFF'}\n\n"
        + "\n".join(f"  ✦ {r}: <b>{n}</b>" for r,n in rarity_count.items())
        + "\n━━━━━━━━━━━━━━━━━━━━━━</blockquote>",
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )

@app.on_message(filters.command("check_db_dupes") & filters.user(ADMIN_ID))
async def check_db_dupes(client, message):
    db = load_db()
    seen, dupes = {}, []
    for cid, data in db.get("global_cards",{}).items():
        key = (data["name"].lower().strip(), data["anime"].lower().strip())
        if key in seen:
            dupes.append((cid, data["name"], data["anime"], seen[key]))
        else:
            seen[key] = cid
    if not dupes:
        await message.reply("<blockquote>✅ No duplicates found.</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
        return
    text = "<blockquote><b>「 DUPES ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for d in dupes[:15]:
        text += f"⚠️ <b>{d[1]}</b> ({d[2]})\n  ├ <code>{d[3]}</code>\n  └ <code>{d[0]}</code>\n"
    if len(dupes) > 15:
        text += f"...and {len(dupes)-15} more.</blockquote>"
    else:
        text += "━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    await message.reply(text, parse_mode=enums.ParseMode.HTML, quote=True)

@app.on_message(filters.command("info") & filters.user(ADMIN_ID))
async def info_cmd(client, message):
    db   = load_db()
    args = message.text.split()
    if len(args) < 2:
        await message.reply(
            f"<blockquote><b>「 INFO ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Users: <b>{len(db['users'])}</b>\n🏘️ Groups: <b>{len(db['groups'])}</b>\n"
            f"💡 /info &lt;user_id&gt; or /info &lt;group_id&gt;</blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
        return
    target = args[1].strip()
    if target in db["users"]:
        u = db["users"][target]
        await message.reply(
            f"<blockquote><b>「 USER INFO ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <code>{target}</code>\n👤 {get_mention(target, u.get('name','User'))}\n"
            f"🎴 Cards: {len(u.get('cards',{}))}\n📦 Claimed: {u.get('total_claimed',0)}\n"
            f"🚫 Ghost: {'Yes' if int(target) in ghost_banned else 'No'}\n"
            f"🔇 Shadow: {'Yes' if is_shadow_banned(int(target)) else 'No'}</blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
        return
    if target in db["groups"]:
        g = db["groups"][target]
        await message.reply(
            f"<blockquote><b>「 GROUP INFO ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <code>{target}</code>\n🏘️ {g.get('title','?')}\n"
            f"🎴 Drops: {g.get('drops',0)}\n🏆 Claims: {g.get('claims',0)}</blockquote>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )
        return
    await message.reply(f"<blockquote>❌ ID <code>{target}</code> not found.</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)

@app.on_message(filters.command("autoleave") & filters.user(ADMIN_ID))
async def autoleave_toggle(client, message):
    global autoleave_enabled
    args = message.text.split()
    if len(args) < 2 or args[1].lower() not in ("on","off"):
        await message.reply("<blockquote>⚠️ Usage: <code>/autoleave on</code> or <code>off</code></blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
        return
    autoleave_enabled = (args[1].lower() == "on")
    db = load_db()
    db["settings"]["autoleave"] = autoleave_enabled
    save_db()
    await message.reply(
        f"<blockquote>🔄 Auto-leave: {'✅ ON' if autoleave_enabled else '❌ OFF'}\nMin: {AUTOLEAVE_MIN_MEMBERS} members</blockquote>",
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )

@app.on_message(filters.command("ghostban") & filters.user(ADMIN_ID))
async def ghost_ban(client, message):
    try:
        target = int(message.text.split(" ",1)[1].strip())
    except Exception:
        await message.reply("<blockquote>⚠️ Format: <code>/ghostban &lt;id&gt;</code></blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
        return
    ghost_banned.add(target)
    await message.reply(f"<blockquote>👻 <code>{target}</code> ghost-banned.</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)

@app.on_message(filters.command("unghostban") & filters.user(ADMIN_ID))
async def un_ghost_ban(client, message):
    try:
        target = int(message.text.split(" ",1)[1].strip())
    except Exception:
        await message.reply("<blockquote>⚠️ Format: <code>/unghostban &lt;id&gt;</code></blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
        return
    ghost_banned.discard(target)
    await message.reply(f"<blockquote>✅ <code>{target}</code> un-ghost-banned.</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)

@app.on_message(filters.command("shadowban") & filters.user(ADMIN_ID))
async def shadow_ban_cmd(client, message):
    try:
        target = int(message.text.split(" ",1)[1].strip())
    except Exception:
        await message.reply("<blockquote>⚠️ Format: <code>/shadowban &lt;id&gt;</code></blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)
        return
    shadow_banned[target] = time.time() + SHADOW_BAN_DUR
    await message.reply(f"<blockquote>🔇 <code>{target}</code> shadow-banned 10 min.</blockquote>", parse_mode=enums.ParseMode.HTML, quote=True)


# ==========================================
# /start — Menu Structure
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

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    sent = await message.reply(
        "<blockquote>⠀\n　　　✦\n⠀\n　　<b>Loading...</b></blockquote>",
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )
    await asyncio.sleep(0.6)
    await sent.edit_text(
        "<blockquote>⠀\n　🌸  <b>A N I M E</b>\n⠀\n　　<b>Initializing...</b></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )
    await asyncio.sleep(0.6)
    await sent.edit_text(
        "<blockquote>⠀\n　🌸  <b>A N I M E  N E X U S</b>  🌸\n⠀\n　　<b>Starting engine...</b></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )
    await asyncio.sleep(0.7)
    await sent.edit_text(
        FINAL_START,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📖 Commands", callback_data="show_help"),
            InlineKeyboardButton("🎴 My Deck",  callback_data=f"deck_prev_{message.from_user.id}_1"),
        ],[
            InlineKeyboardButton("🏆 Leaderboard", callback_data="show_lb"),
        ]])
    )

@app.on_callback_query(filters.regex(r"^show_help$"))
async def show_help_cb(client, cq: CallbackQuery):
    await cq.answer()
    await cq.message.edit_text(
        build_help_text(cq.from_user.id),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 Back", callback_data="show_start")
        ]])
    )

@app.on_callback_query(filters.regex(r"^show_start$"))
async def show_start_cb(client, cq: CallbackQuery):
    await cq.answer()
    await cq.message.edit_text(
        FINAL_START,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📖 Commands", callback_data="show_help"),
            InlineKeyboardButton("🎴 My Deck",  callback_data=f"deck_prev_{cq.from_user.id}_1"),
        ],[
            InlineKeyboardButton("🏆 Leaderboard", callback_data="show_lb"),
        ]])
    )

@app.on_callback_query(filters.regex(r"^show_lb$"))
async def show_lb_cb(client, cq: CallbackQuery):
    await cq.answer()
    db  = load_db()
    top = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards",{})), reverse=True)[:10]
    medals = ["🥇","🥈","🥉"] + ["🏅"]*7
    text   = (
        "<blockquote><b>「 ANIME NEXUS : LEADERBOARD ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✦ Top Collectors (Unique Cards)\n\n"
    )
    for i, (uid, ud) in enumerate(top):
        text += f"{medals[i]} <b>{get_mention(uid, ud.get('name','Unknown'))}</b> — 🎴 {len(ud.get('cards',{}))}\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━━━</blockquote>"
    await cq.message.edit_text(
        text, parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back", callback_data="show_start")]])
    )

# ==========================================
# /help — Command Reference
# ==========================================
def build_help_text(user_id: int) -> str:
    text = (
        "<blockquote><b>「 ANIME NEXUS : COMMANDS ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👤 <b>Player Commands</b>\n"
        "┊ /flex [Name] — Showcase a card you own\n"
        "┊ /profile — Your profile & stats\n"
        "┊ /deck — View your deck summary\n"
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

@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply(
        build_help_text(message.from_user.id),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 Start Menu", callback_data="show_start"),
        ]]),
        quote=True
    )

# ==========================================
# MAIN EXECUTION ROUTINE
# ==========================================
if __name__ == "__main__":
    load_settings()

    async def _runner():
        asyncio.create_task(periodic_save())
        await app.start()
        print("🌸 Anime Nexus is running...")
        await idle()
        _flush_db(force=True)
        await app.stop()

    app.run(_runner())
