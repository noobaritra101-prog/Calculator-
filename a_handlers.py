import io
import os
import sys
import time
import uuid
import asyncio
import traceback
from datetime import datetime, timezone
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode, ChatType

import config
from config import (
    bot, main_router, ADMIN_IDS, SUPREME_OWNER_ID, DB_GROUP_ID,
    RARITIES, format_rarity, load_db, save_db, perform_backup,
    get_mention, resolve_target, bot_start_time, DB_FILE
)

from handlers import trigger_drop

# ==========================================
# POWERFUL EVALUATION COMMAND (/eval)
# ==========================================
@main_router.message(Command("eval"))
async def eval_cmd(message: Message, command: CommandObject):
    # Restricted strictly to the designated Supreme Owner ID
    if message.from_user.id != SUPREME_OWNER_ID: return
    if not command.args:
        await message.reply("<blockquote><b>⚠️ Provide code to evaluate.</b></blockquote>", parse_mode=ParseMode.HTML)
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

    # Wrap raw execution in an async function template
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
        f"<blockquote><code>{output}</code></blockquote>",
        parse_mode=ParseMode.HTML
    )

# ==========================================
# SYSTEM MAINTENANCE TOOLS
# ==========================================
@main_router.message(Command("ping"))
async def ping_cmd(message: Message):
    start_time = time.time()
    msg = await message.reply("<blockquote><b>⚡ Measuring latency...</b></blockquote>", parse_mode=ParseMode.HTML)
    latency = round((time.time() - start_time) * 1000)
    await msg.edit_text(
        f"<blockquote><b>🏓 Pong!</b>\nLatency is <b>{latency}ms</b>.</blockquote>", 
        parse_mode=ParseMode.HTML
    )

@main_router.message(Command("refresh"))
async def refresh_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.reply("<blockquote><b>🔄 Synchronizing cache & hot-restarting bot engines...</b></blockquote>", parse_mode=ParseMode.HTML)
    
    # Save cache safely before restarting
    save_db()
    await perform_backup()
    
    # Reload process using Python runtime arguments
    os.execv(sys.executable, [sys.executable] + sys.argv)

