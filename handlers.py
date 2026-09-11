import math
import time
import random
import asyncio
import difflib
import unicodedata
from datetime import datetime, timezone, timedelta
from aiogram import F
from aiogram.types import (
    Message, CallbackQuery, InlineQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo,
    InlineQueryResultPhoto, InlineQueryResultCachedPhoto, InlineQueryResultArticle,
    InputTextMessageContent, BufferedInputFile, InputMediaPhoto, ReactionTypeEmoji
)
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode, ChatType, ChatMemberStatus

import config
from config import (
    bot, main_router, ADMIN_IDS, DECK_PER_PAGE, CARDS_PER_PAGE, BROWSE_PER_PAGE,
    group_counters, active_drops, bot_start_time, spoiler_cache, RARITIES,
    RARITY_ORDER, RARITY_SAFE, SAFE_RARITY, format_rarity, load_db, save_db,
    ensure_user, ensure_group, get_mention, is_ghost_banned, is_shadow_banned,
    format_wait_mmss, QUERY_GROUP_ID, get_query_daily_tracker
)
from vlog import log_action
from economy import check_and_reward_referral

# In-memory mining tracking dictionary to prevent spam farming
user_mine_cooldowns = {}


# ==========================================
# /qry SUPPORT-TICKET SYSTEM CONFIGURATION
# ==========================================
_query_cooldowns: dict[str, float] = {}
QUERY_COOLDOWN_SECS = 90     # seconds between submissions for regular users
QUERY_DAILY_LIMIT   = 5      # max queries a user can submit per day
QUERY_MAX_PENDING   = 3      # max unanswered queries a user can have open at once
QUERY_MIN_LEN       = 5      # minimum characters in a query
QUERY_MAX_LEN       = 800    # maximum characters in a query



# ==========================================
# CHAT-AWARE RESPONSE HELPERS
# In groups: reply (quoted) to the user's command.
# In DMs: plain answer, no quote banner.
# ==========================================
async def smart_reply(message: Message, *args, **kwargs):
    if message.chat.type == ChatType.PRIVATE:
        return await message.answer(*args, **kwargs)
    return await message.reply(*args, **kwargs)


async def smart_reply_photo(message: Message, *args, **kwargs):
    if message.chat.type == ChatType.PRIVATE:
        return await message.answer_photo(*args, **kwargs)
    return await message.reply_photo(*args, **kwargs)



# ==========================================
# /setspawn - MESSAGE THRESHOLD CONFIG
# ==========================================
@main_router.message(Command("setspawn"))
async def set_spawn_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if message.chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await message.reply("This command can only be used in groups.")
        return

    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR] and message.from_user.id not in ADMIN_IDS:
        await message.reply("Only group admins can use this command.")
        return

    db  = ensure_group(message.chat.id, message.chat.title)
    cid = str(message.chat.id)
    s_min = db["groups"][cid].get("spawn_min", 100)
    s_max = db["groups"][cid].get("spawn_max", 110)

    if command.args and "-" in command.args:
        parts = command.args.split("-")
        try:
            new_min = int(parts[0].strip())
            new_max = int(parts[1].strip())
            if new_min >= 100 and new_max <= 500 and new_min < new_max:
                s_min = new_min
                s_max = new_max
                db["groups"][cid]["spawn_min"] = s_min
                db["groups"][cid]["spawn_max"] = s_max
                save_db()
                config.group_counters[cid] = {"count": 0, "target": random.randint(s_min, s_max)}
            else:
                await message.reply("Invalid ranges! Minimum is 100, maximum is 500, and min must be less than max.")
                return
        except ValueError:
            pass

    text = (
        "<b>⚙️ Spawn Configuration</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 <b>Min messages</b> - <code>{s_min}</code>\n"
        f"📈 <b>Max messages</b> - <code>{s_max}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Rules: Min 100, Max 500. Min must be &lt; Max.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➖ Min -10", callback_data=f"spbtn_min_sub_{cid}"),
            InlineKeyboardButton(text="➕ Min +10", callback_data=f"spbtn_min_add_{cid}")
        ],
        [
            InlineKeyboardButton(text="➖ Max -10", callback_data=f"spbtn_max_sub_{cid}"),
            InlineKeyboardButton(text="➕ Max +10", callback_data=f"spbtn_max_add_{cid}")
        ],
        [InlineKeyboardButton(text="✅ Save & Close", callback_data=f"spbtn_save_none_{cid}")]
    ])
    await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data.startswith("spbtn_"))
async def spawn_config_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    parts       = cq.data.split("_")
    action_type = parts[1]
    op          = parts[2]
    cid         = "_".join(parts[3:])

    member = await bot.get_chat_member(int(cid), cq.from_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR] and cq.from_user.id not in ADMIN_IDS:
        await cq.answer("Only group admins can adjust this.", show_alert=True)
        return

    if action_type == "save":
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.answer("✅ Spawn settings saved!", show_alert=True)
        return

    db = load_db()
    if cid not in db["groups"]: return

    s_min = db["groups"][cid].get("spawn_min", 100)
    s_max = db["groups"][cid].get("spawn_max", 110)
    current_min = s_min
    current_max = s_max

    if action_type == "min":
        if op == "sub": s_min -= 10
        elif op == "add": s_min += 10
    elif action_type == "max":
        if op == "sub": s_max -= 10
        elif op == "add": s_max += 10

    if s_min < 100: s_min = 100
    if s_max > 500: s_max = 500
    if s_min >= s_max:
        if action_type == "min": s_min = s_max - 10
        if action_type == "max": s_max = s_min + 10
    if s_min < 100: s_min = 100

    if s_min == current_min and s_max == current_max:
        await cq.answer("Limit reached!", show_alert=False)
        return

    db["groups"][cid]["spawn_min"] = s_min
    db["groups"][cid]["spawn_max"] = s_max
    save_db()
    config.group_counters[cid] = {"count": 0, "target": random.randint(s_min, s_max)}

    text = (
        "<b>⚙️ Spawn Configuration</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 <b>Min messages</b> - <code>{s_min}</code>\n"
        f"📈 <b>Max messages</b> - <code>{s_max}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Rules: Min 100, Max 500. Min must be &lt; Max.</i>"
    )
    await cq.message.edit_text(text, reply_markup=cq.message.reply_markup, parse_mode=ParseMode.HTML)
    await cq.answer()


async def expire_drop(chat_id: str, msg_id: int):
    await asyncio.sleep(600)
    if chat_id in active_drops and active_drops[chat_id].get("message_id") == msg_id:
        del active_drops[chat_id]
        try:
            await bot.delete_message(chat_id=int(chat_id), message_id=msg_id)
        except Exception:
            pass


