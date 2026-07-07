import io
import os
import sys
import time
import uuid
import asyncio
import traceback
import random
import difflib
from datetime import datetime, timezone
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode, ChatType
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

import config # 👈 FIXED: Added the missing config import

from config import (
    bot, main_router, ADMIN_IDS, SUPREME_OWNER_ID, DB_GROUP_ID,
    RARITIES, format_rarity, load_db, save_db, perform_backup,
    get_mention, resolve_target, bot_start_time, DB_FILE,
    BROWSE_PER_PAGE, RARITY_SAFE, SAFE_RARITY,
    is_ghost_banned, is_shadow_banned,
    parse_gban_duration_token, format_duration_seconds
)

from handlers import trigger_drop

# ==========================================
# SAFE ANIME-NAME <-> CALLBACK KEY MAPPING
# ==========================================
# Telegram callback_data has a hard 64-byte limit, so long anime titles
# can't be embedded directly. Previously this was "solved" by truncating
# the name to 35 characters, which silently corrupted any title longer
# than that (e.g. "The Angel Next Door Spoils Me Rotten" — 36 chars) and
# broke exact-match lookups downstream. Instead, we use a short stable
# hash of the full name as the callback key, and resolve it back to the
# real name on demand by hashing the live anime list and matching.
import hashlib

def anime_hash_key(anime_name: str) -> str:
    """Short, stable, collision-resistant key safe for callback_data."""
    return hashlib.md5(anime_name.encode("utf-8")).hexdigest()[:12]


def anime_key_lookup(db: dict, anime_key: str) -> str | None:
    """Resolves a hash key back to the full, untruncated anime name by
    checking it against every anime title currently in global_cards."""
    cards = db.get("global_cards", {})
    anime_titles = set(c["anime"] for c in cards.values())
    for anime in anime_titles:
        if anime_hash_key(anime) == anime_key:
            return anime
    return None


# ==========================================
# CENTRALISED DB-GROUP ACTIVITY LOGGER
# ==========================================
async def send_log(text: str):
    """Send a structured log message to the backup/log group. Silent on failure."""
    try:
        await bot.send_message(
            chat_id=config.DATABASE_BACKUP_ID,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"[LOG] Failed to send log to backup group: {e}")

# ==========================================
# POWERFUL EVALUATION COMMAND (/eval)
# ==========================================
@main_router.message(Command("eval"))
async def eval_cmd(message: Message, command: CommandObject):
    if message.from_user.id != SUPREME_OWNER_ID: return
    if not command.args:
        await message.reply("⚠️ Provide code to evaluate.", parse_mode=ParseMode.HTML)
        return

    code = command.args
    old_stdout = sys.stdout
    redirected_output = sys.stdout = io.StringIO()

    variables = {
        'bot': bot,
        'message': message,
        'command': command,
        'config': config,
        'db': load_db(),
        'save_db': save_db,
        'asyncio': asyncio,
        'sys': sys,
        'os': os,
        'time': time
    }

    formatted_code = f"async def _eval_expr():\n" + "".join(f"    {line}\n" for line in code.split("\n"))

    try:
        exec(formatted_code, variables)
        _eval_expr = variables['_eval_expr']
        result = await _eval_expr()
        stdout_val = redirected_output.getvalue()
    except Exception:
        stdout_val = redirected_output.getvalue()
        result = traceback.format_exc()
    finally:
        sys.stdout = old_stdout

    output = stdout_val.strip() or str(result)
    if len(output) > 3000:
        output = output[:3000] + "\n... truncated due to size limit ..."

    await message.reply(
        f"<b>📝 Evaluation Output:</b>\n"
        f"<code>{output}</code>",
        parse_mode=ParseMode.HTML
    )

# ==========================================
# SYSTEM MAINTENANCE TOOLS
# ==========================================
@main_router.message(Command("ping"))
async def ping_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    start_time = time.time()
    msg = await message.reply("⚡ <b>Measuring latency...</b>", parse_mode=ParseMode.HTML)
    latency = round((time.time() - start_time) * 1000)
    await msg.edit_text(
        f"🏓 <b>Pong!</b>\nLatency is <b>{latency}ms</b>.", 
        parse_mode=ParseMode.HTML
    )

@main_router.message(Command("refresh"))
async def refresh_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.reply("🔄 <b>Synchronizing cache & hot-restarting bot engines...</b>", parse_mode=ParseMode.HTML)
    
    save_db()
    await perform_backup()
    
    os.execv(sys.executable, [sys.executable] + sys.argv)

@main_router.message(Command("cleangroups"))
async def clean_groups_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    msg = await message.reply("🧹 <b>Scanning database records for invalid group memberships...</b>", parse_mode=ParseMode.HTML)
    
    db = load_db()
    inactive_groups = []
    
    for gid in list(db.get("groups", {}).keys()):
        try:
            await bot.get_chat_member(chat_id=int(gid), user_id=message.bot.id)
        except Exception:
            inactive_groups.append(gid)
            
    for gid in inactive_groups:
        db["groups"].pop(gid, None)
        
    save_db()
    await msg.edit_text(
        f"✅ <b>Cleanup Complete!</b>\n"
        f"Removed <b>{len(inactive_groups)} groups</b> where the bot is no longer present.", 
        parse_mode=ParseMode.HTML
    )