@main_router.message(Command("cleangroups"))
async def clean_groups_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    msg = await message.reply("<blockquote><b>🧹 Scanning database records for invalid group memberships...</b></blockquote>", parse_mode=ParseMode.HTML)
    
    db = load_db()
    inactive_groups = []
    
    for gid in list(db.get("groups", {}).keys()):
        try:
            # Check if bot is still a member of the group
            await bot.get_chat_member(chat_id=int(gid), user_id=message.bot.id)
        except Exception:
            inactive_groups.append(gid)
            
    for gid in inactive_groups:
        db["groups"].pop(gid, None)
        
    save_db()
    await msg.edit_text(
        f"<blockquote><b>✅ Cleanup Complete!</b>\n"
        f"Removed <b>{len(inactive_groups)} groups</b> where the bot is no longer present.</blockquote>", 
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
        await msg.edit_text("✅ Database successfully imported and loaded into memory!", parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"❌ Import failed: {e}", parse_mode=ParseMode.HTML)

# ==========================================
# CARD MANAGEMENT CONTROLS
# ==========================================
@main_router.message(Command("add_card"))
async def add_card(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    if not message.reply_to_message or not message.reply_to_message.photo:
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
    
    log_text = (
        "<b>「 DATABASE LOG : NEW CARD ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Card ID  ┊ <code>{card_id}</code>\n"
        f"👤 Name     ┊ <b>{char_name}</b>\n"
        f"📺 Anime    ┊ <b>{anime_name}</b>\n"
        f"🌟 Rarity   ┊ <b>{formatted_rar}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

    msg_id = None
    try: 
        msg = await bot.send_photo(DB_GROUP_ID, photo=file_id, caption=log_text, parse_mode=ParseMode.HTML)
        msg_id = msg.message_id
    except Exception as e: 
        print(f"[LOG_GROUP] Send failed: {e}")

    db["global_cards"][card_id] = {"name": char_name, "anime": anime_name, "rarity": formatted_rar, "file_id": file_id, "msg_id": msg_id}
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
        await message.reply(f"❌ Card <code>{card_id}</code> not found.", parse_mode=ParseMode.HTML)
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
        await message.reply(f"❌ Card <code>{card_id}</code> not found.", parse_mode=ParseMode.HTML)
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
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Card ID  ┊ <code>{card_id}</code>\n"
        f"👤 Name     ┊ <b>{new_name}</b>\n"
        f"📺 Anime    ┊ <b>{new_anime}</b>\n"
        f"🌟 Rarity   ┊ <b>{new_rarity}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
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
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "In DM, provide the group ID:\n"
                "<code>/forcedrop -100XXXXXXXXXX</code>\n"
                "━━━━━━━━━━━━━━━━━━━━━━",
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

    # Calculate users joined since UTC calendar midnight
    start_of_today = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    users_today = sum(1 for u in db["users"].values() if u.get("joined", 0) >= start_of_today)

    rarity_count = {}
    for c in db["global_cards"].values():
        normalized_rar = format_rarity(c["rarity"])
        rarity_count[normalized_rar] = rarity_count.get(normalized_rar, 0) + 1

    rarity_lines = []
    for i, r in enumerate(RARITIES):
        prefix = "  └" if i == len(RARITIES) - 1 else "  ├"
        rarity_lines.append(f"{prefix} <b>{r}:</b> <code>{rarity_count.get(r, 0)}</code>")
    rarity_text = "\n".join(rarity_lines)

    text = (
        f"<b>「 📊 BOT STATISTICS ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>🤖 Engine Metrics:</b>\n"
        f"<blockquote>"
        f"  ├ ⏱️ <b>Uptime:</b> <code>{h}h {m}m {s}s</code>\n"
        f"  ├ 📨 <b>Logs Tracked:</b> <code>{config.total_messages} msgs</code>\n"
        f"  ├ 🔄 <b>AutoLeave:</b> <code>{'Enabled ✅' if config.autoleave_enabled else 'Disabled ❌'}</code>\n"
        f"  └ 📈 <b>Users Joined Today:</b> <b>{users_today}</b>"
        f"</blockquote>\n"
        f"<b>📂 Registered DB Metrics:</b>\n"
        f"<blockquote>"
        f"  ├ 🎴 <b>Database Cards:</b> <code>{len(db['global_cards'])}</code>\n"
        f"  ├ 👥 <b>Database Users:</b> <code>{len(db['users'])}</code>\n"
        f"  └ 🏘️ <b>Database Groups:</b> <code>{len(db['groups'])}</code>\n"
        f"</blockquote>\n"
        f"<b>🌟 Cards Allocation:</b>\n"
        f"<blockquote>{rarity_text}</blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
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
    text = "<b>「 DUPES ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for d in dupes[:15]: text += f"⚠️ <b>{d[1]}</b> ({d[2]})\n  ├ <code>{d[3]}</code>\n  └ <code>{d[0]}</code>\n"
    text += "...and more." if len(dupes) > 15 else "━━━━━━━━━━━━━━━━━━━━━━"
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
                f"<b>「 USER DETAIL ぁ 」</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 <b>User ID:</b> <code>{target}</code>\n"
                f"👤 <b>Name:</b> {get_mention(target, u.get('name','User'))}\n"
                f"🎴 <b>Cards owned:</b> {len(u.get('cards',{}))}\n"
                f"📦 <b>Total claims:</b> {u.get('total_claimed',0)}\n"
                f"👻 <b>Global Banned:</b> {'Yes 🔴' if int(target) in config.ghost_banned else 'No 🟢'}\n"
                f"🔇 <b>Shadow Banned:</b> {'Yes 🔴' if config.is_shadow_banned(int(target)) else 'No 🟢'}", 
                parse_mode=ParseMode.HTML
            )
            return
        if target in db["groups"]:
            g = db["groups"][target]
            await message.reply(
                f"<b>「 GROUP DETAIL ぁ 」</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 <b>Group ID:</b> <code>{target}</code>\n"
                f"🏘️ <b>Title:</b> {g.get('title','?')}\n"
                f"🎴 <b>Total drops:</b> {g.get('drops',0)}\n"
                f"🏆 <b>Total claims:</b> {g.get('claims',0)}", 
                parse_mode=ParseMode.HTML
            )
            return
        await message.reply(f"❌ Target ID <code>{target}</code> is not registered.", parse_mode=ParseMode.HTML)
        return
        
    text = (
        f"<b>「 📊 DATABASE INFO PANEL ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Total users:</b> <code>{len(db['users'])}</code>\n"
        f"🏘️ <b>Total groups:</b> <code>{len(db['groups'])}</code>\n\n"
        f"Select an index parameter below to explore stored database records."
    )
    await message.reply(text, reply_markup=build_info_panel_keyboard(), parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "infopanel")
async def info_panel_back_cb(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("❌ Admin authentication required.", show_alert=True)
        return
    db = load_db()
    text = (
        f"<b>「 📊 DATABASE INFO PANEL ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Total users:</b> <code>{len(db['users'])}</code>\n"
        f"🏘️ <b>Total groups:</b> <code>{len(db['groups'])}</code>\n\n"
        f"Select an index parameter below to explore stored database records."
    )
    await cq.message.edit_text(text, reply_markup=build_info_panel_keyboard(), parse_mode=ParseMode.HTML)
    await cq.answer()

@main_router.callback_query(F.data.startswith("infousers_"))
async def info_users_page_cb(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("❌ Admin authentication required.", show_alert=True)
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
    
    text = f"<b>「 REGISTERED PLAYERS LIST (Page {page+1}/{total_pages}) 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for idx, (uid, udata) in enumerate(sliced, start=start+1):
        mention = get_mention(uid, udata.get("name", "User"))
        username_str = f" (@{udata['username']})" if udata.get("username") else ""
        text += f"{idx}. {mention}{username_str} ➜ <code>{uid}</code>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━"
    
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
        await cq.answer("❌ Admin authentication required.", show_alert=True)
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
    
    text = f"<b>「 REGISTERED GROUPS LIST (Page {page+1}/{total_pages}) 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for idx, (gid, gdata) in enumerate(sliced, start=start+1):
        text += f"{idx}. <b>{gdata.get('title', 'Unknown')}</b> ➜ <code>{gid}</code>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━"
    
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
    await message.reply(f"🔄 Auto-leave: {'✅ ON' if config.autoleave_enabled else '❌ OFF'}\nMin: {config.AUTOLEAVE_MIN_MEMBERS} members", parse_mode=ParseMode.HTML)

# ==========================================
# RESTRICTION AND MODERATION CONTROLS
# ==========================================
@main_router.message(Command("gban"))
async def global_ban_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    uid, name = await resolve_target(command.args, message)
    if not uid:
        await message.reply("⚠️ <b>Format:</b> Reply to user or use:\n• <code>/gban &lt;user_id&gt;</code>\n• <code>/gban &lt;@username&gt;</code>", parse_mode=ParseMode.HTML)
        return
    
    config.ghost_banned.add(uid)
    db = load_db()
    db["settings"]["ghost_banned"] = list(config.ghost_banned)
    save_db()
    
    await message.reply(f"👻 {get_mention(uid, name)} has been globally banned.", parse_mode=ParseMode.HTML)

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
    
    await message.reply(f"✅ {get_mention(uid, name)} has been globally unbanned.", parse_mode=ParseMode.HTML)

@main_router.message(Command("gbans"))
async def global_bans_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    if not config.ghost_banned:
        await message.reply("📝 Global Ban list is empty.", parse_mode=ParseMode.HTML)
        return
        
    db = load_db()
    text = "<b>「 👻 GLOBAL BANNED USERS 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for idx, uid in enumerate(config.ghost_banned, start=1):
        name = db["users"].get(str(uid), {}).get("name", "User")
        text += f"{idx}. {get_mention(uid, name)} ➜ <code>{uid}</code>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━"
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
    text = "<b>「 🔇 SHADOW BANNED USERS 」</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for idx, (uid, exp) in enumerate(active_shadows.items(), start=1):
        name = db["users"].get(str(uid), {}).get("name", "User")
        rem = int(exp - now)
        m, s = divmod(rem, 60)
        text += f"{idx}. {get_mention(uid, name)} ➜ <code>{uid}</code> (Rem: {m}m {s}s)\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━"
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
        "➷ /ping\n〻 Measure current engine latency\n\n"
        "➷ /refresh\n〻 Perform a hard reload of process and script modules\n\n"
        "➷ /cleangroups\n〻 Clean database records for inactive/kicked chats\n\n"
        "➷ /info\n〻 Interactive DB player & group list\n\n"
        "➷ /gban /gunban &lt;reply/id/@user&gt;\n〻 Restrict user globally\n\n"
        "➷ /gbans\n〻 List globally restricted users\n\n"
        "➷ /sban /sunban &lt;reply/id/@user&gt;\n〻 Mute user temporarily\n\n"
        "➷ /sbans\n〻 List muted users\n\n"
        "➷ /autoleave on|off\n〻 Toggle automated small group departure</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

@main_router.message(Command("a_help"))
async def admin_help_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    text = build_admin_help_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✕ Close", callback_data="close_msg")]])
    await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)