async def trigger_drop(chat_id: int):
    db = load_db()
    if not db["global_cards"]: return

    # Check for locked anime parameters from Settings DB
    locked_animes = db.get("settings", {}).get("locked_animes", [])
    locked_animes_lower = [a.lower().strip() for a in locked_animes]

    roll = random.randint(1, 100)
    if roll <= 78:   target_rarity = "Basic 🃏"    # 78%
    elif roll <= 98: target_rarity = "Elite ⚓"    # 20%
    else:            target_rarity = "Divine ❄️"  # 2%

    # Filter our drop pool to exclude cards belonging to locked anime series
    pool = {k: v for k, v in db["global_cards"].items() 
            if format_rarity(v["rarity"]) == target_rarity 
            and v["anime"].lower().strip() not in locked_animes_lower}
            
    # Fallback to any unlocked cards if the current rarity pool has been locked out entirely
    if not pool:
        pool = {k: v for k, v in db["global_cards"].items() 
                if v["anime"].lower().strip() not in locked_animes_lower}

    # Absolute fallback (ignores locks) to protect execution state if ALL registered cards in DB are locked
    if not pool:
        pool = db["global_cards"]

    card_id, card_data = random.choice(list(pool.items()))
    display_rarity     = format_rarity(card_data["rarity"])

    caption = (
        "<b>「 CARD DROP ぁ 」\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>A wild card has appeared!</i></b>\n\n"
        f"<b>⟡ Rarity ⁝〔 {display_rarity}〕</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>◈ Use</b> /seize [character name] <b>to claim it!</b>"
    )

    try:
        original_file_id = card_data["file_id"]
        if original_file_id in spoiler_cache:
            msg = await bot.send_photo(
                chat_id=chat_id, photo=spoiler_cache[original_file_id],
                caption=caption, parse_mode=ParseMode.HTML, has_spoiler=True
            )
        else:
            file_info  = await bot.get_file(original_file_id)
            file_bytes = await bot.download_file(file_info.file_path)
            photo_input = BufferedInputFile(file_bytes.getvalue(), filename="card.jpg")
            msg = await bot.send_photo(
                chat_id=chat_id, photo=photo_input,
                caption=caption, parse_mode=ParseMode.HTML, has_spoiler=True
            )
            spoiler_cache[original_file_id] = msg.photo[-1].file_id

        active_drops[str(chat_id)] = {"card_id": card_id, "time": time.time(), "message_id": msg.message_id}
        asyncio.create_task(expire_drop(str(chat_id), msg.message_id))

        cid = str(chat_id)
        if cid in db["groups"]:
            db["groups"][cid]["drops"] = db["groups"][cid].get("drops", 0) + 1
            save_db()

        # ── DB-Group log: card spawn ────────────────────────────────────────
        try:
            group_title = db["groups"].get(cid, {}).get("title", str(chat_id))
            await bot.send_message(
                chat_id=config.DATABASE_BACKUP_ID,
                text=(
                    f"<b>「 🎴 CARD SPAWNED 」</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"• 🆔 <b>Card ID:</b> <code>{card_id}</code>\n"
                    f"• 👤 <b>Card:</b> <b>{card_data['name']}</b>\n"
                    f"• 🌟 <b>Rarity:</b> {display_rarity}\n"
                    f"• 🏘️ <b>Group:</b> {group_title} (<code>{chat_id}</code>)\n"
                    f"• 🕐 <b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as log_err:
            print(f"[SPAWN LOG] Failed: {log_err}")
    except Exception as e:
        print(f"[DROP] Error: {e}")


def _drop_message_link(chat_id: int, message_id: int):
    """Builds a t.me/c/... deep link to a card-drop message, so a failed
    guess can offer a button back to it. Only works for supergroups
    (chat_id starting with -100) — regular basic groups don't support
    stable message links, so this returns None for those."""
    if not message_id:
        return None
    gid = str(chat_id)
    if gid.startswith("-100"):
        return f"https://t.me/c/{gid[4:]}/{message_id}"
    return None


def _seize_name_matches(query: str, target_name: str) -> bool:
    """Word-level /seize matching. A guess is correct if:
      - the whole query is a substring of (or close-fuzzy to) the full
        name — the original behavior, still handles guessing the full
        name or a multi-word span typed in the right order, or
      - EVERY word in the query individually matches some word in the
        name, in any order — so for "Gojo Satoru" all of /seize gojo,
        /seize satoru, and /seize gojo satoru (or satoru gojo) now work,
        while a guess containing an unrelated word still fails since
        that word won't match anything.
    Words under 3 characters must match a target word exactly (same
    short-fragment guard the original whole-string check had) so tiny
    fragments can't loosely fuzzy-match their way in."""
    query  = query.lower().strip()
    target = target_name.lower().strip()
    if not query:
        return False

    if len(query) >= 3 and query in target:
        return True
    if difflib.SequenceMatcher(None, query, target).ratio() > 0.70:
        return True

    target_words = target.split()
    query_words  = query.split()
    if not query_words:
        return False

    def word_ok(qw: str) -> bool:
        if len(qw) < 3:
            return qw in target_words
        for tw in target_words:
            if qw in tw or tw in qw:
                return True
            if difflib.SequenceMatcher(None, qw, tw).ratio() > 0.70:
                return True
        return False

    return all(word_ok(qw) for qw in query_words)


@main_router.message(Command("seize"))
async def seize_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    chat_id = message.chat.id
    cid_str = str(chat_id)

    if cid_str not in active_drops: return
    if not command.args:
        await message.reply("Provide the character name!\nFormat: <code>/seize</code> [name]", parse_mode=ParseMode.HTML)
        return

    drop_data   = active_drops[cid_str]
    card_id     = drop_data["card_id"]
    drop_time   = drop_data["time"]

    db          = load_db()
    global_card = db["global_cards"].get(card_id)
    if not global_card:
        # The card was deleted from the database after this drop spawned.
        # Previously this just silently returned, leaving the drop stuck
        # in active_drops forever — nobody could ever seize it (every
        # future /seize hit this same dead end) until the 10-minute
        # auto-expiry finally deleted the message. Clear it immediately
        # instead so a fresh, claimable drop can appear right away.
        del active_drops[cid_str]
        try:
            await bot.delete_message(chat_id=chat_id, message_id=drop_data.get("message_id"))
        except Exception:
            pass
        await message.reply(
            "This card was removed from the database and can no longer be claimed.",
            parse_mode=ParseMode.HTML
        )
        return

    target_name = global_card["name"].lower()
    query       = command.args.lower().strip()

    matched = _seize_name_matches(query, target_name)

    if not matched:
        link = _drop_message_link(chat_id, drop_data.get("message_id"))
        wrong_kb = None
        if link:
            wrong_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="View Again", url=link)]
            ])
        await message.reply(
            "🚫「 𝗪𝗥𝗢𝗡𝗚 𝗚𝗨𝗘𝗦𝗦 ぁ 」\n\n➜ 𝗧𝗿𝘆 𝗔𝗴𝗮𝗶𝗻",
            parse_mode=ParseMode.HTML,
            reply_markup=wrong_kb
        )
        return

    time_taken = round(time.time() - drop_time, 2)
    del active_drops[cid_str]

    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji="🎉")]
        )
    except Exception:
        pass

    user_id = str(uid_int)
    name    = message.from_user.first_name
    uname   = message.from_user.username
    db      = ensure_user(user_id, name, uname)

    rarity_normalized = format_rarity(global_card["rarity"])
    base_shards       = 10
    if rarity_normalized == "Elite ⚓":   base_shards = 25
    elif rarity_normalized == "Divine ❄️": base_shards = 100

    speed_bonus  = 15 if time_taken <= 3.0 else 0
    is_duplicate = card_id in db["users"][user_id]["cards"]
    dupe_bonus   = 10 if is_duplicate else 0
    total_earned = base_shards + speed_bonus + dupe_bonus

    db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + total_earned

    if card_id not in db["users"][user_id]["cards"]:
        db["users"][user_id]["cards"][card_id] = {"name": global_card["name"], "rarity": global_card["rarity"], "amount": 0}
    db["users"][user_id]["cards"][card_id]["amount"] += 1
    db["users"][user_id]["total_claimed"] = db["users"][user_id].get("total_claimed", 0) + 1

    if cid_str in db["groups"]:
        db["groups"][cid_str]["claims"] = db["groups"][cid_str].get("claims", 0) + 1

    await check_and_reward_referral(user_id, db)
    save_db()

    display_rarity     = format_rarity(global_card["rarity"])
    bonus_breakdown    = f" (+{speed_bonus} Speed⚡)" if speed_bonus else ""
    if dupe_bonus:
        bonus_breakdown += " (+10 Dupe♻️)"

    winner_text = (
        "<b>「 🎊 CARD SEIZED ぁ 」\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🎊 <i>{get_mention(user_id, name)} seized the card in {time_taken}s!</i>\n\n"
        "Character : </b>"
        f"{global_card['name']} <b>《{display_rarity}》</b>\n"
        f"<b>Anime :</b> {global_card['anime']}\n"
        f"<b>Economy :</b> Earned <b>{total_earned} Nexus Shards!</b>{bonus_breakdown}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Use /deck to <b>view your collection</b>"
    )
    seize_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View Collection 🫧", switch_inline_query_current_chat=f"card_user.{user_id}")]
    ])
    try:
        await message.reply(winner_text, parse_mode=ParseMode.HTML, reply_markup=seize_kb)
    except Exception:
        pass