# ==========================================
# ADMINISTRATIVE PICTURE AND SYSTEM TOOLS
# ==========================================
@main_router.message(Command("up"))
async def update_pictures(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    
    valid_modes = ["st", "hp", "lb", "sm", "store", "onst", "offst"]
    if not command.args or command.args.lower() not in valid_modes:
        await message.reply(
            "⚠️ <b>Usage:</b> Reply to a photo with <code>/up &lt;mode&gt;</code>\n\n"
            "<b>Valid Modes:</b>\n"
            "• <code>st</code>: Start Page Pic\n"
            "• <code>hp</code>: Help Page Pic\n"
            "• <code>lb</code>: Leaderboard Page Pic\n"
            "• <code>sm</code>: Stock Market Pic\n"
            "• <code>store</code>: Main Store Pic\n"
            "• <code>onst</code>: Online Store Pic\n"
            "• <code>offst</code>: Offline Store Pic", 
            parse_mode=ParseMode.HTML
        )
        return
    
    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply("⚠️ You must reply to an image to update the picture parameter.", parse_mode=ParseMode.HTML)
        return
        
    mode = command.args.lower()
    file_id = message.reply_to_message.photo[-1].file_id
    db = load_db()
    
    target_map = {
        "st": ("start_pic", "Start picture"),
        "hp": ("help_pic", "Help picture"),
        "lb": ("leaderboard_pic", "Leaderboard picture"),
        "sm": ("pic_stockmarket", "Stock Market picture"),
        "store": ("pic_store", "Main Store picture"),
        "onst": ("pic_online_store", "Online Store picture"),
        "offst": ("pic_offline_store", "Offline Store picture")
    }
    
    key, label = target_map[mode]
    db.setdefault("settings", {})[key] = file_id
    save_db()
    await message.reply(f"✅ <b>{label}</b> has been updated successfully!", parse_mode=ParseMode.HTML)

@main_router.message(Command("backup"))
async def backup_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.reply("⚙️ Backing up database to group...")
    await perform_backup()
    await message.reply("✅ Backup completed and pinned.")

@main_router.message(Command("import"))
async def import_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply("⚠️ Reply to a database.json document to import.", parse_mode=ParseMode.HTML)
        return
    doc = message.reply_to_message.document
    if not doc.file_name.endswith(".json"):
        await message.reply("⚠️ File must be a JSON document.", parse_mode=ParseMode.HTML)
        return
    msg = await message.reply("📥 Downloading and importing database...", parse_mode=ParseMode.HTML)
    try:
        file_info = await bot.get_file(doc.file_id)
        await bot.download_file(file_info.file_path, destination=DB_FILE)

        # ── CRITICAL FIX: Invalidate the in-memory cache so load_db() re-reads
        # the freshly downloaded file on the very next call instead of serving
        # the stale pre-import snapshot that was already in RAM.
        import config as _cfg
        _cfg._db_cache = None
        _cfg._db_dirty = False

        await msg.edit_text("✅ Database successfully imported and loaded into memory!", parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"Import failed: {e}", parse_mode=ParseMode.HTML)

# ==========================================
# CARD MANAGEMENT CONTROLS
# ==========================================
@main_router.message(Command("add_card"))
async def add_card(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    if not command.args or not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply("⚠️ Reply to an image.\n<code>/add_card Name | Anime | Rarity</code>", parse_mode=ParseMode.HTML)
        return
    try:
        args = command.args.split("|")
        char_name, anime_name, rarity = args[0].strip(), args[1].strip(), args[2].strip()
    except Exception:
        await message.reply("⚠️ Format: <code>/add_card Character | Anime | Rarity</code>", parse_mode=ParseMode.HTML)
        return

    formatted_rar = format_rarity(rarity)
    if formatted_rar not in RARITIES:
        await message.reply(f"⚠️ Invalid rarity! Use one of:\n" + "\n".join(f"  <code>{r}</code>" for r in RARITIES), parse_mode=ParseMode.HTML)
        return

    file_id = message.reply_to_message.photo[-1].file_id
    card_id = f"DB-{str(uuid.uuid4())[:6].upper()}"
    db = load_db()

    added_by = message.from_user.id
    added_by_mention = get_mention(added_by, message.from_user.first_name)

    log_text = (
        "<b>「 📥 DATABASE LOG : NEW CARD 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<blockquote><i>A new collectable character card has been registered globally.</i></blockquote>\n\n"
        f"• 🆔 <b>Card ID:</b> <code>{card_id}</code>\n"
        f"• 👤 <b>Character:</b> <b>{char_name}</b>\n"
        f"• 📺 <b>Anime:</b> <i>{anime_name}</i>\n"
        f"• 🌟 <b>Rarity:</b> <b>{formatted_rar}</b>\n"
        f"• ✍️ <b>Added By:</b> {added_by_mention}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    msg_id = None
    try: 
        msg = await bot.send_photo(DB_GROUP_ID, photo=file_id, caption=log_text, parse_mode=ParseMode.HTML)
        msg_id = msg.message_id
    except Exception as e: 
        print(f"[LOG_GROUP] Send failed: {e}")

    db["global_cards"][card_id] = {
        "name": char_name, "anime": anime_name, "rarity": formatted_rar,
        "file_id": file_id, "msg_id": msg_id, "added_by": added_by
    }
    save_db()

    await message.reply(log_text + "\n\n✅ Saved!", parse_mode=ParseMode.HTML)

@main_router.message(Command("remove_card"))
async def remove_card(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    if not command.args:
        await message.reply("⚠️ Format: <code>/remove_card DB-XXXXXX</code>", parse_mode=ParseMode.HTML)
        return
        
    card_id = command.args.split()[0].strip().upper()
    db = load_db()
    
    if card_id not in db["global_cards"]:
        await message.reply(f"Card <code>{card_id}</code> not found.", parse_mode=ParseMode.HTML)
        return
        
    removed = db["global_cards"].pop(card_id)
    save_db()
    
    msg_id = removed.get("msg_id")
    if msg_id:
        try:
            await bot.delete_message(chat_id=DB_GROUP_ID, message_id=msg_id)
        except Exception:
            pass
            
    await message.reply(f"🗑️ Removed: <b>{removed['name']}</b> (<code>{card_id}</code>)\n✅ Removed from Global Database & GC.", parse_mode=ParseMode.HTML)

@main_router.message(Command("edit_card"))
async def edit_card(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    if not command.args:
        await message.reply(
            "⚠️ <b>Usage:</b>\n<code>/edit_card DB-XXXXXX | New Name | New Anime | New Rarity</code>\n\n"
            "<i>Note: If you want to change the picture, reply to a new image with this command. If you don't reply to an image, the old picture is kept. Leave text fields blank between pipes ' | ' to keep old text.</i>", 
            parse_mode=ParseMode.HTML
        )
        return
        
    args = command.args.split("|")
    card_id = args[0].strip().upper()
    
    db = load_db()
    if card_id not in db["global_cards"]:
        await message.reply(f"Card <code>{card_id}</code> not found.", parse_mode=ParseMode.HTML)
        return
        
    card_data = db["global_cards"][card_id]
    
    new_name = args[1].strip() if len(args) > 1 and args[1].strip() else card_data["name"]
    new_anime = args[2].strip() if len(args) > 2 and args[2].strip() else card_data["anime"]
    
    new_rarity = format_rarity(args[3].strip()) if len(args) > 3 and args[3].strip() else card_data["rarity"]
    if new_rarity not in RARITIES:
        await message.reply(f"⚠️ Invalid rarity! Leave empty or use:\n" + "\n".join(f"  <code>{r}</code>" for r in RARITIES), parse_mode=ParseMode.HTML)
        return
        
    new_file_id = card_data["file_id"]
    photo_changed = False
    if message.reply_to_message and message.reply_to_message.photo:
        new_file_id = message.reply_to_message.photo[-1].file_id
        photo_changed = True
        
    db["global_cards"][card_id]["name"] = new_name
    db["global_cards"][card_id]["anime"] = new_anime
    db["global_cards"][card_id]["rarity"] = new_rarity
    db["global_cards"][card_id]["file_id"] = new_file_id
    save_db()
    
    log_text = (
        "<b>「 DATABASE LOG : CARD EDITED ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Card ID  ┊ <code>{card_id}</code>\n"
        f"👤 Name     ┊ <b>{new_name}</b>\n"
        f"📺 Anime    ┊ <b>{new_anime}</b>\n"
        f"🌟 Rarity   ┊ <b>{new_rarity}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    
    msg_id = card_data.get("msg_id")
    if msg_id:
        try:
            if photo_changed:
                await bot.edit_message_media(
                    chat_id=DB_GROUP_ID,
                    message_id=msg_id,
                    media=InputMediaPhoto(media=new_file_id, caption=log_text, parse_mode=ParseMode.HTML)
                )
            else:
                await bot.edit_message_caption(
                    chat_id=DB_GROUP_ID,
                    message_id=msg_id,
                    caption=log_text,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            print(f"[EDIT_CARD] Failed to update group message: {e}")
            
    await message.reply(f"✅ Card <code>{card_id}</code> updated successfully!\n\n" + log_text, parse_mode=ParseMode.HTML)

# ==========================================
# /forcedrop IMPLEMENTATION
# ==========================================
@main_router.message(Command("forcedrop"))
async def force_drop_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    try: await message.delete()
    except Exception: pass

    db = load_db()
    if not db.get("global_cards"):
        await message.reply("⚠️ No cards in database. Use <code>/add_card</code> first.", parse_mode=ParseMode.HTML)
        return

    if message.chat.type == ChatType.PRIVATE:
        if not command.args:
            await message.reply(
                "<b>「 FORCE DROP ぁ 」</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "In DM, provide the group ID:\n"
                "<code>/forcedrop -100XXXXXXXXXX</code>\n"
                "━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML
            )
            return
        try: target_chat = int(command.args.split()[0])
        except ValueError:
            await message.reply("⚠️ Invalid chat ID.", parse_mode=ParseMode.HTML)
            return
        await trigger_drop(target_chat)
        await message.reply(f"✅ Drop triggered in <code>{target_chat}</code>", parse_mode=ParseMode.HTML)
    else:
        await trigger_drop(message.chat.id)

# ==========================================
# SYSTEM METRICS AND AUDITING
# ==========================================
def get_botstats_content() -> tuple[str, InlineKeyboardMarkup]:
    db = load_db()
    sec = int(time.time() - bot_start_time)
    h, r = divmod(sec, 3600); m, s = divmod(r, 60)

    start_of_today = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    users_today = sum(1 for u in db["users"].values() if u.get("joined", 0) >= start_of_today)

    rarity_count = {}
    for c in db["global_cards"].values():
        normalized_rar = format_rarity(c["rarity"])
        rarity_count[normalized_rar] = rarity_count.get(normalized_rar, 0) + 1

    rarity_lines = []
    for i, rar in enumerate(RARITIES):
        prefix = "  └" if i == len(RARITIES) - 1 else "  ├"
        rarity_lines.append(f"{prefix} <b>{rar}:</b> <code>{rarity_count.get(rar, 0)}</code>")
    rarity_text = "\n".join(rarity_lines)

    text = (
        "<b>「 📊 SYSTEM PERFORMANCE METRICS 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<blockquote><i>Real-time server diagnostic and database allocation metrics.</i></blockquote>\n\n"
        "<b>🤖 Core Diagnostic Telemetry:</b>\n"
        f"  ├ ⏱️ <b>Uptime:</b> <code>{h}h {m}m {s}s</code>\n"
        f"  ├ 📨 <b>Engine Traffic:</b> <code>{config.total_messages} processed</code>\n"
        f"  ├ 🔄 <b>Auto-Leave Guard:</b> <code>{'Active ✅' if config.autoleave_enabled else 'Inactive'}</code>\n"
        f"  └ 📈 <b>Daily User Gain:</b> <b>+{users_today}</b>\n\n"
        "<b>📂 Global Database Indices:</b>\n"
        f"  ├ 🎴 <b>Registered Cards:</b> <code>{len(db['global_cards'])}</code>\n"
        f"  ├ 👥 <b>Tracked Profiles:</b> <code>{len(db['users'])}</code>\n"
        f"  └ 🏘️ <b>Managed Guilds:</b> <code>{len(db['groups'])}</code>\n\n"
        "<b>🌟 Global Rarity Allocations:</b>\n"
        f"{rarity_text}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh Stats", callback_data="refresh_botstats")],
        [InlineKeyboardButton(text="✕ Close", callback_data="close_msg")]
    ])
    return text, kb

@main_router.message(Command("botstats"))
async def bot_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    text, kb = get_botstats_content()
    await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "refresh_botstats")
async def refresh_botstats_cb(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS: return
    text, kb = get_botstats_content()
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cq.answer("🔄 Statistics updated!")

@main_router.message(Command("check_db_dupes"))
async def check_db_dupes(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    db = load_db()
    seen, dupes = {}, []
    for cid, data in db.get("global_cards",{}).items():
        key = (data["name"].lower().strip(), data["anime"].lower().strip())
        if key in seen: dupes.append((cid, data["name"], data["anime"], seen[key]))
        else: seen[key] = cid
    if not dupes:
        await message.reply("✅ No duplicates found.", parse_mode=ParseMode.HTML)
        return
    text = "<b>「 DUPES ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for d in dupes[:15]: text += f"⚠️ <b>{d[1]}</b> ({d[2]})\n  ├ <code>{d[3]}</code>\n  └ <code>{d[0]}</code>\n"
    text += "...and more." if len(dupes) > 15 else "━━━━━━━━━━━━━━━━━━━━"
    await message.reply(text, parse_mode=ParseMode.HTML)

# ==========================================
# /info PANEL CONTROLS AND PAGE ACTIONS
# ==========================================
def build_info_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Users List", callback_data="infousers_0"),
            InlineKeyboardButton(text="🏘️ Groups List", callback_data="infogroups_0")
        ],
        [InlineKeyboardButton(text="✕ Close", callback_data="close_msg")]
    ])

@main_router.message(Command("info"))
async def info_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    db = load_db()
    
    if command.args:
        target = command.args.split()[0].strip()
        if target in db["users"]:
            u = db["users"][target]
            await message.reply(
                f"<b>「 👤 USER REGISTRY PROFILE 」</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<blockquote><i>Database record tracking profile indices.</i></blockquote>\n\n"
                f"• 🆔 <b>User ID:</b> <code>{target}</code>\n"
                f"• 👤 <b>Mention:</b> {get_mention(target, u.get('name','User'))}\n"
                f"• 🎴 <b>Unique Items:</b> <code>{len(u.get('cards',{}))}</code>\n"
                f"• 📦 <b>Accumulated Claims:</b> <code>{u.get('total_claimed',0)}</code>\n"
                f"• 👻 <b>Global Ban Filter:</b> <i>{'Flagged 🔴' if int(target) in config.ghost_banned else 'Clear 🟢'}</i>\n"
                f"• 🔇 <b>Shadow Mute Guard:</b> <i>{'Muted 🔴' if config.is_shadow_banned(int(target)) else 'Clear 🟢'}</i>\n"
                f"━━━━━━━━━━━━━━━━━━━━", 
                parse_mode=ParseMode.HTML
            )
            return
        if target in db["groups"]:
            g = db["groups"][target]
            await message.reply(
                f"<b>「 🏘️ REGISTERED GROUP DETAILS 」</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"• 🆔 <b>Group ID:</b> <code>{target}</code>\n"
                f"• 🏘️ <b>Title:</b> <b>{g.get('title','?')}</b>\n"
                f"• 🎴 <b>Spawned Drops:</b> <code>{g.get('drops',0)}</code>\n"
                f"• 🏆 <b>Executed Claims:</b> <code>{g.get('claims',0)}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━", 
                parse_mode=ParseMode.HTML
            )
            return
        await message.reply(f"Target ID <code>{target}</code> is not registered.", parse_mode=ParseMode.HTML)
        return
        
    text = (
        f"<b>「 📊 DATABASE INFO PANEL ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Total users:</b> <code>{len(db['users'])}</code>\n"
        f"🏘️ <b>Total groups:</b> <code>{len(db['groups'])}</code>\n\n"
        f"Select an index parameter below to explore stored database records."
    )
    await message.reply(text, reply_markup=build_info_panel_keyboard(), parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "infopanel")
async def info_panel_back_cb(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("Admin authentication required.", show_alert=True)
        return
    db = load_db()
    text = (
        f"<b>「 📊 DATABASE INFO PANEL ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Total users:</b> <code>{len(db['users'])}</code>\n"
        f"🏘️ <b>Total groups:</b> <code>{len(db['groups'])}</code>\n\n"
        f"Select an index parameter below to explore stored database records."
    )
    await cq.message.edit_text(text, reply_markup=build_info_panel_keyboard(), parse_mode=ParseMode.HTML)
    await cq.answer()

@main_router.callback_query(F.data.startswith("infousers_"))
async def info_users_page_cb(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("Admin authentication required.", show_alert=True)
        return
    page = int(cq.data.split("_")[1])
    db = load_db()
    users = sorted(db["users"].items(), key=lambda x: x[1].get("joined", 0), reverse=True)
    
    per_page = 10
    total = len(users)
    total_pages = max(1, (total - 1) // per_page + 1)
    
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
    start = page * per_page
    end = start + per_page
    sliced = users[start:end]
    
    text = f"<b>「 REGISTERED PLAYERS LIST (Page {page+1}/{total_pages}) 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for idx, (uid, udata) in enumerate(sliced, start=start+1):
        mention = get_mention(uid, udata.get("name", "User"))
        username_str = f" (@{udata['username']})" if udata.get("username") else ""
        text += f"{idx}. {mention}{username_str} ➜ <code>{uid}</code>\n"
    text += "━━━━━━━━━━━━━━━━━━━━"
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"infousers_{page-1}"))
    if end < total:
        nav_buttons.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"infousers_{page+1}"))
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        nav_buttons,
        [InlineKeyboardButton(text="🏠 Back to Panel", callback_data="infopanel")],
        [InlineKeyboardButton(text="✕ Close", callback_data="close_msg")]
    ])
    
    await cq.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await cq.answer()