# ==========================================
# GLOBAL CANCELLATION & CLOSE HANDLERS
# ==========================================
@main_router.callback_query(F.data.startswith("cancel_action_"))
async def cancel_action_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    owner_id = cq.data[len("cancel_action_"):]
    if str(uid_int) != owner_id:
        await cq.answer("This menu is not for you!", show_alert=True)
        return

    try:
        await cq.message.edit_caption(caption="Action cancelled.", reply_markup=None)
    except Exception:
        await cq.message.edit_text("Action cancelled.", reply_markup=None)
    await cq.answer()


@main_router.callback_query(F.data.startswith("close_msg"))
async def close_msg_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    parts = cq.data.split("|")
    if len(parts) > 1:
        owner_id = parts[1]
        if str(uid_int) != owner_id:
            await cq.answer("This menu is not for you!", show_alert=True)
            return

    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.answer()



# ==========================================
# /sortcards INTERFACE PRESETS
# ==========================================
@main_router.message(Command("sortcards"))
async def sort_cards(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id      = str(message.from_user.id)
    db           = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    current_sort = db["users"][user_id].get("sort_pref", "default").title()

    text = (
        f"<b>「 SORTING ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 Rarity  — Divine → Elite → Basic\n"
        f"🔤 Name    — A → Z\n"
        f"📦 Amount  — Most owned first\n"
        f"🔄 Default — Claim order\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Current sorting order </b>- {current_sort}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌟 Rarity", callback_data=f"setsort_{user_id}_rarity"),
            InlineKeyboardButton(text="🔤 Name",   callback_data=f"setsort_{user_id}_name")
        ],
        [
            InlineKeyboardButton(text="📦 Amount",  callback_data=f"setsort_{user_id}_amount"),
            InlineKeyboardButton(text="🔄 Default", callback_data=f"setsort_{user_id}_default")
        ]
    ])
    await message.reply(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data.startswith("setsort_"))
async def set_sort_cb(callback_query: CallbackQuery):
    uid_int = callback_query.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await callback_query.answer("🔇 You are currently restricted.", show_alert=True)
        return

    parts    = callback_query.data.split("_")
    owner_id = parts[1]
    mode     = parts[2]
    if str(callback_query.from_user.id) != owner_id: return

    db = load_db()
    db["users"][owner_id]["sort_pref"] = mode
    save_db()
    await callback_query.answer(f"✅ Sorting order saved: {mode.title()}")

    text = (
        f"<b>「 SORTING ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 Rarity  — Divine → Elite → Basic\n"
        f"🔤 Name    — A → Z\n"
        f"📦 Amount  — Most owned first\n"
        f"🔄 Default — Claim order\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Current sorting order </b>- {mode.title()}"
    )
    await callback_query.message.edit_text(text, reply_markup=callback_query.message.reply_markup, parse_mode=ParseMode.HTML)


# ==========================================
# /profile ENGINE PARSER DESIGN LAYOUTS
# ==========================================
@main_router.message(Command("profile"))
async def view_profile(message: Message):
    uid_int = message.from_user.id

    user_id  = str(message.from_user.id)
    name     = message.from_user.first_name
    username = message.from_user.username
    db       = ensure_user(user_id, name, username)
    user_data = db["users"][user_id]
    cards     = user_data.get("cards", {})

    # Total copies owned, broken down by rarity (dupes counted, not just unique cards)
    rarity_counts = {"Divine ❄️": 0, "Elite ⚓": 0, "Basic 🃏": 0}
    for cdata in cards.values():
        r = format_rarity(cdata.get("rarity", ""))
        if r in rarity_counts:
            rarity_counts[r] += cdata.get("amount", 0)
    total_cards = sum(rarity_counts.values())

    joined_year  = datetime.fromtimestamp(user_data.get("joined", int(time.time())), tz=timezone.utc).strftime("%Y")
    shards       = user_data.get("nexus_shards", 0)

    sorted_users = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)
    rank = 9999
    for i, (uid, udata) in enumerate(sorted_users):
        if uid == user_id:
            rank = i + 1
            break

    uname_display = f"@{username}" if username else "None"
    now = time.time()

    # Global (ghost) ban status — reuses is_ghost_banned() which also auto-clears expired bans
    is_gbanned_now = is_ghost_banned(uid_int)
    if is_gbanned_now:
        meta = config.gban_meta.get(uid_int, {})
        expires_at = meta.get("expires_at")
        if expires_at:
            remaining = expires_at - now
            gban_line = f"{is_gbanned_now} [Wait : {format_wait_mmss(remaining)} min]"
        else:
            gban_line = f"{is_gbanned_now} [Permanent]"
    else:
        gban_line = f"{is_gbanned_now}"

    is_shadow_banned_now = bool(int(user_id) in config.shadow_banned and config.shadow_banned[int(user_id)] > now)
    if is_shadow_banned_now:
        remaining = config.shadow_banned[int(user_id)] - now
        shadow_ban_line = f"{is_shadow_banned_now} [Wait : {format_wait_mmss(remaining)} min]"
    else:
        shadow_ban_line = f"{is_shadow_banned_now}"

    full_name  = message.from_user.full_name
    first_name = message.from_user.first_name
    safe_full_name  = str(full_name).replace("<", "&lt;").replace(">", "&gt;")
    safe_first_name = str(first_name).replace("<", "&lt;").replace(">", "&gt;")
    name_link = f'<a href="tg://user?id={user_id}">{safe_full_name}</a>'

    profile_text = (
        "<b>「 𝗡𝗘𝗫𝗨𝗦 : 𝗣𝗥𝗢𝗙𝗜𝗟𝗘 ぁ」</b>\n\n"
        f"<b>Name</b> - {name_link}\n"
        f"<b>ID</b> - {safe_first_name} [{user_id}]\n\n"
        f"<b>Total Shards</b> - {shards} 💠\n"
        f"<b>Total Cards</b> - {total_cards}\n"
        f"• <b>Total Divine</b> - {rarity_counts['Divine ❄️']}\n"
        f"• <b>Total Elite</b> - {rarity_counts['Elite ⚓']}\n"
        f"• <b>Total Basic</b> - {rarity_counts['Basic 🃏']}\n"
        f"<b>Global Rank</b> - #{rank}\n\n"
        f"<b>Global Ban</b> - {gban_line}\n"
        f"<b>Shadow Ban</b> - {shadow_ban_line}"
    )

    keyboard  = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Close", callback_data=f"close_msg|{user_id}")]])
    photo_sent = False
    try:
        photos = await bot.get_user_profile_photos(int(user_id), limit=1)
        if photos.total_count > 0:
            await smart_reply_photo(message, photo=photos.photos[0][0].file_id, caption=profile_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            photo_sent = True
    except Exception:
        pass

    if not photo_sent:
        try:
            await smart_reply(message, profile_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ==========================================
# /leaderboard WRAPPERS
# ==========================================
LEADERBOARD_SYMBOLS = ["✦", "✧", "❖"] + ["◈"] * 7


@main_router.message(Command("leaderboard", "top"))
async def leaderboard(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    db      = load_db()
    top     = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)
    user_id = str(uid_int)

    user_rank = 0
    for i, (uid, ud) in enumerate(top):
        if uid == user_id:
            user_rank = i + 1
            break
    rank_text = f"#{user_rank}" if user_rank > 0 else "Unranked"

    text = "<b>「 🌐 𝗧𝗢𝗣 𝗖𝗔𝗥𝗗 𝗖𝗢𝗟𝗟𝗘𝗖𝗧𝗢𝗥 ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    if not top:
        text += "<i>No collectors found yet.</i>\n"
    else:
        for i, (uid, ud) in enumerate(top[:10]):
            sym       = LEADERBOARD_SYMBOLS[i % 10]
            safe_name = str(ud.get("name", "Unknown")).replace("<", "&lt;").replace(">", "&gt;")
            text += f"{sym} <b>{safe_name}</b> ― 🎴 {len(ud.get('cards', {}))}\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❖ Your Rank - {rank_text}", callback_data="noop")],
        [InlineKeyboardButton(text="✕ Close", callback_data=f"close_msg|{uid_int}")]
    ])

    pic = db.get("settings", {}).get("leaderboard_pic")
    if pic: await smart_reply_photo(message, photo=pic, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:   await smart_reply(message, text, reply_markup=kb, parse_mode=ParseMode.HTML)


# ==========================================
# INLINE BROWSER EXECUTION
# ==========================================
@main_router.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    uid_int = inline_query.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    query_raw      = inline_query.query.strip()
    target_user_id = str(inline_query.from_user.id)
    query          = ""

    if query_raw.lower().startswith("card_user."):
        rest  = query_raw[len("card_user."):]
        parts = rest.split(maxsplit=1)
        if parts and parts[0].isdigit():
            target_user_id = parts[0]
            query = parts[1].lower() if len(parts) > 1 else ""
        else:
            # "card_user." prefix present but no valid numeric id followed —
            # fall back to treating everything after the prefix as a plain
            # search query against the requester's own collection.
            query = rest.lower()
    else:
        # BUG THIS FIXES: a bare query (no "card_user." prefix), e.g.
        # "@Animenx_bot Goku", was previously discarded entirely — `query`
        # stayed "" so it silently showed the requester's whole unfiltered
        # collection no matter what they typed. Now it's used as-is to
        # search the requester's own collection by card name or anime.
        query = query_raw.lower()

    db           = ensure_user(str(inline_query.from_user.id), inline_query.from_user.first_name, inline_query.from_user.username)
    cards        = db["users"].get(target_user_id, {}).get("cards", {})
    global_cards = db.get("global_cards", {})
    results      = []

    items     = list(cards.items())
    sort_pref = db["users"].get(target_user_id, {}).get("sort_pref", "default")
    if sort_pref == "rarity":   items.sort(key=lambda x: RARITY_ORDER.get(format_rarity(x[1]["rarity"]), 99))
    elif sort_pref == "amount": items.sort(key=lambda x: x[1]["amount"], reverse=True)
    else:                       items.sort(key=lambda x: x[1]["name"].lower())

    # BUG THIS FIXES: previously sliced to the first 50 cards BEFORE
    # applying the search filter, and never told Telegram there were more
    # results to page through. That meant (a) any card past the 50th in
    # sort order was invisible to search no matter the query, and (b) any
    # collection over 50 cards was permanently capped at 50 in inline mode
    # — the rest were unreachable no matter how far you scrolled.
    # Fix: filter first, then paginate the FILTERED list using Telegram's
    # inline offset mechanism so scrolling further actually fetches more.
    if query:
        filtered = [
            (cid, cdata) for cid, cdata in items
            if query in cdata["name"].lower()
            or query in cdata["rarity"].lower()
            or query in global_cards.get(cid, {}).get("anime", "").lower()
        ]
    else:
        filtered = items

    try:
        offset = int(inline_query.offset) if inline_query.offset else 0
    except ValueError:
        offset = 0

    PAGE_SIZE = 50
    page_slice = filtered[offset:offset + PAGE_SIZE]
    next_offset = str(offset + PAGE_SIZE) if offset + PAGE_SIZE < len(filtered) else ""

    for cid, cdata in page_slice:
        full    = global_cards.get(cid, {})
        file_id = full.get("file_id", "")
        if not file_id or len(file_id) < 10: continue

        disp_rarity  = format_rarity(cdata["rarity"])
        user_name    = db["users"].get(target_user_id, {}).get("name", "User")
        safe_name    = str(user_name).replace("<", "&lt;").replace(">", "&gt;")
        mention      = f'<a href="tg://user?id={target_user_id}">{safe_name}</a>'
        
        caption_text = (
            f"<i><b>Ooooh! Check out {mention}'s card!</b></i>\n\n"
            f"<b>⦿ <i>Character </i>» {cdata['name']} ⟪ {full.get('anime', '?')} ⟫ \n"
            f"⦾ <i>Rarity </i>» {disp_rarity}\n"
            f"⬤ <i>Owned</i>  » x{cdata['amount']}</b>"
        )

        if file_id.startswith("http://") or file_id.startswith("https://"):
            results.append(InlineQueryResultPhoto(id=cid, photo_url=file_id, thumbnail_url=file_id, caption=caption_text, parse_mode=ParseMode.HTML))
        else:
            results.append(InlineQueryResultCachedPhoto(id=cid, photo_file_id=file_id, caption=caption_text, parse_mode=ParseMode.HTML))

    if not results:
        next_offset = ""  # no results at all — don't offer further pagination
        results.append(InlineQueryResultArticle(
            id="empty", title="No cards found",
            description="Try a different search or claim cards first!",
            input_message_content=InputTextMessageContent(
                message_text="No cards match your search. Claim some in the group!",
                parse_mode=ParseMode.HTML
            )
        ))

    try:
        await inline_query.answer(results, cache_time=10, is_personal=True, next_offset=next_offset)
    except Exception as e:
        print(f"[INLINE] Error: {e}")


# ==========================================
# WELCOME CONTROLLERS (/start & /help)
# ==========================================
def build_help_text() -> str:
    return (
        "<b>「 𝘊𝘖𝘔𝘔𝘈𝘕𝘋𝘚 ぁ 」\n"
        "━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>➷ /profile\n〻 View your profile &amp; stats\n\n"
        "➷ /deck\n〻 View your card deck\n\n"
        "➷ /flex [Name]\n〻 Showcase your cards\n\n"
        "➷ /gift [Name] (reply to msg)\n〻 Gift a card to a user\n\n"
        "➷ /trade [Your Card] | [Their Card] (reply to msg)\n〻 Propose a card-for-card trade\n\n"
        "➷ /leaderboard\n〻 Global collector ranking\n\n"
        "➷ /special [Name]\n〻 Set featured card\n\n"
        "➷ /daily\n〻 Claim daily shard allowance\n\n"
        "➷ /weekly\n〻 Claim weekly shards &amp; a Basic or Elite card!\n\n"
        "➷ /roll\n〻 Play bowling for 10 tries!\n\n"
        "➷ /throw\n〻 Play basketball for 10 tries!\n\n"
        "➷ /burn [Name]\n〻 Burn a card for quick Shards!\n\n"
        "➷ /referral\n〻 View your referral status and link!\n\n"
        "➷ /redeem [Code]\n〻 Redeem active promotional codes!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "々 Cards randomly appear in chats\n"
        "々 Type <code>/seize</code> [name] before others to grab them!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


@main_router.message(Command("help"))
async def help_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    db  = load_db()
    pic = db.get("settings", {}).get("help_pic")
    kb  = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="メ Close", callback_data=f"close_msg|{uid_int}")]])
    if pic: await smart_reply_photo(message, photo=pic, caption=build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)
    else:   await smart_reply(message, build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "show_help")
async def show_help_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return
    await cq.answer()
    db  = load_db()
    pic = db.get("settings", {}).get("help_pic")
    kb  = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="メ Close", callback_data=f"close_msg|{uid_int}")]])
    await cq.message.delete()
    if pic: await cq.message.answer_photo(photo=pic, caption=build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)
    else:   await cq.message.answer(build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)


def build_start_text(user_id: int, first_name: str) -> str:
    safe_name = str(first_name).replace("<", "&lt;").replace(">", "&gt;")
    mention   = f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    return (
        f"<b>Hҽყ {mention} ✨\n\n"
        f"I Aɱ <a href='https://t.me/Animenx_bot'>「 ANIME NEXUS ぁ 」</a> 🍫</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"➜ 🍜 Cσʅʅҽƈƚ   ԃιϝϝҽɾɳƚ Aɳιɱҽ ƈαɾԃʂ 🎴\n"
        f"➜ 🥂 Bυιʅԃ   ყσυɾ υɳιϙυҽ Cαɾԃ Dҽƈƙ ✦\n"
        f"➜ ⛺ Cσɱρҽƚҽ ωιƚԋ ƈσʅʅҽƈƚσɾʂ ɠʅσႦαʅʅყ 🌍\n\n"
        f"╰➤ Tσ υʂҽ ɱҽ, <a href='https://t.me/Animenx_bot?startgroup=true'> αԃԃ   ɱҽ ƚσ   ყσυɾ ɠɾσυρ </a>."
    )