@main_router.callback_query(F.data.startswith("infogroups_"))
async def info_groups_page_cb(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("Admin authentication required.", show_alert=True)
        return
    page = int(cq.data.split("_")[1])
    db = load_db()
    groups = sorted(db["groups"].items(), key=lambda x: x[1].get("joined", 0), reverse=True)
    
    per_page = 10
    total = len(groups)
    total_pages = max(1, (total - 1) // per_page + 1)
    
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
    start = page * per_page
    end = start + per_page
    sliced = groups[start:end]
    
    text = f"<b>「 REGISTERED GROUPS LIST (Page {page+1}/{total_pages}) 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for idx, (gid, gdata) in enumerate(sliced, start=start+1):
        text += f"{idx}. <b>{gdata.get('title', 'Unknown')}</b> ➜ <code>{gid}</code>\n"
    text += "━━━━━━━━━━━━━━━━━━━━"
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"infogroups_{page-1}"))
    if end < total:
        nav_buttons.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"infogroups_{page+1}"))
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        nav_buttons,
        [InlineKeyboardButton(text="🏠 Back to Panel", callback_data="infopanel")],
        [InlineKeyboardButton(text="✕ Close", callback_data="close_msg")]
    ])
    
    await cq.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await cq.answer()

@main_router.message(Command("autoleave"))
async def autoleave_toggle(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    if not command.args or command.args.lower() not in ["on", "off"]:
        await message.reply("⚠️ Usage: <code>/autoleave on</code> or <code>off</code>", parse_mode=ParseMode.HTML)
        return
    
    config.autoleave_enabled = (command.args.lower() == "on")
    db = load_db()
    db["settings"]["autoleave"] = config.autoleave_enabled
    save_db()
    await message.reply(f"🔄 Auto-leave: {'✅ ON' if config.autoleave_enabled else 'OFF'}\nMin: {config.AUTOLEAVE_MIN_MEMBERS} members", parse_mode=ParseMode.HTML)

# ==========================================
# RESTRICTION AND MODERATION CONTROLS
# ==========================================
@main_router.message(Command("gban"))
async def global_ban_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return

    raw_args = (command.args or "").strip()
    is_reply = bool(message.reply_to_message and message.reply_to_message.from_user)

    if is_reply:
        uid  = message.reply_to_message.from_user.id
        name = message.reply_to_message.from_user.first_name
        remainder = raw_args
    else:
        if not raw_args:
            await message.reply(
                "⚠️ <b>Usage:</b>\n"
                "<code>/gban &lt;user_id | @username&gt; [reason] [duration]</code>\n"
                "Or reply to a user's message with <code>/gban [reason] [duration]</code>\n\n"
                "<b>Duration examples:</b> <code>30d</code>, <code>7h</code>, <code>45m</code>, <code>2w</code>, <code>permanent</code>\n"
                "If no reason is given it defaults to <b>None</b>. If no duration is given it defaults to <b>Permanent</b>.",
                parse_mode=ParseMode.HTML
            )
            return
        parts = raw_args.split(maxsplit=1)
        identifier = parts[0]
        remainder  = parts[1] if len(parts) > 1 else ""
        uid, name = await resolve_target(identifier, message)
        if not uid:
            await message.reply(f"⚠️ Could not resolve target <code>{identifier}</code>.", parse_mode=ParseMode.HTML)
            return

    # The last whitespace-separated token, if it parses as a duration
    # (e.g. "30d", "7h", "permanent"), is treated as the duration and
    # stripped off — whatever's left is the reason. If it doesn't parse
    # as a duration, the whole remainder is just the reason and the ban
    # defaults to permanent.
    reason         = remainder
    duration_token = None
    if remainder:
        tokens = remainder.rsplit(maxsplit=1)
        parsed = parse_gban_duration_token(tokens[-1])
        if parsed is not None:
            duration_token = parsed
            reason = tokens[0] if len(tokens) > 1 else ""

    reason = reason.strip() or "None"
    is_permanent = duration_token is None or duration_token == "permanent"
    expires_at   = None if is_permanent else int(time.time()) + duration_token
    duration_display = "Permanent" if is_permanent else format_duration_seconds(duration_token)

    config.ghost_banned.add(uid)
    config.gban_meta[uid] = {
        "reason": reason,
        "expires_at": expires_at,
        "banned_by": message.from_user.id,
        "banned_at": int(time.time())
    }
    db = load_db()
    db["settings"]["ghost_banned"] = list(config.ghost_banned)
    db["settings"]["gban_meta"]    = {str(k): v for k, v in config.gban_meta.items()}
    save_db()

    admin_mention = get_mention(message.from_user.id, message.from_user.first_name)
    await send_log(
        f"<b>「 👻 GLOBAL BAN ISSUED 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🎯 <b>Target:</b> {get_mention(uid, name)} (<code>{uid}</code>)\n"
        f"• 📝 <b>Reason:</b> {reason}\n"
        f"• ⏳ <b>Duration:</b> {duration_display}\n"
        f"• 🛡️ <b>Admin:</b> {admin_mention}\n"
        f"• 🕐 <b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await message.reply(
        f"👻 {get_mention(uid, name)} has been globally banned.\n"
        f"📝 <b>Reason:</b> {reason}\n"
        f"⏳ <b>Duration:</b> {duration_display}",
        parse_mode=ParseMode.HTML
    )

@main_router.message(Command("gunban"))
async def global_unban_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    uid, name = await resolve_target(command.args, message)
    if not uid:
        await message.reply("⚠️ <b>Format:</b> Reply to user or use:\n• <code>/gunban &lt;user_id&gt;</code>\n• <code>/gunban &lt;@username&gt;</code>", parse_mode=ParseMode.HTML)
        return
        
    config.ghost_banned.discard(uid)
    db = load_db()
    db["settings"]["ghost_banned"] = list(config.ghost_banned)
    save_db()

    admin_mention = get_mention(message.from_user.id, message.from_user.first_name)
    await send_log(
        f"<b>「 ✅ GLOBAL BAN LIFTED 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🎯 <b>Target:</b> {get_mention(uid, name)} (<code>{uid}</code>)\n"
        f"• 🛡️ <b>Admin:</b> {admin_mention}\n"
        f"• 🕐 <b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await message.reply(f"✅ {get_mention(uid, name)} has been globally unbanned.", parse_mode=ParseMode.HTML)

@main_router.message(Command("gbans"))
async def global_bans_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    if not config.ghost_banned:
        await message.reply("📝 Global Ban list is empty.", parse_mode=ParseMode.HTML)
        return
        
    db = load_db()
    text = "<b>「 👻 GLOBAL BANNED USERS 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for idx, uid in enumerate(config.ghost_banned, start=1):
        name = db["users"].get(str(uid), {}).get("name", "User")
        text += f"{idx}. {get_mention(uid, name)} ➜ <code>{uid}</code>\n"
    text += "━━━━━━━━━━━━━━━━━━━━"
    await message.reply(text, parse_mode=ParseMode.HTML)

@main_router.message(Command("sban"))
async def shadow_ban_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    uid, name = await resolve_target(command.args, message)
    if not uid:
        await message.reply("⚠️ <b>Format:</b> Reply to user or use:\n• <code>/sban &lt;user_id&gt;</code>\n• <code>/sban &lt;@username&gt;</code>", parse_mode=ParseMode.HTML)
        return
        
    config.shadow_banned[uid] = time.time() + config.SHADOW_BAN_DUR
    db = load_db()
    db["settings"]["shadow_banned"] = config.shadow_banned
    save_db()

    admin_mention = get_mention(message.from_user.id, message.from_user.first_name)
    await send_log(
        f"<b>「 🔇 SHADOW BAN ISSUED 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🎯 <b>Target:</b> {get_mention(uid, name)} (<code>{uid}</code>)\n"
        f"• ⏳ <b>Duration:</b> 10 minutes\n"
        f"• 🛡️ <b>Admin:</b> {admin_mention}\n"
        f"• 🕐 <b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await message.reply(f"🔇 {get_mention(uid, name)} has been shadow banned for 10 minutes.", parse_mode=ParseMode.HTML)

@main_router.message(Command("sunban"))
async def shadow_unban_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    uid, name = await resolve_target(command.args, message)
    if not uid:
        await message.reply("⚠️ <b>Format:</b> Reply to user or use:\n• <code>/sunban &lt;user_id&gt;</code>\n• <code>/sunban &lt;@username&gt;</code>", parse_mode=ParseMode.HTML)
        return
        
    config.shadow_banned.pop(uid, None)
    db = load_db()
    db["settings"]["shadow_banned"] = config.shadow_banned
    save_db()

    admin_mention = get_mention(message.from_user.id, message.from_user.first_name)
    await send_log(
        f"<b>「 🔊 SHADOW BAN LIFTED 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🎯 <b>Target:</b> {get_mention(uid, name)} (<code>{uid}</code>)\n"
        f"• 🛡️ <b>Admin:</b> {admin_mention}\n"
        f"• 🕐 <b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await message.reply(f"🔊 {get_mention(uid, name)} shadow ban restriction removed.", parse_mode=ParseMode.HTML)

@main_router.message(Command("sbans"))
async def shadow_bans_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    now = time.time()
    active_shadows = {uid: exp for uid, exp in config.shadow_banned.items() if exp > now}
    
    if not active_shadows:
        await message.reply("📝 Shadow Ban list is currently empty.", parse_mode=ParseMode.HTML)
        return
        
    db = load_db()
    text = "<b>「 🔇 SHADOW BANNED USERS 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for idx, (uid, exp) in enumerate(active_shadows.items(), start=1):
        name = db["users"].get(str(uid), {}).get("name", "User")
        rem = int(exp - now)
        m, s = divmod(rem, 60)
        text += f"{idx}. {get_mention(uid, name)} ➜ <code>{uid}</code> (Rem: {m}m {s}s)\n"
    text += "━━━━━━━━━━━━━━━━━━━━"
    await message.reply(text, parse_mode=ParseMode.HTML)

def build_admin_help_text() -> str:
    return (
        "<b>「 🛡️ ADMIN SYSTEM HELP 」\n"
        "━━〔 ⟡ 管理指令 ⟡ 〕━━</b>\n\n"
        "<b>➷ /a_help\n〻 View this admin guide\n\n"
        "➷ /up [mode] (reply img)\n〻 Update interface images (st/hp/lb/sm/store/onst/offst)\n\n"
        "➷ /add_card Character|Anime|Rarity\n〻 Upload a new card image (reply to photo)\n\n"
        "➷ /edit_card DB-ID | Name | Anime | Rarity\n〻 Edit an existing card's details/image\n\n"
        "➷ /remove_card [ID]\n〻 Remove card from global db & GC\n\n"
        "➷ /forcedrop\n〻 Manually trigger a drop in chat\n\n"
        "➷ /backup\n〻 Force instant backup upload\n\n"
        "➷ /import (reply to file)\n〻 Import data from JSON DB\n\n"
        "➷ /eval [code]\n〻 Run raw Python executions and manipulate DB variables\n\n"
        "➷ /ping\n〻 Measure current engine latency [Admin Only]\n\n"
        "➷ /refresh\n〻 Perform a hard reload of process and script modules\n\n"
        "➷ /cleangroups\n〻 Clean database records for inactive/kicked chats\n\n"
        "➷ /info\n〻 Interactive DB player & group list\n\n"
        "➷ /check [ID / Name]\n〻 Interactively inspect user or global card profiles [Admin Only]\n\n"
        "➷ /cards\n〻 Browse global database [Admin Only]\n\n"
        "➷ /add_promo\n〻 Generate promo codes [Admin Only]\n\n"
        "➷ /list_promos\n〻 View all active promotional codes [Admin Only]\n\n"
        "➷ /del_promo [Code]\n〻 Delete an active promotional code [Admin Only]\n\n"
        "➷ /gban /gunban &lt;reply/id/@user&gt;\n〻 Restrict user globally\n\n"
        "➷ /gbans\n〻 List globally restricted users\n\n"
        "➷ /sban /sunban &lt;reply/id/@user&gt;\n〻 Mute user temporarily\n\n"
        "➷ /sbans\n〻 List muted users\n\n"
        "➷ /autoleave on|off\n〻 Toggle automated small group departure\n\n"
        "➷ /lock_drop &lt;anime name&gt;\n〻 Prevent a specific anime series from appearing in drops\n\n"
        "➷ /unlock_drop &lt;anime name&gt;\n〻 Re-enable drops for a previously locked anime series\n\n"
        "➷ /bnxcast [mode] (reply to msg)\n〻 Broadcast/forward a message to users/groups/all</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

# ==========================================
# BROADCAST SYSTEM (/bnxcast) [ADMIN ONLY]
# ==========================================
BNXCAST_USAGE = (
    "⚠️ <b>Usᴀɢᴇ:</b> Rᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ:\n"
    "<code>/bnxcast [mode]</code>\n\n"
    "<b>Mᴏᴅᴇs:</b>\n"
    "┣ <code>users</code> - Cᴏᴘʏ ᴛᴏ ᴀʟʟ ᴜsᴇʀs\n"
    "┣ <code>gcs</code> - Cᴏᴘʏ ᴛᴏ ᴀʟʟ ɢʀᴏᴜᴘs\n"
    "┣ <code>all</code> - Cᴏᴘʏ ᴛᴏ ᴇᴠᴇʀʏᴏɴᴇ\n"
    "┣ <code>fusers</code> - Fᴏʀᴡᴀʀᴅ ᴛᴏ ᴜsᴇʀs\n"
    "┣ <code>fgcs</code> - Fᴏʀᴡᴀʀᴅ ᴛᴏ ɢʀᴏᴜᴘs\n"
    "┗ <code>fall</code> - Fᴏʀᴡᴀʀᴅ ᴛᴏ ᴇᴠᴇʀʏᴏɴᴇ"
)
BNXCAST_MODES = {"users", "gcs", "all", "fusers", "fgcs", "fall"}

@main_router.message(Command("bnxcast"))
async def broadcast_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return

    if not message.reply_to_message or not command.args:
        await message.reply(BNXCAST_USAGE, parse_mode=ParseMode.HTML)
        return

    mode = command.args.split()[0].strip().lower()
    if mode not in BNXCAST_MODES:
        await message.reply(BNXCAST_USAGE, parse_mode=ParseMode.HTML)
        return

    forward  = mode.startswith("f")
    base_mode = mode[1:] if forward else mode  # users / gcs / all

    db = load_db()
    target_ids = []
    if base_mode in ("users", "all"):
        target_ids += list(db["users"].keys())
    if base_mode in ("gcs", "all"):
        target_ids += list(db["groups"].keys())

    if not target_ids:
        await message.reply("No registered targets found for this mode.", parse_mode=ParseMode.HTML)
        return

    src_chat_id = message.chat.id
    src_msg_id  = message.reply_to_message.message_id

    status_msg = await message.reply(
        f"📡 <b>Broadcast started...</b>\n"
        f"Target: <code>{len(target_ids)}</code> chats ({'Forward' if forward else 'Copy'})",
        parse_mode=ParseMode.HTML
    )

    sent, failed = 0, 0
    for raw_id in target_ids:
        try:
            chat_id_int = int(raw_id)
        except (TypeError, ValueError):
            failed += 1
            continue

        for attempt in range(2):
            try:
                if forward:
                    await bot.forward_message(chat_id=chat_id_int, from_chat_id=src_chat_id, message_id=src_msg_id)
                else:
                    await bot.copy_message(chat_id=chat_id_int, from_chat_id=src_chat_id, message_id=src_msg_id)
                sent += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                continue
            except (TelegramForbiddenError, TelegramBadRequest):
                failed += 1
                break
            except Exception:
                failed += 1
                break

        await asyncio.sleep(0.05)

    admin_mention = get_mention(message.from_user.id, message.from_user.first_name)
    await status_msg.edit_text(
        f"<b>「 📡 BROADCAST COMPLETE 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🧭 <b>Mode:</b> <code>{mode}</code>\n"
        f"• ✅ <b>Delivered:</b> <code>{sent}</code>\n"
        f"• <b>Failed:</b> <code>{failed}</code>\n"
        f"• 📦 <b>Total Targets:</b> <code>{len(target_ids)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML
    )

    await send_log(
        f"<b>「 📡 BROADCAST EXECUTED 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🛡️ <b>Admin:</b> {admin_mention}\n"
        f"• 🧭 <b>Mode:</b> <code>{mode}</code>\n"
        f"• ✅ <b>Delivered:</b> <code>{sent}</code> / <b>Failed:</b> <code>{failed}</code>\n"
        f"• 🕐 <b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

@main_router.message(Command("a_help"))
async def admin_help_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    text = build_admin_help_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✕ Close", callback_data="close_msg")]])
    await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ==========================================
# PROMOTIONAL CODES MANAGER (/add_promo) [ADMIN ONLY]
# ==========================================
@main_router.message(Command("add_promo"))
async def add_promo_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if uid_int not in ADMIN_IDS: return

    if not command.args:
        await message.reply(
            "⚠️ <b>Usage:</b>\n"
            "<code>/add_promo CODE max_claims reward1 [reward2] ...</code>\n\n"
            "<b>Reward String Formats:</b>\n"
            "• 💠 <b>Shards:</b> <code>shards:amount</code> (Example: <code>shards:500</code>)\n"
            "• 🎴 <b>Cards:</b> <code>card:Rarity:quantity</code> (Example: <code>card:Divine:1</code>)\n\n"
            "<b>Multi-Gift Examples:</b>\n"
            "• <code>/add_promo WELCOME 100 shards:1000 card:Elite:1</code>\n"
            "• <code>/add_promo MEGA 50 shards:5000 card:Divine:2 card:Elite:1</code>",
            parse_mode=ParseMode.HTML
        )
        return

    parts = command.args.split()
    if len(parts) < 3:
        await message.reply("⚠️ Invalid parameters. You must supply a Code, Claim Limit, and at least 1 Reward block.", parse_mode=ParseMode.HTML)
        return

    code = parts[0].upper().strip()
    try:
        max_claims = int(parts[1])
    except ValueError:
        await message.reply("⚠️ Max claims parameter must be an integer.", parse_mode=ParseMode.HTML)
        return

    rewards = []
    for r_str in parts[2:]:
        r_parts = r_str.split(":")
        if not r_parts: continue
        r_type = r_parts[0].lower().strip()
        
        if r_type == "shards":
            if len(r_parts) < 2:
                await message.reply(f"⚠️ Invalid format in shards block: <code>{r_str}</code>", parse_mode=ParseMode.HTML)
                return
            try:
                amt = int(r_parts[1])
                rewards.append({"type": "shards", "shards": amt})
            except ValueError:
                await message.reply(f"⚠️ Shard quantity in <code>{r_str}</code> must be an integer.", parse_mode=ParseMode.HTML)
                return
                
        elif r_type == "card":
            if len(r_parts) < 2:
                await message.reply(f"⚠️ Invalid format in card block: <code>{r_str}</code>", parse_mode=ParseMode.HTML)
                return
            rarity_raw = r_parts[1].strip()
            rarity_normalized = format_rarity(rarity_raw)
            if rarity_normalized not in RARITIES:
                await message.reply(f"⚠️ Invalid rarity in card block: <code>{r_str}</code>", parse_mode=ParseMode.HTML)
                return
            
            quantity = 1
            if len(r_parts) >= 3:
                try:
                    quantity = int(r_parts[2])
                except ValueError:
                    pass
            rewards.append({"type": "card", "rarity": rarity_normalized, "amount": quantity})
            
        else:
            await message.reply(f"⚠️ Unknown reward block type: <code>{r_str}</code>. Use <code>shards</code> or <code>card</code>.", parse_mode=ParseMode.HTML)
            return

    db = load_db()
    promos = db.setdefault("promos", {})
    
    promos[code] = {
        "rewards": rewards,
        "max_claims": max_claims,
        "claimed_by": []
    }
    save_db()

    reward_descriptions = []
    for r in rewards:
        if r["type"] == "shards":
            reward_descriptions.append(f"• 💠 <b>Nexus Shards:</b> +{r['shards']}")
        else:
            reward_descriptions.append(f"• 🎴 <b>[{r['rarity']}] Cards:</b> x{r['amount']}")

    desc_text = "\n".join(reward_descriptions)
    await message.reply(
        f"✅ <b>Promo Code Added</b>\n"
        f"🎫 Code: <code>{code}</code>\n"
        f"👥 Max Claims Allowed: <b>{max_claims}</b>\n\n"
        f"📦 <b>Bundled Rewards:</b>\n{desc_text}",
        parse_mode=ParseMode.HTML
    )

# ==========================================
# PROMOTIONAL CODES AUDITOR (/list_promos) [ADMIN ONLY]
# ==========================================
@main_router.message(Command("list_promos"))
async def list_promos_cmd(message: Message):
    uid_int = message.from_user.id
    if uid_int not in ADMIN_IDS: return

    db = load_db()
    promos = db.setdefault("promos", {})

    if not promos:
        await message.reply("📝 No promotional codes are currently active.", parse_mode=ParseMode.HTML)
        return

    text = "<b>「 🎫 ACTIVE PROMO CODES 」</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for idx, (code, data) in enumerate(promos.items(), start=1):
        claimed = len(data.get("claimed_by", []))
        limit = data.get("max_claims", 0)
        
        rewards_summary = []
        if "rewards" in data:
            for r in data["rewards"]:
                if r["type"] == "shards":
                    rewards_summary.append(f"💠 {r['shards']} Shards")
                else:
                    rewards_summary.append(f"🎴 x{r['amount']} {r['rarity']}")
        else:
            if data.get("type") == "shards":
                rewards_summary.append(f"💠 {data.get('shards', 0)} Shards")
            else:
                rewards_summary.append(f"🎴 x{data.get('amount', 1)} {data.get('rarity')}")
                
        payouts_desc = ", ".join(rewards_summary)
        text += f"{idx}. 🎫 <code>{code}</code> ➜ [{payouts_desc}]\n   └ Claims Status: <b>{claimed}/{limit}</b>\n\n"
        
    text += "━━━━━━━━━━━━━━━━━━━━"
    await message.reply(text, parse_mode=ParseMode.HTML)

# ==========================================
# PROMOTIONAL CODES DESTROYER (/del_promo) [ADMIN ONLY]
# ==========================================
@main_router.message(Command("del_promo"))
async def del_promo_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if uid_int not in ADMIN_IDS: return

    if not command.args:
        await message.reply("⚠️ Usage: <code>/del_promo CODE</code>\nExample: <code>/del_promo GOLD</code>", parse_mode=ParseMode.HTML)
        return

    code = command.args.upper().strip()
    db = load_db()
    promos = db.setdefault("promos", {})

    if code not in promos:
        await message.reply(f"Promo code <code>{code}</code> does not exist.", parse_mode=ParseMode.HTML)
        return

    del promos[code]
    save_db()
    await message.reply(f"🗑️ Promotional code <code>{code}</code> has been deleted and invalidated.", parse_mode=ParseMode.HTML)


# ==========================================
# GLOBAL DB BROWSER LOGIC & PAGES (/cards)
# ==========================================
async def show_anime_list(event, edit=False, page=0):
    db = load_db()
    cards = db.get("global_cards", {})
    anime_titles = sorted(list(set(c["anime"] for c in cards.values())))
    
    if not anime_titles:
        text = "<b>「 GLOBAL DATABASE EMPTY 」</b>\n━━━━━━━━━━━━━━━━━━━━\nNo cards are registered yet."
        if edit and isinstance(event, CallbackQuery):
            await event.message.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            target = event.message if isinstance(event, CallbackQuery) else event
            await target.reply(text, parse_mode=ParseMode.HTML)
        return

    per_page = 10
    total = len(anime_titles)
    total_pages = max(1, (total - 1) // per_page + 1)
    
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
    start = page * per_page
    end = min(start + per_page, total)
    sliced = anime_titles[start:end]

    # ── Detailed rarity breakdown across the ENTIRE card pool (not just
    # the current page) — total unique cards AND total copies in
    # circulation, per rarity tier, so admins can see the pool's shape
    # at a glance every time /cards is opened.
    rarity_unique_counts = {r: 0 for r in RARITIES}
    rarity_circulation_counts = {r: 0 for r in RARITIES}
    for cid, cdata in cards.items():
        r = format_rarity(cdata["rarity"])
        if r not in rarity_unique_counts:
            continue  # unknown/legacy rarity string, skip rather than crash
        rarity_unique_counts[r] += 1

    users = db.get("users", {})
    for u_data in users.values():
        for cid, holding in u_data.get("cards", {}).items():
            cdata = cards.get(cid)
            if not cdata:
                continue
            r = format_rarity(cdata["rarity"])
            if r not in rarity_circulation_counts:
                continue
            rarity_circulation_counts[r] += holding.get("amount", 0)

    breakdown_lines = "\n".join(
        f"  {r}  —  <code>{rarity_unique_counts[r]}</code> unique  ┊  <code>{rarity_circulation_counts[r]}</code> in circulation"
        for r in RARITIES
    )
    
    text = (
        f"<b>「 📺 GLOBAL CARD BROWSER 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📊 Rarity Breakdown:</b>\n"
        f"{breakdown_lines}\n"
        f"  🎴 <b>Total Unique Cards:</b> <code>{len(cards)}</code>\n"
        f"  📺 <b>Total Anime Series:</b> <code>{total}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Select an anime to view its registered card pool:\n\n"
    )
    
    locked_animes = {a.lower() for a in db.get("settings", {}).get("locked_animes", [])}

    buttons = []
    row = []
    for idx, anime in enumerate(sliced, start=start+1):
        is_locked = anime.lower() in locked_animes
        lock_tag  = " 🔒" if is_locked else ""
        text += f"<b>{idx})</b> {anime}{lock_tag}\n"
        # FIXED: previously did anime.replace("|","¦")[:35] which SILENTLY
        # TRUNCATES any anime title longer than 35 characters (e.g. "The
        # Angel Next Door Spoils Me Rotten" is 36 chars). The truncated
        # name then failed exact-match lookups further down the chain,
        # producing the "broken name" / card-not-found bug. Now we use a
        # short stable hash as the callback key instead of the name itself
        # — callback_data stays tiny regardless of title length, and the
        # full, untruncated name is recovered via anime_key_lookup().
        anime_key = anime_hash_key(anime)
        button_label = f"🔒 {idx}" if is_locked else f"{idx}"
        row.append(InlineKeyboardButton(text=button_label, callback_data=f"an|{anime_key}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    text += f"\n━━━━━━━━━━━━━━━━━━━━\nPage <b>{page+1}/{total_pages}</b>"
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"anlist_page_{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"anlist_page_{page+1}"))
    
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="✕ Close", callback_data="close_msg")])
    
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    if edit and isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    else:
        target = event.message if isinstance(event, CallbackQuery) else event
        await target.reply(text, reply_markup=markup, parse_mode=ParseMode.HTML)


@main_router.message(Command("cards"))
async def cards_browse_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await show_anime_list(message)


@main_router.callback_query(F.data.startswith("anlist_page_"))
async def anime_list_page_cb(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("⚠️ Admin restricted.", show_alert=True)
        return
    page = int(cq.data.split("_")[2])
    await cq.answer()
    await show_anime_list(cq, edit=True, page=page)


# ==========================================
# UNIVERSAL INSPECTOR (/check) [ADMIN ONLY]
# ==========================================
@main_router.message(Command("check"))
async def check_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    
    if not command.args:
        await message.reply(
            "⚠️ <b>Usage:</b>\n"
            "• <code>/check &lt;user_id / @username&gt;</code> - Inspect user profile\n"
            "• <code>/check &lt;card name&gt;</code> - Inspect global card details\n"
            "• Or reply to a user message with <code>/check</code>", 
            parse_mode=ParseMode.HTML
        )
        return

    query = command.args.strip()
    db = load_db()

    # 1. Inspect User Path
    uid, name = await resolve_target(query, message)
    if uid and str(uid) in db["users"]:
        u_data = db["users"][str(uid)]
        cards = u_data.get("cards", {})
        
        text = (
            f"<b>「 👤 USER REGISTRY PROFILE 」</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<blockquote><i>Database record tracking profile indices.</i></blockquote>\n\n"
            f"• 🆔 <b>User ID:</b> <code>{uid}</code>\n"
            f"• 👤 <b>Mention:</b> {get_mention(uid, name)}\n"
            f"• 🎴 <b>Unique Items:</b> <code>{len(cards)}</code>\n"
            f"• 📦 <b>Claims:</b> <code>{u_data.get('total_claimed', 0)}</code>\n"
            f"• 💠 <b>Shards:</b> <code>{u_data.get('nexus_shards', 0)}</code>\n"
            f"• 👻 <b>Global Ban:</b> <i>{'Flagged 🔴' if int(uid) in config.ghost_banned else 'Clear 🟢'}</i>\n"
            f"• 🔇 <b>Shadow Mute:</b> <i>{'Muted 🔴' if config.is_shadow_banned(int(uid)) else 'Clear 🟢'}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        
        if cards:
            text += "\n\n<b>🎴 Sample Inventory:</b>\n"
            sorted_cards = sorted(cards.items(), key=lambda x: format_rarity(x[1]["rarity"]))
            for cid, cdata in sorted_cards[:15]:
                rar = format_rarity(cdata["rarity"])
                text += f" • <b>{cdata['name']}</b> ({rar}) x{cdata['amount']}\n"
            if len(sorted_cards) > 15:
                text += f" <i>...and {len(sorted_cards) - 15} more unique cards.</i>"
                
        await message.reply(text, parse_mode=ParseMode.HTML)
        return

    # 2. Inspect Card Path (Fuzzy Matching)
    query_lower = query.lower()
    global_cards = db.get("global_cards", {})
    
    best_match = None
    best_ratio = 0.0

    for cid, cdata in global_cards.items():
        name_lower = cdata["name"].lower()
        if query_lower == name_lower:
            best_match = (cid, cdata)
            break
        if query_lower in name_lower:
            ratio = 0.8 + (len(query_lower) / len(name_lower)) * 0.1
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = (cid, cdata)
        else:
            ratio = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()
            if ratio > 0.6 and ratio > best_ratio:
                best_ratio = ratio
                best_match = (cid, cdata)

    if best_match:
        cid, cdata = best_match
        display_rarity = format_rarity(cdata["rarity"])
        
        owners_count = sum(1 for u in db["users"].values() if cid in u.get("cards", {}))
        total_copies = sum(u.get("cards", {}).get(cid, {}).get("amount", 0) for u in db["users"].values())

        added_by = cdata.get("added_by")
        added_by_line = f"• ✍️ <b>Added By:</b> {get_mention(added_by, db['users'].get(str(added_by), {}).get('name', 'Unknown'))}\n" if added_by else ""

        card_text = (
            f"<b>「 🎴 CARD REFERENCE LOOKUP 」</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"• 🆔 <b>Card ID:</b> <code>{cid}</code>\n"
            f"• 👤 <b>Name:</b> <b>{cdata['name']}</b>\n"
            f"• 📺 <b>Anime:</b> <i>{cdata.get('anime', 'Unknown')}</i>\n"
            f"• 🌟 <b>Rarity:</b> <b>{display_rarity}</b>\n"
            f"{added_by_line}\n"
            f"• 👥 <b>Unique Owners:</b> <code>{owners_count}</code> players\n"
            f"• 📦 <b>Circulation:</b> <code>{total_copies} copies</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        
        try:
            await message.reply_photo(photo=cdata["file_id"], caption=card_text, parse_mode=ParseMode.HTML)
        except Exception:
            await message.reply(card_text, parse_mode=ParseMode.HTML)
        return

    await message.reply(
        f"No registered users or card titles match the query: <b>{query}</b>.", 
        parse_mode=ParseMode.HTML
    )




@main_router.callback_query(F.data.startswith("an|"))
async def anime_rarity_picker(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    if uid_int not in ADMIN_IDS:
        await cq.answer("⚠️ Restricted to administrators.", show_alert=True)
        return 
        
    anime_key = cq.data.split("|")[1]
    db = load_db()
    anime_name = anime_key_lookup(db, anime_key)
    if not anime_name:
        await cq.answer("This anime no longer exists in the database (titles may have changed). Please reopen /cards.", show_alert=True)
        return

    locked_animes = {a.lower() for a in db.get("settings", {}).get("locked_animes", [])}
    is_locked = anime_name.lower() in locked_animes
    lock_line = "\n🔒 <i>Drops are currently locked for this series.</i>" if is_locked else ""

    text = f"<b>「 📺 {anime_name} 」</b>\n━━━━━━━━━━━━━━━━━━━━{lock_line}\nSelect a rarity filter to browse cards:"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❄️ Divine", callback_data=f"crd|{anime_key}|divine|0")],
        [InlineKeyboardButton(text="⚓ Elite", callback_data=f"crd|{anime_key}|elite|0")],
        [InlineKeyboardButton(text="🃏 Basic", callback_data=f"crd|{anime_key}|basic|0")],
        [InlineKeyboardButton(text="🌟 All Rarities", callback_data=f"crd|{anime_key}|all|0")],
        [InlineKeyboardButton(text="◀️ Back to Anime List", callback_data="anlist_page_0")],
        [InlineKeyboardButton(text="✕ Close", callback_data="close_msg")]
    ])
    
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cq.answer()

@main_router.callback_query(F.data.startswith("crd|"))
async def browse_filtered_cards(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    if uid_int not in ADMIN_IDS:
        await cq.answer("⚠️ Restricted.", show_alert=True)
        return

    # Parse the callback data
    parts = cq.data.split("|")
    anime_key = parts[1]
    rarity_filter = parts[2]
    page = int(parts[3])

    db = load_db()
    anime_name = anime_key_lookup(db, anime_key)
    if not anime_name:
        await cq.answer("This anime no longer exists in the database. Please reopen /cards.", show_alert=True)
        return

    all_cards = db.get("global_cards", {})
    
    # Apply filters
    filtered_cards = []
    for cid, c in all_cards.items():
        if c["anime"] == anime_name:
            if rarity_filter == "all":
                filtered_cards.append((cid, c))
            else:
                # Compare against the safe mapping from config
                r_safe = RARITY_SAFE.get(format_rarity(c["rarity"]))
                if r_safe == rarity_filter:
                    filtered_cards.append((cid, c))
                    
    if not filtered_cards:
        await cq.answer("No cards found for this rarity.", show_alert=True)
        return

    # Calculate pagination bounds
    per_page = BROWSE_PER_PAGE
    total = len(filtered_cards)
    total_pages = max(1, (total - 1) // per_page + 1)
    
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
    start = page * per_page
    end = start + per_page
    sliced = filtered_cards[start:end]

    # Build the display string
    disp_rarity = rarity_filter.title() if rarity_filter != "all" else "All Rarities"
    text = f"<b>「 📺 {anime_name} - {disp_rarity} 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    
    for idx, (cid, c) in enumerate(sliced, start=start+1):
        text += f"{idx}. <b>{c['name']}</b> ({format_rarity(c['rarity'])})\n"
        
    text += f"━━━━━━━━━━━━━━━━━━━━\nPage <b>{page+1}/{total_pages}</b>"
    
    # Build navigation matrix
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"crd|{anime_key}|{rarity_filter}|{page-1}"))
    if end < total:
        nav_row.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"crd|{anime_key}|{rarity_filter}|{page+1}"))
        
    kb_layout = []
    if nav_row:
        kb_layout.append(nav_row)
    kb_layout.append([InlineKeyboardButton(text="🔙 Back to Rarities", callback_data=f"an|{anime_key}")])
    kb_layout.append([InlineKeyboardButton(text="✕ Close", callback_data="close_msg")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_layout)
    
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cq.answer()

# ==========================================
# /lock_drop AND /unlock_drop CONTROLS
# ==========================================
@main_router.message(Command("lock_drop"))
async def lock_drop_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    if not command.args:
        await message.reply("⚠️ <b>Usage:</b> <code>/lock_drop <anime series name></code>", parse_mode=ParseMode.HTML)
        return

    anime_name = command.args.strip()
    db = load_db()

    # Verify the anime actually exists in DB card records
    anime_lower = anime_name.lower().strip()
    db_anime_name = None
    for c in db.get("global_cards", {}).values():
        if c["anime"].lower().strip() == anime_lower:
            db_anime_name = c["anime"]
            break

    if not db_anime_name:
        await message.reply("Anime not found in the database.", parse_mode=ParseMode.HTML)
        return

    if "settings" not in db:
        db["settings"] = {}
    if "locked_animes" not in db["settings"]:
        db["settings"]["locked_animes"] = []

    locked = db["settings"]["locked_animes"]
    if any(a.lower() == anime_lower for a in locked):
        await message.reply(f"⚠️ <b>{db_anime_name}</b> is already locked from dropping.", parse_mode=ParseMode.HTML)
        return

    locked.append(db_anime_name)
    save_db(db)
    await perform_backup()
    await message.reply(f"🔒 <b>{db_anime_name}</b> drops have been locked successfully!", parse_mode=ParseMode.HTML)


@main_router.message(Command("unlock_drop"))
async def unlock_drop_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    if not command.args:
        await message.reply("⚠️ <b>Usage:</b> <code>/unlock_drop <anime series name></code>", parse_mode=ParseMode.HTML)
        return

    anime_name = command.args.strip()
    db = load_db()

    if "settings" not in db:
        db["settings"] = {}
    if "locked_animes" not in db["settings"]:
        db["settings"]["locked_animes"] = []

    locked = db["settings"]["locked_animes"]
    anime_lower = anime_name.lower().strip()

    # Verify the anime name exists in the DB to give better feedback
    db_anime_name = None
    for c in db.get("global_cards", {}).values():
        if c["anime"].lower().strip() == anime_lower:
            db_anime_name = c["anime"]
            break

    if not db_anime_name:
        await message.reply("Anime not found in the database.", parse_mode=ParseMode.HTML)
        return

    found = False
    for item in list(locked):
        if item.lower() == anime_lower:
            locked.remove(item)
            found = True

    if not found:
        await message.reply(f"⚠️ <b>{db_anime_name}</b> is not currently locked.", parse_mode=ParseMode.HTML)
        return

    save_db(db)
    await perform_backup()
    await message.reply(f"🔓 <b>{db_anime_name}</b> drops have been unlocked successfully!", parse_mode=ParseMode.HTML)