def build_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Aԃԃ Tσ Gɾσυρ", url="https://t.me/Animenx_bot?startgroup=true")],
        [InlineKeyboardButton(text="🌐 Mαιɳ Gɾσυρ", url=config.MAIN_GROUP_LINK),
         InlineKeyboardButton(text="📖 Hҽʅρ", callback_data="show_help")],
        [InlineKeyboardButton(text="WҽႦ", url="https://t.me/Animenx_bot/webdeck")]
    ])


@main_router.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    # ── Guide deep-link handler ──────────────────────────────────────────────
    # Reached via the "Click here 🪼" button /guide shows in groups.
    if command.args == "guide":
        await _send_guide_miniapp(message)
        return

    # ── Mine web app deep-link handler ───────────────────────────────────────
    # Reached via the "💬 Open in DM" button /webmine shows when run in a group.
    # Deferred import to avoid a circular import (mines.py imports from handlers.py).
    if command.args == "webmine":
        from mines import webmine_cmd
        await webmine_cmd(message)
        return

    # ── Referral deep-link handler ──────────────────────────────────────────
    if command.args and command.args.startswith("ref_"):
        referrer_id = command.args.split("_", 1)[1]
        buyer_id    = str(message.from_user.id)
        db          = load_db()

        if referrer_id != buyer_id and buyer_id not in db.get("users", {}):
            ensure_user(buyer_id,    message.from_user.first_name, message.from_user.username)
            ensure_user(referrer_id, "User")
            db = load_db()

            if not db["users"][buyer_id].get("referred_by"):
                db["users"][buyer_id]["referred_by"] = referrer_id
                save_db()

                buyer_mention = get_mention(buyer_id, message.from_user.first_name)
                try:
                    await bot.send_message(
                        chat_id=int(referrer_id),
                        text=(
                            "<b>「 👥 REFERRAL SYSTEM UPDATE 」</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 {buyer_mention} registered with your link!\n"
                            "💡 They'll activate your reward once they seize their first card."
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass

    # ── Offline store deep-link handler ─────────────────────────────────────
    if command.args and command.args.startswith("buy_"):
        lid      = command.args.split("_", 1)[1]
        buyer_id = str(message.from_user.id)
        db       = ensure_user(buyer_id, message.from_user.first_name, message.from_user.username)

        if lid not in db.get("offline_store", {}):
            await smart_reply(message, "This listing does not exist or has already been sold.", parse_mode=ParseMode.HTML)
            return

        listing     = db["offline_store"][lid]
        card_id     = listing["card_id"]
        global_card = db["global_cards"].get(card_id)

        if not global_card:
            await smart_reply(message, "The card for this listing no longer exists.", parse_mode=ParseMode.HTML)
            return

        if listing["seller_id"] == buyer_id:
            await smart_reply(message, "You cannot buy your own listing.", parse_mode=ParseMode.HTML)
            return

        price       = listing["price"]
        rarity_str  = format_rarity(global_card["rarity"])
        rarity_name, _, rarity_icon = rarity_str.rpartition(" ")

        caption = (
            f"<b>「 PURCHASE CONFIRMATION 」\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Name :</b> {global_card['name']}\n"
            f"<b>Rarity :</b> {rarity_name}<b>〔{rarity_icon}〕</b>\n"
            f"<b>Anime :</b> {global_card.get('anime', 'Unknown')}\n"
            f"<b>Price :</b> {price} Shards\n\n"
            f"Do you wish to proceed with this purchase?"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Confirm", callback_data=f"cboff_{buyer_id}_{lid}"),
                InlineKeyboardButton(text="Cancel", callback_data="cancel_action")
            ]
        ])
        await smart_reply_photo(message, photo=global_card["file_id"], caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    # ── Default start ────────────────────────────────────────────────────────
    db  = load_db()
    pic = db.get("settings", {}).get("start_pic")
    if pic:
        await smart_reply_photo(message, 
            photo=pic, caption=build_start_text(message.from_user.id, message.from_user.first_name),
            reply_markup=build_start_keyboard(), parse_mode=ParseMode.HTML
        )
    else:
        await smart_reply(message, 
            build_start_text(message.from_user.id, message.from_user.first_name),
            reply_markup=build_start_keyboard(), parse_mode=ParseMode.HTML
        )


@main_router.callback_query(F.data == "show_start")
async def show_start_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return
    await cq.answer()
    db  = load_db()
    pic = db.get("settings", {}).get("start_pic")
    await cq.message.delete()
    if pic: await cq.message.answer_photo(photo=pic, caption=build_start_text(cq.from_user.id, cq.from_user.first_name), reply_markup=build_start_keyboard(), parse_mode=ParseMode.HTML)
    else:   await cq.message.answer(build_start_text(cq.from_user.id, cq.from_user.first_name), reply_markup=build_start_keyboard(), parse_mode=ParseMode.HTML)




# ==========================================
# CARD LOOKUP + OWNERSHIP SEARCH (/search)
# ==========================================
WHOOWNS_COST = 200


def _find_owned_card(db: dict, user_id: str, query: str):
    """Fuzzy-matches a query against cards the user themself owns."""
    query      = query.lower().strip()
    user_cards = db["users"].get(user_id, {}).get("cards", {})
    best_match = None
    best_ratio = 0.0

    for cid, cdata in user_cards.items():
        if cdata.get("amount", 0) <= 0:
            continue
        name_lower = cdata["name"].lower()
        if query == name_lower:
            return cid
        if query in name_lower:
            ratio = 0.8 + (len(query) / len(name_lower)) * 0.1
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = cid
        else:
            ratio = difflib.SequenceMatcher(None, query, name_lower).ratio()
            if ratio > 0.6 and ratio > best_ratio:
                best_ratio = ratio
                best_match = cid

    return best_match


def _get_owners(db: dict, card_id: str):
    """Returns a list of (user_id, name, amount) for every user owning the card, sorted by amount desc."""
    owners = [
        (uid, udata.get("name", "Unknown"), udata["cards"][card_id].get("amount", 0))
        for uid, udata in db.get("users", {}).items()
        if card_id in udata.get("cards", {}) and udata["cards"][card_id].get("amount", 0) > 0
    ]
    owners.sort(key=lambda x: x[2], reverse=True)
    return owners


def _build_card_lookup_caption(global_card: dict) -> str:
    display_rarity = format_rarity(global_card["rarity"])
    return (
        "<b>「 Card Lookup 🔍 」\n"
        "<blockquote>╺╺╺╺╺╺╺╺╺╺╺╺╺╺╺</blockquote>\n"
        f"⦿ <i>Character </i>» {global_card['name']} ⟪ {global_card['anime']} ⟫\n"
        f"⦾ <i>Rarity</i> » {display_rarity}\n"
        "<blockquote>╺╺╺╺╺╺╺╺╺╺╺╺╺╺╺</blockquote></b>"
    )


@main_router.message(Command("search"))
async def search_card_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if not command.args:
        await message.reply("<b>Usage:</b> <code>/search &lt;card name&gt;</code>\nExample: <code>/search Makima</code>", parse_mode=ParseMode.HTML)
        return

    user_id = str(uid_int)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    if not db["users"].get(user_id, {}).get("cards"):
        await message.reply("You don't own any cards yet. Collect some first!", parse_mode=ParseMode.HTML)
        return

    card_id = _find_owned_card(db, user_id, command.args)
    if not card_id:
        await message.reply(f"You don't own any card matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    global_data = db["global_cards"][card_id]
    caption     = _build_card_lookup_caption(global_data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌐 𝗪𝗵𝗼 𝗼𝘄𝗻? ({WHOOWNS_COST} 💠)", callback_data=f"whoowns_{user_id}_{card_id}")]
    ])

    try:
        await message.reply_photo(
            photo=global_data.get("file_id"),
            caption=caption,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
            has_spoiler=True
        )
    except Exception:
        await message.reply(caption, reply_markup=kb, parse_mode=ParseMode.HTML)


DATA_CARD_AUTODELETE_SECS = 60


def _find_global_card(db: dict, query: str):
    """Fuzzy-matches a query against every card in the global pool, regardless of ownership."""
    query      = query.lower().strip()
    best_match = None
    best_ratio = 0.0

    for cid, cdata in db.get("global_cards", {}).items():
        name_lower = cdata["name"].lower()
        if query == name_lower:
            return cid
        if query in name_lower:
            ratio = 0.8 + (len(query) / len(name_lower)) * 0.1
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = cid
        else:
            ratio = difflib.SequenceMatcher(None, query, name_lower).ratio()
            if ratio > 0.6 and ratio > best_ratio:
                best_ratio = ratio
                best_match = cid

    return best_match


def _build_data_caption(global_card: dict) -> str:
    display_rarity = format_rarity(global_card["rarity"])
    return (
        "<b>╭─────〔 Card Data 〕─────╮</b>\n\n"
        f"<b>⦿ Character » </b>{global_card['name']} ⟪ {global_card['anime']} ⟫\n"
        f"<b>⦾ Rarity » </b>{display_rarity}"
    )


async def _autodelete_data_card(chat_id: int, msg_id: int):
    await asyncio.sleep(DATA_CARD_AUTODELETE_SECS)
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


@main_router.message(Command("data"))
async def data_card_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if message.chat.type != ChatType.PRIVATE:
        bot_info = await bot.get_me()
        dm_link = f"https://t.me/{bot_info.username}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Come here", url=dm_link)]
        ])
        await message.reply(
            "🔒 This command can only be used in the bot's DM.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
        return

    if not command.args:
        await message.reply("<b>Usage:</b> <code>/data &lt;card name&gt;</code>\nExample: <code>/data Makima</code>", parse_mode=ParseMode.HTML)
        return

    db = load_db()
    card_id = _find_global_card(db, command.args)
    if not card_id:
        await message.reply(f"No card found matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    global_data = db["global_cards"][card_id]
    caption = _build_data_caption(global_data)

    try:
        sent = await message.reply_photo(
            photo=global_data.get("file_id"),
            caption=caption,
            parse_mode=ParseMode.HTML,
            protect_content=True,
            has_spoiler=True
        )
    except Exception:
        sent = await message.reply(caption, parse_mode=ParseMode.HTML, protect_content=True)

    asyncio.create_task(_autodelete_data_card(sent.chat.id, sent.message_id))


@main_router.callback_query(F.data.startswith("whoowns_"))
async def who_owns_cb(cq: CallbackQuery):
    parts          = cq.data.split("_", 2)
    searcher_id    = parts[1]
    card_id        = parts[2]

    if str(cq.from_user.id) != searcher_id:
        await cq.answer("This isn't your search!", show_alert=True)
        return

    db          = load_db()
    global_card = db.get("global_cards", {}).get(card_id)
    if not global_card:
        await cq.answer("This card no longer exists.", show_alert=True)
        return

    user_data = db["users"].get(searcher_id, {})
    balance   = user_data.get("nexus_shards", 0)
    if balance < WHOOWNS_COST:
        await cq.answer(f"You need {WHOOWNS_COST} 💠 Shards to check owners. You have {balance} 💠.", show_alert=True)
        return

    owners = _get_owners(db, card_id)
    if not owners:
        await cq.answer("Nobody owns this card yet!", show_alert=True)
        return

    # Build the owner list as its own text message rather than stuffing it
    # into the photo caption — Telegram caps photo captions at 1024 chars,
    # and with enough owners that limit gets blown past, edit_caption throws,
    # and (since shards were deducted first) the user was charged for a
    # list that never rendered. Text messages allow up to 4096 chars, so
    # paginate defensively at a much higher owner count instead.
    OWNERS_PER_MSG = 80
    owner_lines_all = [f"{name} ({uid}) - {amount}" for uid, name, amount in owners]

    header = _build_card_lookup_caption(global_card)
    chunks = []
    for i in range(0, len(owner_lines_all), OWNERS_PER_MSG):
        chunk_lines = owner_lines_all[i:i + OWNERS_PER_MSG]
        chunks.append("\n".join(chunk_lines))

    try:
        # Remove the button but leave the original caption/text untouched —
        # this never risks a length error since we're not rewriting the caption.
        await cq.message.edit_reply_markup(reply_markup=None)

        first_text = f"{header}\n\n👥 <b>{len(owners)} owner(s):</b>\n{chunks[0]}"
        await cq.message.reply(first_text, parse_mode=ParseMode.HTML)
        for chunk in chunks[1:]:
            await cq.message.reply(chunk, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"[WHOOWNS] Failed to deliver owner list for {card_id}: {e}")
        await cq.answer("Something went wrong showing the owner list — you weren't charged.", show_alert=True)
        return

    # Only deduct shards after the list has actually been delivered.
    db["users"][searcher_id]["nexus_shards"] = balance - WHOOWNS_COST
    save_db()
    await cq.answer(f"{WHOOWNS_COST} 💠 Shards deducted.")


# ==========================================
# USER CARD BROWSER (/cardlists)
# Anime -> Rarity -> owned/not-owned card names. Same hashing trick as the
# admin /cards browser (anime names can be long/contain "|", so callback_data
# carries a short stable hash instead of the raw title).
# ==========================================
import hashlib as _cl_hashlib

def _cl_anime_hash_key(anime_name: str) -> str:
    return _cl_hashlib.md5(anime_name.encode("utf-8")).hexdigest()[:12]


def _cl_anime_key_lookup(db: dict, anime_key: str):
    cards = db.get("global_cards", {})
    anime_titles = set(c["anime"] for c in cards.values())
    for anime in anime_titles:
        if _cl_anime_hash_key(anime) == anime_key:
            return anime
    return None


CARDLISTS_PER_PAGE = 10  # laid out as 4 + 4 + 2 button rows


async def _show_cardlists_anime_page(event, edit=False, page=0, owner_id=None):
    if owner_id is None:
        owner_id = event.from_user.id

    db = load_db()
    cards = db.get("global_cards", {})
    hidden_animes = db.get("settings", {}).get("hidden_animes", [])
    hidden_lower = [a.lower().strip() for a in hidden_animes]
    anime_titles = sorted(
        a for a in set(c["anime"] for c in cards.values())
        if a.lower().strip() not in hidden_lower
    )

    if not anime_titles:
        text = "<b>「 Anime List 🪐 」</b>\n━━━━━━━━━━━━━━━━━━━━\nNo cards are registered yet."
        if edit and isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(text, parse_mode=ParseMode.HTML)
            except Exception:
                pass
        else:
            target = event.message if isinstance(event, CallbackQuery) else event
            await target.reply(text, parse_mode=ParseMode.HTML)
        return

    total = len(anime_titles)
    total_pages = max(1, (total - 1) // CARDLISTS_PER_PAGE + 1)
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0

    start = page * CARDLISTS_PER_PAGE
    end = min(start + CARDLISTS_PER_PAGE, total)
    sliced = anime_titles[start:end]

    lines = []
    for i, anime in enumerate(sliced):
        idx = start + i + 1
        connector = "╰─" if i == len(sliced) - 1 else "├─"
        lines.append(f"{connector} [{idx}] {anime}")

    text = (
        "<b>「 Anime List 🪐 」\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(lines) +
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>Page {page+1}/{total_pages}</blockquote></b>"
    )

    # Number buttons laid out 4 + 4 + 2
    number_buttons = [
        InlineKeyboardButton(text=str(start + i + 1), callback_data=f"cl_an|{owner_id}|{_cl_anime_hash_key(anime)}")
        for i, anime in enumerate(sliced)
    ]
    rows = []
    for chunk_size in (4, 4, 2):
        if not number_buttons: break
        rows.append(number_buttons[:chunk_size])
        number_buttons = number_buttons[chunk_size:]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="« Prev", callback_data=f"cl_page|{owner_id}|{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton(text="Next »", callback_data=f"cl_page|{owner_id}|{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="✕ Close", callback_data=f"close_msg|{owner_id}")])

    markup = InlineKeyboardMarkup(inline_keyboard=rows)

    if edit and isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    else:
        target = event.message if isinstance(event, CallbackQuery) else event
        await target.reply(text, reply_markup=markup, parse_mode=ParseMode.HTML)


def _cl_find_anime(db: dict, query: str):
    """Fuzzy-resolves a typed anime name to the exact title stored in
    global_cards. Exact match first, then substring, then similarity ratio —
    same approach _find_owned_card uses for card names. Anime hidden via
    /hide are excluded so they can't be reached by typing the name directly."""
    cards = db.get("global_cards", {})
    hidden_lower = [a.lower().strip() for a in db.get("settings", {}).get("hidden_animes", [])]
    anime_titles = sorted(
        a for a in set(c["anime"] for c in cards.values())
        if a.lower().strip() not in hidden_lower
    )
    query_lower = query.lower().strip()

    for anime in anime_titles:
        if anime.lower() == query_lower:
            return anime

    best_match, best_ratio = None, 0.0
    for anime in anime_titles:
        anime_lower = anime.lower()
        if query_lower in anime_lower:
            ratio = 0.8 + (len(query_lower) / len(anime_lower)) * 0.1
        else:
            ratio = difflib.SequenceMatcher(None, query_lower, anime_lower).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = anime

    return best_match if best_ratio > 0.6 else None


@main_router.message(Command("cardlists"))
async def cardlists_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if command.args:
        db = load_db()
        anime_name = _cl_find_anime(db, command.args)
        if not anime_name:
            await message.reply(f"No anime matching <b>{command.args}</b> was found.", parse_mode=ParseMode.HTML)
            return
        await _show_cardlists_rarity_picker(message, anime_name)
        return

    await _show_cardlists_anime_page(message)


@main_router.callback_query(F.data.startswith("cl_page|"))
async def cardlists_page_cb(cq: CallbackQuery):
    if is_ghost_banned(cq.from_user.id) or is_shadow_banned(cq.from_user.id):
        await cq.answer()
        return

    parts = cq.data.split("|")
    owner_id = parts[1]
    page = int(parts[2])

    if str(cq.from_user.id) != owner_id:
        await cq.answer("This menu is not for you!", show_alert=True)
        return

    await cq.answer()
    await _show_cardlists_anime_page(cq, edit=True, page=page, owner_id=owner_id)


async def _show_cardlists_rarity_picker(event, anime_name: str, edit=False, owner_id=None):
    """Renders the rarity-choice screen for a given anime. `event` is either
    a Message (fresh reply, e.g. from /cardlists <anime>) or a CallbackQuery
    (edit in place, e.g. from tapping an anime number button)."""
    if owner_id is None:
        owner_id = event.from_user.id

    anime_key = _cl_anime_hash_key(anime_name)

    db = load_db()
    anime_cards = {cid: c for cid, c in db.get("global_cards", {}).items() if c["anime"] == anime_name}
    owned_cards = db.get("users", {}).get(str(owner_id), {}).get("cards", {})

    def _owned_total(match_fn):
        total = owned = 0
        for cid, c in anime_cards.items():
            if match_fn(format_rarity(c["rarity"])):
                total += 1
                if cid in owned_cards and owned_cards[cid].get("amount", 0) > 0:
                    owned += 1
        return owned, total

    divine_owned, divine_total = _owned_total(lambda r: "Divine" in r)
    elite_owned, elite_total   = _owned_total(lambda r: "Elite" in r)
    basic_owned, basic_total   = _owned_total(lambda r: "Basic" in r)
    total_owned = divine_owned + elite_owned + basic_owned
    total_all   = divine_total + elite_total + basic_total

    text = (
        f"<b>Anime - 「 {anime_name} 」\n"
        f"Total Cards: ({total_owned}/{total_all})\n"
        f"[❄️] Total divine : ({divine_owned}/{divine_total})\n"
        f"[⚓] Total elite : ({elite_owned}/{elite_total})\n"
        f"[🎴] Total Basic: ({basic_owned}/{basic_total})</b>\n\n"
        "<blockquote>Choose a rarity:</blockquote>"
    )

    top_row = [r for r in RARITIES if r != "Basic 🃏"]
    bottom_row = [r for r in RARITIES if r == "Basic 🃏"]

    rarity_rows = []
    if top_row:
        rarity_rows.append([
            InlineKeyboardButton(text=r, callback_data=f"cl_r|{owner_id}|{anime_key}|{RARITY_SAFE[r]}|0")
            for r in top_row
        ])
    if bottom_row:
        rarity_rows.append([
            InlineKeyboardButton(text=r, callback_data=f"cl_r|{owner_id}|{anime_key}|{RARITY_SAFE[r]}|0")
            for r in bottom_row
        ])

    kb = InlineKeyboardMarkup(inline_keyboard=rarity_rows + [
        [InlineKeyboardButton(text="« Back to Anime List", callback_data=f"cl_page|{owner_id}|0")],
        [InlineKeyboardButton(text="✕ Close", callback_data=f"close_msg|{owner_id}")]
    ])

    if edit and isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    else:
        target = event.message if isinstance(event, CallbackQuery) else event
        await target.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data.startswith("cl_an|"))
async def cardlists_rarity_picker_cb(cq: CallbackQuery):
    if is_ghost_banned(cq.from_user.id) or is_shadow_banned(cq.from_user.id):
        await cq.answer()
        return

    parts = cq.data.split("|")
    owner_id = parts[1]
    anime_key = parts[2]

    if str(cq.from_user.id) != owner_id:
        await cq.answer("This menu is not for you!", show_alert=True)
        return

    db = load_db()
    anime_name = _cl_anime_key_lookup(db, anime_key)
    if not anime_name:
        await cq.answer("This anime no longer exists. Please reopen /cardlists.", show_alert=True)
        return

    hidden_lower = [a.lower().strip() for a in db.get("settings", {}).get("hidden_animes", [])]
    if anime_name.lower().strip() in hidden_lower:
        await cq.answer("This anime is no longer available. Please reopen /cardlists.", show_alert=True)
        return

    await _show_cardlists_rarity_picker(cq, anime_name, edit=True, owner_id=owner_id)
    await cq.answer()


@main_router.callback_query(F.data.startswith("cl_r|"))
async def cardlists_card_view_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await cq.answer()
        return

    parts = cq.data.split("|")
    owner_id     = parts[1]
    anime_key    = parts[2]
    rarity_safe  = parts[3]
    page         = int(parts[4])

    if str(uid_int) != owner_id:
        await cq.answer("This menu is not for you!", show_alert=True)
        return

    db = load_db()
    anime_name = _cl_anime_key_lookup(db, anime_key)
    if not anime_name:
        await cq.answer("This anime no longer exists. Please reopen /cardlists.", show_alert=True)
        return

    rarity_display = SAFE_RARITY.get(rarity_safe)
    if not rarity_display:
        await cq.answer("Unknown rarity.", show_alert=True)
        return

    all_cards = db.get("global_cards", {})
    filtered = [
        (cid, c) for cid, c in all_cards.items()
        if c["anime"] == anime_name and RARITY_SAFE.get(format_rarity(c["rarity"])) == rarity_safe
    ]

    if not filtered:
        await cq.answer(f"No {rarity_display} cards available for {anime_name}.", show_alert=True)
        return

    user_id = str(uid_int)
    owned_cards = db.get("users", {}).get(user_id, {}).get("cards", {})

    per_page = BROWSE_PER_PAGE
    total = len(filtered)
    total_pages = max(1, (total - 1) // per_page + 1)
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0

    start = page * per_page
    end = start + per_page
    sliced = filtered[start:end]

    owned_count = sum(1 for cid, _ in filtered if cid in owned_cards and owned_cards[cid].get("amount", 0) > 0)

    lines = []
    for cid, c in sliced:
        is_owned = cid in owned_cards and owned_cards[cid].get("amount", 0) > 0
        dot = "⬤" if is_owned else "◯"
        lines.append(f"{dot} {c['name']}")

    text = (
        f"<b>「 {anime_name} — {rarity_display} 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(lines) +
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote><b>Collected: ({owned_count}/{total})\n⬤  - Owned \n◯  - not owned</b></blockquote>"
    )
    if total_pages > 1:
        text += f"\nPage <b>{page+1}/{total_pages}</b>"

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="« Prev", callback_data=f"cl_r|{owner_id}|{anime_key}|{rarity_safe}|{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton(text="Next »", callback_data=f"cl_r|{owner_id}|{anime_key}|{rarity_safe}|{page+1}"))

    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="« Back to Rarities", callback_data=f"cl_an|{owner_id}|{anime_key}")])
    rows.append([InlineKeyboardButton(text="✕ Close", callback_data=f"close_msg|{owner_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cq.answer()


# ==========================================
# GUIDE WEBSITE (/guide)
# ==========================================
GUIDE_URL = "https://animated-cajeta-10b450.netlify.app/"


async def _send_guide_miniapp(message: Message):
    """Sends the actual guide message with the Open Guide Mini App button.
    Only valid in private chats — web_app buttons don't work in groups."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Open Guide", web_app=WebAppInfo(url=GUIDE_URL))]
    ])
    await message.reply(
        "<b>「 📖 GUIDE ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Everything about collecting, trading, and the shard economy — "
        "commands, drops, the store, stock market, mines, and more, all in one place</b>.\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@main_router.message(Command("guide"))
async def guide_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if message.chat.type == ChatType.PRIVATE:
        await _send_guide_miniapp(message)
        return

    # In groups: web_app buttons aren't allowed on a normal message, so
    # instead point the user to DM the bot — clicking the button deep-links
    # straight into /start?guide, which auto-opens the guide there.
    bot_info = await bot.get_me()
    deep_link = f"https://t.me/{bot_info.username}?start=guide"

    # If used as a reply, tag whoever was replied to — unless that's a bot
    # account or an anonymous channel post (sender_chat set, no real
    # from_user), in which case there's no one sensible to tag/DM, so it
    # just falls back to tagging the person who ran the command.
    reply_msg = message.reply_to_message
    if reply_msg and reply_msg.from_user and not reply_msg.from_user.is_bot:
        target_mention = get_mention(reply_msg.from_user.id, reply_msg.from_user.first_name)
    else:
        target_mention = get_mention(message.from_user.id, message.from_user.first_name)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Click here ", url=deep_link)]
    ])
    await message.reply(
        f" {target_mention}, <b>Guide available on DM!</b>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


# ==========================================
# SUPPORT QUERY SYSTEM — SUBMISSION (/qry)
# Users submit a question by replying to a message (reply is mandatory).
# It's logged to QUERY_GROUP_ID with a ticket number (Qry01, Qry02, ...).
# Admin answering (/aq) and listing unanswered tickets (/nansq) live in
# a_handlers.py.
# ==========================================
_QUERY_MEDIA_ATTRS = ("photo", "document", "video", "animation", "sticker", "voice", "audio", "video_note")


def _next_query_id(db: dict) -> str:
    settings = db.setdefault("settings", {})
    counter = settings.get("query_counter", 0) + 1
    settings["query_counter"] = counter
    return f"Qry{counter:02d}"


QUERY_USAGE_TEXT = "<b>Usage:</b> Reply to a message with <b>/qry</b> to submit it as your question."


@main_router.message(Command("qry"))
async def submit_query_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    reply_msg = message.reply_to_message
    if not reply_msg:
        await message.reply(QUERY_USAGE_TEXT, parse_mode=ParseMode.HTML)
        return

    user_id = str(uid_int)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    # Question text: typed args take priority; otherwise use the text/caption
    # of the message being replied to.
    question_text = (command.args or "").strip()
    if not question_text:
        question_text = (reply_msg.text or reply_msg.caption or "").strip()

    if not question_text:
        await message.reply("<b>That message has no text to use as a question.</b> Add your question after <b>/qry</b> instead.", parse_mode=ParseMode.HTML)
        return

    if len(question_text) < QUERY_MIN_LEN:
        await message.reply("<b>Your query is too short.</b> Please describe your issue in more detail.", parse_mode=ParseMode.HTML)
        return
    if len(question_text) > QUERY_MAX_LEN:
        await message.reply(f"<b>Your query is too long</b> (max {QUERY_MAX_LEN} characters).", parse_mode=ParseMode.HTML)
        return

    # --- Anti-spam: per-user cooldown ---
    now = time.time()
    last = _query_cooldowns.get(user_id, 0.0)
    if uid_int not in ADMIN_IDS and now - last < QUERY_COOLDOWN_SECS:
        rem = QUERY_COOLDOWN_SECS - (now - last)
        await message.reply(f"<b>Slow down!</b> You can submit another query in <b>{format_wait_mmss(rem)}</b>.", parse_mode=ParseMode.HTML)
        return

    # --- Anti-spam: daily submission cap ---
    user_data = db["users"][user_id]
    daily = get_query_daily_tracker(user_data)
    if uid_int not in ADMIN_IDS and daily["count"] >= QUERY_DAILY_LIMIT:
        await message.reply(f"<b>Daily limit reached.</b> You can submit up to <b>{QUERY_DAILY_LIMIT}</b> queries per day — please try again tomorrow.", parse_mode=ParseMode.HTML)
        return

    # --- Anti-spam: cap on open/unanswered tickets ---
    pending_count = sum(
        1 for q in db.get("queries", {}).values()
        if q.get("user_id") == user_id and q.get("status") == "pending"
    )
    if uid_int not in ADMIN_IDS and pending_count >= QUERY_MAX_PENDING:
        await message.reply(f"<b>Too many open queries.</b> You already have <b>{pending_count}</b> unanswered — please wait for a response before submitting more.", parse_mode=ParseMode.HTML)
        return

    # --- Create the ticket ---
    qry_id = _next_query_id(db)
    db["queries"][qry_id] = {
        "user_id": user_id,
        "name": message.from_user.first_name,
        "username": message.from_user.username,
        "question": question_text,
        "status": "pending",
        "created_at": int(time.time()),
        "log_msg_id": None,
        "answer": None,
        "answered_by": None,
        "answered_at": None
    }

    _query_cooldowns[user_id] = now
    daily["count"] += 1
    save_db()

    # --- Log to the query group ---
    asker_mention = get_mention(uid_int, message.from_user.first_name)
    safe_question = question_text.replace("<", "&lt;").replace(">", "&gt;")
    log_text = (
        "<b><u>New Query</u></b>\n\n"
        f"🎫 <b>Ticket :</b> {qry_id}\n"
        f"<b>From :</b> {asker_mention} ({uid_int})\n"
        f"<b>Question ❓:</b>\n"
        f"<blockquote>{safe_question}</blockquote>\n\n"
        f"<b>↳ Reply With : </b> /aq {qry_id}"
    )

    try:
        # Only forward the replied message itself when it carries media the
        # text log can't represent (a plain-text reply is already quoted
        # above, so copying it too would just post the same content twice).
        if any(getattr(reply_msg, attr, None) for attr in _QUERY_MEDIA_ATTRS):
            try:
                await bot.copy_message(chat_id=QUERY_GROUP_ID, from_chat_id=message.chat.id, message_id=reply_msg.message_id)
            except Exception:
                pass
        sent = await bot.send_message(chat_id=QUERY_GROUP_ID, text=log_text, parse_mode=ParseMode.HTML)
        db["queries"][qry_id]["log_msg_id"] = sent.message_id
        save_db()
    except Exception as e:
        print(f"[QUERY] Failed to log {qry_id} to group: {e}")

    await message.reply(
        f"✅ <b>Query submitted.</b>\n"
        f"🎫 <b>Ticket ID</b>: {qry_id}\n\n"
        f"<i>An admin will reply to you here in DM once it's answered.</i>",
        parse_mode=ParseMode.HTML
    )
