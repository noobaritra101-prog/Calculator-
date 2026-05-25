import time
import uuid
import random
import asyncio
import difflib
from datetime import datetime, timezone
from aiogram import F
from aiogram.types import (
    Message, CallbackQuery, InlineQuery, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQueryResultPhoto, InlineQueryResultCachedPhoto, InlineQueryResultArticle, 
    InputTextMessageContent, BufferedInputFile, InputMediaPhoto, ReactionTypeEmoji
)
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode, ChatType, ChatMemberStatus

import config
from config import (
    bot, main_router, ADMIN_IDS,
    DECK_PER_PAGE, CARDS_PER_PAGE, BROWSE_PER_PAGE,
    group_counters, active_drops, bot_start_time,
    spoiler_cache, RARITIES, RARITY_ORDER, RARITY_SAFE, SAFE_RARITY,
    format_rarity, load_db, save_db, ensure_user, ensure_group,
    get_mention, is_ghost_banned, is_shadow_banned
)

async def has_bot_in_bio(user_id: int) -> bool:
    try:
        bot_info = await bot.me()
        bot_username = f"@{bot_info.username}".lower()
        
        user_chat = await bot.get_chat(user_id)
        if user_chat.bio:
            return bot_username in user_chat.bio.lower()
    except Exception:
        pass
    return False

# ==========================================
# DAILY REWARDS CLAIM SYSTEM (/daily)
# ==========================================
@main_router.message(Command("daily"))
async def daily_reward_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    
    now = int(time.time())
    last_claim = db["users"][user_id].get("last_daily", 0)
    cooldown = 24 * 3600  

    if now - last_claim < cooldown:
        rem = cooldown - (now - last_claim)
        h, r = divmod(rem, 3600)
        m, _ = divmod(r, 60)
        await message.reply(f"⏳ <b>Daily already claimed!</b>\nReturn in <b>{h}h {m}m</b> to claim again.", parse_mode=ParseMode.HTML)
        return

    bio_bonus = await has_bot_in_bio(uid_int)
    base_reward = 50
    bonus_reward = 100 if bio_bonus else 0
    total_reward = base_reward + bonus_reward

    db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + total_reward
    db["users"][user_id]["last_daily"] = now
    save_db()

    msg = (
        "<b>「 💠 DAILY SHARDS CLAIMED ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🎁 Base Reward  ➜  <b>+{base_reward} Shards</b>\n"
    )
    if bio_bonus:
        msg += f"✨ Bio Bonus    ➜  <b>+{bonus_reward} Shards</b> (Bot username verified!)\n"
    else:
        msg += "💡 <i>Tip: Put our bot username in your profile Bio for an extra +100 Shards daily!</i>\n"
        
    msg += f"━━━━━━━━━━━━━━━━━\n💰 Total Claimed ➜ <b>{total_reward} Shards 💠</b>"
    await message.reply(msg, parse_mode=ParseMode.HTML)

# ==========================================
# WEEKLY REWARDS CLAIM SYSTEM (/weekly)
# ==========================================
@main_router.message(Command("weekly"))
async def weekly_reward_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    
    now = int(time.time())
    last_claim = db["users"][user_id].get("last_weekly", 0)
    cooldown = 7 * 24 * 3600 

    if now - last_claim < cooldown:
        rem = cooldown - (now - last_claim)
        d, r = divmod(rem, 86400)
        h, _ = divmod(r, 3600)
        await message.reply(f"⏳ <b>Weekly already claimed!</b>\nReturn in <b>{d}d {h}h</b> to claim again.", parse_mode=ParseMode.HTML)
        return

    bio_bonus = await has_bot_in_bio(uid_int)
    base_reward = 150
    bonus_reward = 50 if bio_bonus else 0
    total_reward = base_reward + bonus_reward

    db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + total_reward
    db["users"][user_id]["last_weekly"] = now
    save_db()

    msg = (
        "<b>「 💠 WEEKLY SHARDS CLAIMED ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🎁 Base Reward  ➜  <b>+{base_reward} Shards</b>\n"
    )
    if bio_bonus:
        msg += f"✨ Bio Bonus    ➜  <b>+{bonus_reward} Shards</b> (Bot username verified!)\n"
    else:
        msg += "💡 <i>Tip: Put our bot username in your profile Bio for an extra +50 Shards weekly!</i>\n"
        
    msg += f"━━━━━━━━━━━━━━━━━\n💰 Total Claimed ➜ <b>{total_reward} Shards 💠</b>"
    await message.reply(msg, parse_mode=ParseMode.HTML)

# ==========================================
# 10-ROLL BOWLING SYSTEM COMMAND (/roll)
# ==========================================
@main_router.message(Command("roll"))
async def bowling_roll_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    user_data = db["users"][user_id]
    
    now = int(time.time())
    
    if user_data.get("roll_count", 0) >= 10:
        if now < user_data.get("roll_reset", 0):
            rem = user_data["roll_reset"] - now
            h, r = divmod(rem, 3600)
            m, _ = divmod(r, 60)
            await message.reply(
                f"⏳ <b>Out of rolls!</b>\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"Your pins are resetting.\n"
                f"Return in <b>{h}h {m}m</b>.", 
                parse_mode=ParseMode.HTML
            )
            return
        else:
            db["users"][user_id]["roll_count"] = 0
            db["users"][user_id]["roll_reset"] = 0

    if db["users"][user_id].get("roll_count", 0) == 0:
        db["users"][user_id]["roll_reset"] = now + (8 * 3600)

    db["users"][user_id]["roll_count"] = db["users"][user_id].get("roll_count", 0) + 1
    rolls_left = 10 - db["users"][user_id]["roll_count"]
    save_db()

    dice_msg = await message.answer_dice(emoji="🎳")
    await asyncio.sleep(4) 

    if dice_msg.dice.value == 6:
        shards_won = random.randint(25, 100)
        db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + shards_won
        save_db()
        await message.reply(
            f"<b>「 STRIKE! ぁ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🎉 You knocked down all the pins!\n"
            f"💠 Earned: <b>{shards_won} Shards</b>\n"
            f"🎳 Rolls left: <b>{rolls_left}/10</b>",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.reply(
            f"<b>「 MISS ぁ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"You didn't clear the pins. Keep trying!\n"
            f"🎳 Rolls left: <b>{rolls_left}/10</b>",
            parse_mode=ParseMode.HTML
        )

# ==========================================
# 10-THROW BASKETBALL SYSTEM COMMAND (/throw)
# ==========================================
@main_router.message(Command("throw"))
async def basketball_throw_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    user_data = db["users"][user_id]
    
    now = int(time.time())
    
    if user_data.get("throw_count", 0) >= 10:
        if now < user_data.get("throw_reset", 0):
            rem = user_data["throw_reset"] - now
            h, r = divmod(rem, 3600)
            m, _ = divmod(r, 60)
            await message.reply(
                f"⏳ <b>Out of stamina!</b>\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"You need to rest your arms.\n"
                f"Return in <b>{h}h {m}m</b>.", 
                parse_mode=ParseMode.HTML
            )
            return
        else:
            db["users"][user_id]["throw_count"] = 0
            db["users"][user_id]["throw_reset"] = 0

    if db["users"][user_id].get("throw_count", 0) == 0:
        db["users"][user_id]["throw_reset"] = now + (8 * 3600)

    db["users"][user_id]["throw_count"] = db["users"][user_id].get("throw_count", 0) + 1
    throws_left = 10 - db["users"][user_id]["throw_count"]
    save_db()

    dice_msg = await message.answer_dice(emoji="🏀")
    await asyncio.sleep(4) 

    if dice_msg.dice.value >= 4:
        shards_won = random.randint(25, 100)
        db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + shards_won
        save_db()
        await message.reply(
            f"<b>「 SWISH! ぁ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🎉 Nothing but net!\n"
            f"💠 Earned: <b>{shards_won} Shards</b>\n"
            f"🏀 Throws left: <b>{throws_left}/10</b>",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.reply(
            f"<b>「 MISS ぁ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"You missed the shot. Keep practicing!\n"
            f"🏀 Throws left: <b>{throws_left}/10</b>",
            parse_mode=ParseMode.HTML
        )

# ==========================================
# DROP ENGINE & SEIZE
# ==========================================
@main_router.message(Command("setspawn"))
async def set_spawn_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if message.chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await message.reply("⚠️ This command can only be used in groups.")
        return
    
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR] and message.from_user.id not in ADMIN_IDS:
        await message.reply("⚠️ Only group admins can use this command.")
        return
        
    db = ensure_group(message.chat.id, message.chat.title)
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
                await message.reply("⚠️ Invalid ranges! Minimum is 100, maximum is 500, and min must be less than max.")
                return
        except ValueError:
            pass
    
    text = (
        "<b>⚙️ Spawn Configuration</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"📉 <b>Min messages</b> - <code>{s_min}</code>\n"
        f"📈 <b>Max messages</b> - <code>{s_max}</code>\n"
        "━━━━━━━━━━━━━━━━━\n"
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

    parts = cq.data.split("_")
    action_type = parts[1]
    op = parts[2]
    cid = "_".join(parts[3:])
    
    member = await bot.get_chat_member(int(cid), cq.from_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR] and cq.from_user.id not in ADMIN_IDS:
        await cq.answer("⚠️ Only group admins can adjust this.", show_alert=True)
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
        await cq.answer("⚠️ Limit reached!", show_alert=False)
        return

    db["groups"][cid]["spawn_min"] = s_min
    db["groups"][cid]["spawn_max"] = s_max
    save_db()

    config.group_counters[cid] = {"count": 0, "target": random.randint(s_min, s_max)}

    text = (
        "<b>⚙️ Spawn Configuration</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"📉 <b>Min messages</b> - <code>{s_min}</code>\n"
        f"📈 <b>Max messages</b> - <code>{s_max}</code>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<i>Rules: Min 100, Max 500. Min must be &lt; Max.</i>"
    )
    await cq.message.edit_text(text, reply_markup=cq.message.reply_markup, parse_mode=ParseMode.HTML)
    await cq.answer()

async def expire_drop(chat_id: str, msg_id: int):
    await asyncio.sleep(600)
    if chat_id in active_drops and active_drops[chat_id].get("message_id") == msg_id:
        del active_drops[chat_id]
        try: await bot.delete_message(chat_id=int(chat_id), message_id=msg_id)
        except Exception: pass

async def trigger_drop(chat_id: int):
    db = load_db()
    if not db["global_cards"]: return

    roll = random.randint(1, 100)
    if roll <= 80: target_rarity = "Basic 🃏"
    elif roll <= 98: target_rarity = "Elite ⚓"
    else: target_rarity = "Divine ❄️"

    pool = {k: v for k, v in db["global_cards"].items() if format_rarity(v["rarity"]) == target_rarity}
    if not pool: pool = db["global_cards"]

    card_id, card_data = random.choice(list(pool.items()))
    display_rarity = format_rarity(card_data['rarity'])

    caption = (
        "<b>「 CARD DROP ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "✦ <b><i>A wild card has appeared!</i></b>\n\n"
        f"🌟 Rarity ➜ <b>{display_rarity}</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💮 Use /seize [character name] to claim it!"
    )

    try:
        original_file_id = card_data["file_id"]
        if original_file_id in spoiler_cache:
            msg = await bot.send_photo(chat_id=chat_id, photo=spoiler_cache[original_file_id], caption=caption, parse_mode=ParseMode.HTML, has_spoiler=True)
        else:
            file_info = await bot.get_file(original_file_id)
            file_bytes = await bot.download_file(file_info.file_path)
            photo_input = BufferedInputFile(file_bytes.getvalue(), filename="card.jpg")
            msg = await bot.send_photo(chat_id=chat_id, photo=photo_input, caption=caption, parse_mode=ParseMode.HTML, has_spoiler=True)
            spoiler_cache[original_file_id] = msg.photo[-1].file_id

        active_drops[str(chat_id)] = {"card_id": card_id, "time": time.time(), "message_id": msg.message_id}
        asyncio.create_task(expire_drop(str(chat_id), msg.message_id))
        
        cid = str(chat_id)
        if cid in db["groups"]:
            db["groups"][cid]["drops"] = db["groups"][cid].get("drops", 0) + 1
            save_db()
    except Exception as e: print(f"[DROP] Error: {e}")

@main_router.message(Command("seize"))
async def seize_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    chat_id = message.chat.id
    cid_str = str(chat_id)
    
    if cid_str not in active_drops: return
    if not command.args:
        await message.reply("⚠️ Provide the character name!\nFormat: <code>/seize</code> [name]", parse_mode=ParseMode.HTML)
        return
        
    drop_data = active_drops[cid_str]
    card_id   = drop_data["card_id"]
    drop_time = drop_data["time"]
    
    db = load_db()
    global_card = db["global_cards"].get(card_id)
    if not global_card: return
    
    target_name = global_card["name"].lower()
    query       = command.args.lower().strip()
    
    matched = False
    if len(query) < 3 and query != target_name: matched = False
    elif query in target_name: matched = True
    else:
        ratio = difflib.SequenceMatcher(None, query, target_name).ratio()
        if ratio > 0.6: matched = True
        
    if not matched:
        await message.reply("🚫「 𝗪𝗥𝗢𝗡𝗚 𝗚𝗨𝗘𝗦𝗦 ぁ 」\n\n➜ 𝗧𝗿𝘆 𝗔𝗴𝗮𝗶𝗻", parse_mode=ParseMode.HTML)
        return
    
    time_taken = round(time.time() - drop_time, 2)
    del active_drops[cid_str] 
    
    try:
        await bot.set_message_reaction(chat_id=chat_id, message_id=message.message_id, reaction=[ReactionTypeEmoji(emoji="🎉")])
    except Exception: pass
    
    user_id = str(uid_int)
    name    = message.from_user.first_name
    uname   = message.from_user.username
    db      = ensure_user(user_id, name, uname)
    
    base_shards = 10
    rarity_normalized = format_rarity(global_card["rarity"])
    if rarity_normalized == "Elite ⚓": base_shards = 25
    elif rarity_normalized == "Divine ❄️": base_shards = 100
    
    speed_bonus = 15 if time_taken <= 3.0 else 0
    recycle_bonus = 10 if card_id in db["users"][user_id]["cards"] else 0
        
    total_earned = base_shards + speed_bonus + recycle_bonus
    db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + total_earned
    
    if card_id not in db["users"][user_id]["cards"]:
        db["users"][user_id]["cards"][card_id] = {"name": global_card["name"], "rarity": global_card["rarity"], "amount": 0}
    db["users"][user_id]["cards"][card_id]["amount"] += 1
    db["users"][user_id]["total_claimed"] = db["users"][user_id].get("total_claimed", 0) + 1
    
    if cid_str in db["groups"]:
        db["groups"][cid_str]["claims"] = db["groups"][cid_str].get("claims", 0) + 1
    save_db()
    
    display_rarity = format_rarity(global_card['rarity'])
    bonus_breakdown = f" (+{speed_bonus} Speed⚡)" if speed_bonus else ""
    if recycle_bonus: bonus_breakdown += " (+10 Recycle♻️)"
    
    winner_text = (
        "<b>「 🎊 CARD SEIZED ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f" 🎊 <b><i>{get_mention(user_id, name)}</i></b> seized the card in <b>{time_taken}s</b>!\n\n"
        f" 👤 Character ➜  <b>{global_card['name']} 《{display_rarity}》</b>\n"
        f" 📺 Anime   ➜ <b>{global_card['anime']}</b>\n"
        f" 💠 Economy ➜ Earned <b>{total_earned}</b> Nexus Shards{bonus_breakdown}!\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "➜ 📖 Use /deck to <b>view your collection</b>."
    )
    try: await message.reply(winner_text, parse_mode=ParseMode.HTML)
    except Exception: pass

@main_router.message(F.text & ~F.text.startswith("/"))
async def chat_mining_handler(message: Message):
    if message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP] and random.random() < 0.05:
        uid_int = message.from_user.id
        if is_ghost_banned(uid_int) or is_shadow_banned(uid_int) or message.from_user.is_bot: return
        
        user_id = str(uid_int)
        db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
        mined_amt = random.randint(2, 6)
        
        db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + mined_amt
        save_db()

# ==========================================
# PLAYER INTERFACES & PARSERS (/flex & /check)
# ==========================================
@main_router.message(Command("check"))
async def check_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    db = load_db()
    if not db.get("global_cards"):
        await message.reply("⚠️ No cards in the database yet.", parse_mode=ParseMode.HTML)
        return

    if not command.args:
        await message.reply("⚠️ <b>Usage:</b> <code>/check <card name></code>\nExample: <code>/check goku</code>", parse_mode=ParseMode.HTML)
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
        await message.reply(f"❌ No cards found globally matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    total_owned = sum(udata["cards"][matched_cid]["amount"] for udata in db["users"].values() if "cards" in udata and matched_cid in udata["cards"])
    display_rarity = format_rarity(matched_data['rarity'])

    caption = (
        f"<b>「 ✦ CARD CHECK ぁ ✦ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"➜ 👤 <b><i>Character</i></b>  ➜  <b>{matched_data['name']}</b>\n"
        f"➜ 📺 <b><i>Anime</i></b>      ➜  <b>{matched_data.get('anime', '?')}</b>\n"
        f"➜ 🌟 <b><i>Rarity</i></b>     ➜  <b>{display_rarity}</b>\n"
        f"➜ 📦 <b><i>Global Owned</i></b>  ➜  <b>×{total_owned}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
    )
    await message.reply_photo(photo=matched_data["file_id"], caption=caption, parse_mode=ParseMode.HTML)

@main_router.message(Command("flex"))
async def flex_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    
    if not command.args:
        await message.reply("⚠️ <b>Usage:</b> <code>/flex <card name></code>", parse_mode=ParseMode.HTML)
        return

    query = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})
    
    if not my_cards:
        await message.reply("❌ You don't own any cards yet!", parse_mode=ParseMode.HTML)
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
        await message.reply(f"❌ You do not own a card matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data = db["global_cards"].get(matched_cid, {})
    display_rarity = format_rarity(matched_data['rarity'])

    caption = (
        f"<b>「 ✦ CARD FLEX ぁ ✦ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b><i>Character</i></b>  ➜  <b>{matched_data['name']}</b>\n"
        f"📺 <b><i>Anime</i></b>      ➜  <b>{global_data.get('anime', '?')}</b>\n"
        f"🌟 <b><i>Rarity</i></b>     ➜  <b>{display_rarity}</b>\n"
        f"📦 <b><i>Owned</i></b>      ➜  <b>×{matched_data['amount']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
    )
    await message.reply_photo(photo=global_data.get("file_id"), caption=caption, parse_mode=ParseMode.HTML)

# ==========================================
# /special (Spoiler + Confirmation)
# ==========================================
@main_router.message(Command("special"))
async def set_special_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    
    if not command.args:
        await message.reply("⚠️ <b>Usage:</b> <code>/special <card name></code>", parse_mode=ParseMode.HTML)
        return

    query = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})
    
    if not my_cards:
        await message.reply("❌ You don't own any cards yet!", parse_mode=ParseMode.HTML)
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
        await message.reply(f"❌ You do not own a card matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data = db["global_cards"].get(matched_cid, {})
    display_rarity = format_rarity(matched_data['rarity'])

    caption = (
        f"<b>「 SET SPECIAL CARD ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 Character ➜ <b>{matched_data['name']}</b>\n"
        f"🌟 Rarity  ➜   {display_rarity}\n\n"
        f"Are you sure you want to set this as your Special Card?"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes, Set Special", callback_data=f"setsp_{matched_cid}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")]
    ])
    await message.reply_photo(photo=global_data.get("file_id"), caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML, has_spoiler=True)

@main_router.callback_query(F.data.startswith("setsp_"))
async def confirm_special_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    user_id = str(cq.from_user.id)
    db = load_db()
    card_id = cq.data.split("_")[1]

    if card_id not in db["users"].get(user_id, {}).get("cards", {}):
        await cq.answer("❌ You don't own this card anymore!", show_alert=True)
        return

    db["users"][user_id]["special_card"] = card_id
    save_db()
    
    cdata = db["users"][user_id]["cards"][card_id]
    display_rarity = format_rarity(cdata['rarity'])
    caption = (
        f"<b>「 SPECIAL CARD SET ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 Character ➜ <b>{cdata['name']}</b>\n"
        f"🌟 Rarity    ➜ {display_rarity}\n\n"
        f"✨ Pinned to the top of your deck!"
    )
    await cq.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=None)
    await cq.answer("✅ Special card updated!")

# ==========================================
# /gift (Spoiler + Confirmation)
# ==========================================
@main_router.message(Command("gift"))
async def gift_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Reply to a user's message to gift them a card.", parse_mode=ParseMode.HTML)
        return
        
    target_user = message.reply_to_message.from_user
    if target_user.is_bot:
        await message.reply("❌ You cannot gift cards to bots.", parse_mode=ParseMode.HTML)
        return
    if str(target_user.id) == str(message.from_user.id):
        await message.reply("❌ You cannot gift a card to yourself.", parse_mode=ParseMode.HTML)
        return
    if not command.args:
        await message.reply("⚠️ <b>Usage:</b> <code>/gift <card name></code>", parse_mode=ParseMode.HTML)
        return

    user_id = str(message.from_user.id)
    target_id = str(target_user.id)
    
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    db = ensure_user(target_id, target_user.first_name, target_user.username)
    
    query = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})
    
    if not my_cards:
        await message.reply("❌ You don't own any cards yet!", parse_mode=ParseMode.HTML)
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
        await message.reply(f"❌ You do not own a card matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data = db["global_cards"].get(matched_cid, {})
    display_rarity = format_rarity(matched_data['rarity'])

    caption = (
        f"<b>「 GIFT CARD ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 Character ┊ <b>{matched_data['name']}</b>\n"
        f"🌟 Rarity    ┊ {display_rarity}\n\n"
        f"Are you sure you want to gift this to {get_mention(target_user.id, target_user.first_name)}?"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Yes, Gift Card", callback_data=f"cfgift_{target_id}_{matched_cid}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")]
    ])
    await message.reply_photo(photo=global_data.get("file_id"), caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML, has_spoiler=True)

@main_router.callback_query(F.data.startswith("cfgift_"))
async def confirm_gift_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    parts = cq.data.split("_")
    target_id = parts[1]
    card_id = parts[2]
    user_id = str(cq.from_user.id)

    db = load_db()
    my_cards = db["users"].get(user_id, {}).get("cards", {})

    if card_id not in my_cards or my_cards[card_id]["amount"] <= 0:
        await cq.answer("❌ You don't own this card anymore!", show_alert=True)
        return

    card_data = my_cards[card_id]
    my_cards[card_id]["amount"] -= 1
    if my_cards[card_id]["amount"] <= 0:
        del my_cards[card_id]
        if db["users"][user_id].get("special_card") == card_id:
            db["users"][user_id]["special_card"] = None
            
    target_cards = db["users"][target_id].setdefault("cards", {})
    if card_id not in target_cards:
        target_cards[card_id] = {"name": card_data["name"], "rarity": card_data["rarity"], "amount": 0}
    target_cards[card_id]["amount"] += 1
    save_db()

    target_name = db["users"][target_id].get("name", "User")
    display_rarity = format_rarity(card_data['rarity'])
    
    caption = (
        f"<b>「 CARD GIFTED 🎁 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"You successfully gifted <b>{card_data['name']}</b> [{display_rarity}] to {get_mention(target_id, target_name)}!"
    )
    await cq.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=None)
    await cq.answer("🎁 Gift sent successfully!")

# ==========================================
# GLOBAL CANCELLATION & CLOSE HANDLERS
# ==========================================
@main_router.callback_query(F.data == "cancel_action")
async def cancel_action_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    try:
        await cq.message.edit_caption(caption="❌ Action cancelled.", reply_markup=None)
    except Exception:
        await cq.message.edit_text("❌ Action cancelled.", reply_markup=None)
    await cq.answer()

@main_router.callback_query(F.data == "close_msg")
async def close_msg_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.answer()

# ==========================================
# DECK DISPLAY LAYER (/deck)
# ==========================================
async def send_deck_page(message: Message, db: dict, user_id: str, page=0, edit=False):
    user_data = db["users"][user_id]
    cards     = user_data.get("cards", {})
    items     = list(cards.items())
    user_name = user_data.get('name', 'User')

    if not items:
        text = "<b>「 COLLECTION EMPTY ぁ 」</b>\n━━━━━━━━━━━━━━━━━\nYou haven't collected any cards yet!\nWait for a drop in the group."
        if edit and isinstance(message, CallbackQuery): await message.message.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            if isinstance(message, CallbackQuery): await message.message.reply(text, parse_mode=ParseMode.HTML)
            else: await message.reply(text, parse_mode=ParseMode.HTML)
        return

    global_cards = db.get("global_cards", {})

    for i in range(len(items)):
        cid, cdata = items[i]
        anime = global_cards.get(cid, {}).get("anime", "Unknown")
        items[i] = (cid, cdata, anime)

    sort_pref = user_data.get("sort_pref", "default")
    if sort_pref == "rarity": items.sort(key=lambda x: (x[2], RARITY_ORDER.get(format_rarity(x[1]["rarity"]), 99)))
    elif sort_pref == "name": items.sort(key=lambda x: (x[2], x[1]["name"].lower()))
    elif sort_pref == "amount": items.sort(key=lambda x: (x[2], x[1]["amount"]), reverse=True)
    else: items.sort(key=lambda x: x[2])

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

    display_pic = None
    if special_item: display_pic = db["global_cards"].get(special_item[0], {}).get("file_id")
    if not display_pic and page_items: display_pic = db["global_cards"].get(page_items[0][0], {}).get("file_id")

    safe_name = str(user_name).replace("<", "&lt;").replace(">", "&gt;")
    text = f"『  ぁ 𝘾𝘼𝙍𝘿 𝘿𝙀𝘾𝙆  - {safe_name} 』\n━━━━━━━━━━━━━━━━━\n\n"

    if page == 0 and special_item:
        scid, scdata, sanime = special_item
        disp_rarity = format_rarity(scdata['rarity'])
        text += f"𝗔𝗻𝗶𝗺𝗲  - <b>{sanime} ↧</b>\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
        text += f"✨ <b><i><code>{scdata['name']}</code></i> - [{disp_rarity}]  ×{scdata['amount']} </b>\n"
        text += "\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n\n"

    current_anime = None
    for cid, cdata, anime in page_items:
        if anime != current_anime:
            if current_anime is not None: text += "\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n\n"
            text += f"𝗔𝗻𝗶𝗺𝗲  - <b>{anime} ↧</b>\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
            current_anime = anime

        disp_rarity = format_rarity(cdata['rarity'])
        text += f"<b>✦ <i><code>{cdata['name']}</code></i> - [{disp_rarity}]  ×{cdata['amount']} </b>\n"

    if current_anime is not None: text += "\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"

    nav_buttons = []
    if page > 0: nav_buttons.append(InlineKeyboardButton(text="❮", callback_data=f"deck_prev_{user_id}_{page-1}"))
    else: nav_buttons.append(InlineKeyboardButton(text="❮", callback_data="noop"))
    if end < len(items): nav_buttons.append(InlineKeyboardButton(text="❯", callback_data=f"deck_next_{user_id}_{page+1}"))
    else: nav_buttons.append(InlineKeyboardButton(text="❯", callback_data="noop"))

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⌈ 𝗣𝗮𝗴𝗲 {page+1}/{total_pages} ⌋", callback_data=f"page_alert_{page+1}")],
        nav_buttons,
        [InlineKeyboardButton(text="View collection", switch_inline_query_current_chat=str(user_id))]
    ])

    if display_pic:
        if edit and isinstance(message, CallbackQuery):
            try: await message.message.edit_media(InputMediaPhoto(media=display_pic, caption=text, parse_mode=ParseMode.HTML), reply_markup=keyboard)
            except Exception: pass
        else:
            if isinstance(message, CallbackQuery): await message.message.answer_photo(photo=display_pic, caption=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            else: await message.reply_photo(photo=display_pic, caption=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        if edit and isinstance(message, CallbackQuery): await message.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            if isinstance(message, CallbackQuery): await message.message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            else: await message.reply(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@main_router.message(Command("deck"))
async def view_deck_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    try:
        member = await bot.get_chat_member(config.MAIN_GROUP_USERNAME, message.from_user.id)
        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, ChatMemberStatus.RESTRICTED]: raise Exception("Not member")
    except Exception:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✦ Join Group", url=config.MAIN_GROUP_LINK)],
            [InlineKeyboardButton(text="↻ Try Again", callback_data="check_deck_access")]
        ])
        await message.reply("⚠️「 𝗔𝗖𝗖𝗘𝗦𝗦 𝗗𝗘𝗡𝗜𝗘DEN ぁ 」\n\n🧿 𝗧𝗼 𝘃𝗶𝗲𝘄 𝘆𝗼𝘂𝗿 𝗱𝗲𝗰𝗸 𝗬𝗼𝘂 𝗺𝘂𝘀𝘁 𝗷𝗼𝗶𝗻 𝗼𝘂𝗿 𝗠𝗮𝗶𝗻 𝗚𝗿𝗼𝘂𝗽", reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    await send_deck_page(message, db, user_id, page=0, edit=False)

@main_router.callback_query(F.data == "check_deck_access")
async def check_deck_access_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    try:
        member = await bot.get_chat_member(config.MAIN_GROUP_USERNAME, cq.from_user.id)
        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, ChatMemberStatus.RESTRICTED]:
            await cq.answer("❌ You haven't joined the group yet!", show_alert=True)
            return
    except Exception:
        await cq.answer("❌ You haven't joined the group yet!", show_alert=True)
        return

    await cq.message.delete()
    user_id = str(cq.from_user.id)
    name    = cq.from_user.first_name
    db      = ensure_user(user_id, name, cq.from_user.username)
    await send_deck_page(cq, db, user_id, page=0, edit=False)
    await cq.answer("✅ Access Granted!")

@main_router.callback_query(F.data.startswith("deck_"))
async def deck_nav_cb(callback_query: CallbackQuery):
    uid_int = callback_query.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await callback_query.answer("🔇 You are currently restricted.", show_alert=True)
        return

    parts = callback_query.data.split("_")
    direction, owner_id, page_str = parts[1], parts[2], parts[3]
    if str(callback_query.from_user.id) != owner_id:
        await callback_query.answer("❌ Not your deck!", show_alert=True)
        return
    db = load_db()
    await send_deck_page(callback_query, db, owner_id, int(page_str), edit=True)
    await callback_query.answer()

@main_router.callback_query(F.data.startswith("page_alert_"))
async def page_indicator_alert(callback_query: CallbackQuery):
    uid_int = callback_query.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    page_num = callback_query.data.split("_")[2]
    await callback_query.answer(f"ℹ️ You are currently on page {page_num}.", show_alert=True)

@main_router.callback_query(F.data == "noop")
async def noop_cb(callback_query: CallbackQuery): 
    uid_int = callback_query.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    await callback_query.answer()

# ==========================================
# /sortcards INTERFACE PRESETS
# ==========================================
@main_router.message(Command("sortcards"))
async def sort_cards(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(message.from_user.id)
    name    = message.from_user.first_name
    db      = ensure_user(user_id, name, message.from_user.username)
    current_sort = db["users"][user_id].get("sort_pref", "default").title()
    
    text = (
        f"<b>「 SORTING ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🌟 Rarity  — Divine → Elite → Basic\n"
        f"🔤 Name    — A → Z\n"
        f"📦 Amount  — Most owned first\n"
        f"🔄 Default — Claim order\n"
        f"━━━━━━━━━━━━━━━━━\n"
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
    uid_int = callback_query.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await callback_query.answer("🔇 You are currently restricted.", show_alert=True)
        return

    parts = callback_query.data.split("_")
    owner_id, mode = parts[1], parts[2]
    if str(callback_query.from_user.id) != owner_id: return
        
    db = load_db()
    db["users"][owner_id]["sort_pref"] = mode
    save_db()
    await callback_query.answer(f"✅ Sorting order saved: {mode.title()}")
    
    text = (
        f"<b>「 SORTING ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🌟 Rarity  — Divine → Elite → Basic\n"
        f"🔤 Name    — A → Z\n"
        f"📦 Amount  — Most owned first\n"
        f"🔄 Default — Claim order\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<b>Current sorting order </b>- {mode.title()}"
    )
    await callback_query.message.edit_text(text, reply_markup=callback_query.message.reply_markup, parse_mode=ParseMode.HTML)

# ==========================================
# /profile ENGINE PARSER DESIGN LAYOUTS
# ==========================================
@main_router.message(Command("profile"))
async def view_profile(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id   = str(message.from_user.id)
    name      = message.from_user.first_name
    username  = message.from_user.username
    db        = ensure_user(user_id, name, username)
    user_data = db["users"][user_id]
    cards     = user_data.get("cards", {})

    unique_cards  = len(cards)
    joined_year   = datetime.fromtimestamp(user_data.get("joined", int(time.time())), tz=timezone.utc).strftime("%Y")
    shards        = user_data.get("nexus_shards", 0)
    
    sorted_users = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)
    rank = 9999
    for i, (uid, udata) in enumerate(sorted_users):
        if uid == user_id:
            rank = i + 1
            break

    uname_display = f"@{username}" if username else "None"
    now = time.time()
    if int(user_id) in config.shadow_banned and config.shadow_banned[int(user_id)] > now:
        rem = int(config.shadow_banned[int(user_id)] - now)
        m, s = divmod(rem, 60)
        ban_status = f"Restricted 🔇 ({m}m {s}s remaining)"
    else: ban_status = "None 🟢"

    safe_name = str(name).replace("<", "&lt;").replace(">", "&gt;")
    name_link = f'<a href="tg://user?id={user_id}">{safe_name}</a>'

    profile_text = (
        "「 𝙉𝙀𝙓𝙐𝙎 : 𝙋𝙍𝙊𝙁𝙄𝙇𝙀 ぁ 」\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"❖ 𝙉𝙖𝙢𝙚          ➜ {name_link}\n"
        f"❖ 𝙐𝙨𝙚𝙧𝙣𝙖𝙢𝙚     ➜ {uname_display}\n"
        f"❖ 𝙐𝙨𝙚𝙧 𝙄𝘿       ➜ <code>{user_id}</code>\n"
        f"❖ 𝙔𝙚𝙖𝙧 𝙅𝙤𝙞𝙣𝙚𝙙   ➜ {joined_year}\n\n"
        f"❖ 𝙏𝙤𝙩𝙖𝙡 𝘾𝙖𝙧𝙙𝙨   ➜ {unique_cards}\n"
        f"❖ 𝙍𝙖𝙣𝙠          ➜ #{rank}\n"
        f"❖ 𝙉𝙚𝙭𝙪𝙨 𝙎𝙝𝙖𝙧𝙙𝙨  ➜ <b>{shards} 💠</b>\n"
        f"❖ 𝙎𝙝𝙖𝙙𝙤𝙬 𝘽𝙖𝙣   ➜ {ban_status}\n\n"
        "━━━━━━━━━━━━━━━━━"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Close", callback_data="close_msg")]])
    photo_sent = False
    try:
        photos = await bot.get_user_profile_photos(int(user_id), limit=1)
        if photos.total_count > 0:
            await message.reply_photo(photo=photos.photos[0][0].file_id, caption=profile_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            photo_sent = True
    except Exception: pass
    
    if not photo_sent: 
        try:
            await message.reply(profile_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception: pass

# ==========================================
# /leaderboard WRAPPERS
# ==========================================
@main_router.message(Command("leaderboard", "top"))
async def leaderboard(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    db  = load_db()
    top = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)
    
    user_id = str(message.from_user.id)
    user_rank = 0
    for i, (uid, ud) in enumerate(top):
        if uid == user_id:
            user_rank = i + 1
            break
            
    rank_text = f"#{user_rank}" if user_rank > 0 else "Unranked"
    symbols = ["✦", "✧", "❖"] + ["◈"] * 7
    
    text = (
        "<b>「  𝘓𝘌𝘈𝘋𝘌𝘙𝘉𝘖𝘈𝘙𝘋 ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "〄 <b>𝙏𝙤𝙥 𝘾𝙤𝙡𝙡𝙚𝙘𝙩𝙤𝙧𝙨</b>\n\n"
    )
    for i, (uid, ud) in enumerate(top[:10]):
        sym = symbols[i%10]
        safe_name = str(ud.get('name','Unknown')).replace("<", "&lt;").replace(">", "&gt;")
        text += f"{sym} <b>{safe_name}</b> ┊ 🎴 {len(ud.get('cards', {}))}\n"
        
    text += "\n━━━━━━━━━━━━━━━━━"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❖ Your Rank - {rank_text}", callback_data="noop")],
        [InlineKeyboardButton(text="✕ Close", callback_data="close_msg")]
    ])

    pic = db.get("settings", {}).get("leaderboard_pic")
    if pic: await message.reply_photo(photo=pic, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else: await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ==========================================
# GLOBAL ENGINES DRILLED DATASET (/cards)
# ==========================================
@main_router.message(Command("cards", "total_cards", "all_cards"))
async def cards_browser(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    db = load_db()
    if not db.get("global_cards"):
        await message.reply("⚠️ Database is empty.", parse_mode=ParseMode.HTML)
        return
    await show_anime_list(message, edit=False)

async def show_anime_list(message: Message, edit=False):
    db    = load_db()
    cards = db.get("global_cards", {})
    anime_map = {}
    for cd in cards.values(): anime_map[cd["anime"]] = anime_map.get(cd["anime"], 0) + 1

    sorted_animes = sorted(anime_map.items(), key=lambda x: x[1], reverse=True)
    rarity_lines = []
    for r in RARITIES:
        n = sum(1 for c in cards.values() if format_rarity(c["rarity"]) == r)
        rarity_lines.append(f"  ✦ {r} ┊ <b>{n}</b>")

    text = (
        f"<b>「 ANIME NEXUS : CARD DATABASE ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🎴 Total Cards  ┊ <b>{len(cards)}</b>\n"
        f"📺 Anime Series ┊ <b>{len(sorted_animes)}</b>\n\n"
        f"── Rarity Breakdown ──\n"
        f"{chr(10).join(rarity_lines)}\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📌 Choose an anime series:"
    )

    buttons = []
    row = []
    for anime, count in sorted_animes[:18]:
        label = (anime[:16] + "…" if len(anime) > 16 else anime) + f" ({count})"
        safe  = anime.replace("|", "¦")[:35]
        row.append(InlineKeyboardButton(text=f"📺 {label}", callback_data=f"an|{safe}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row: buttons.append(row)

    buttons.append([InlineKeyboardButton(text="✦ All Divine ❄️", callback_data="gr|divine"), InlineKeyboardButton(text="✦ All Elite ⚓", callback_data="gr|elite")])
    buttons.append([InlineKeyboardButton(text="✦ All Basic 🃏", callback_data="gr|basic"), InlineKeyboardButton(text="📋 Full List", callback_data="gr|all")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit and isinstance(message, CallbackQuery): await message.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else: await message.reply(text, reply_markup=markup, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data.startswith("an|"))
async def anime_rarity_picker(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    await cq.answer()
    anime_name = cq.data[3:].replace("¦", "|")
    db    = load_db()
    cards = db.get("global_cards", {})

    rarity_count = {}
    for cd in cards.values():
        if cd["anime"] == anime_name:
            rk = format_rarity(cd["rarity"])
            rarity_count[rk] = rarity_count.get(rk, 0) + 1

    total = sum(rarity_count.values())
    if not total: return

    lines = [f"  ✦ <b>{r}</b>  ┊  {rarity_count.get(r.strip(), 0)} card{'s' if rarity_count.get(r.strip(), 0)!=1 else ''}" for r in RARITIES]
    text = (
        f"<b>「 {anime_name.upper()} ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📺 <b>{anime_name}</b>\n"
        f"🎴 Total: <b>{total}</b> cards\n\n"
        f"Choose a rarity to browse:\n\n"
        f"{chr(10).join(lines)}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

    safe_anime = anime_name.replace("|", "¦")[:35]
    buttons = []
    for r in RARITIES:
        n = rarity_count.get(r.strip(), 0)
        if n > 0:
            buttons.append([InlineKeyboardButton(text=f"✦ {r}  ({n} cards)", callback_data=f"acl|{safe_anime}|{RARITY_SAFE[r]}|0")])

    buttons.append([InlineKeyboardButton(text="◀️ Back to Anime List", callback_data="back_anime")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "back_anime")
async def back_to_anime(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    await cq.answer(); await show_anime_list(cq, edit=True)

@main_router.callback_query(F.data.startswith("acl|"))
async def anime_card_list(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    await cq.answer()
    parts      = cq.data.split("|")
    safe_anime = parts[1]
    safe_r     = parts[2]
    page       = int(parts[3])

    anime_name  = safe_anime.replace("¦", "|")
    rarity_name = SAFE_RARITY.get(safe_r, safe_r)
    db    = load_db()
    cards = db.get("global_cards", {})

    matched = sorted([(cid, cd) for cid, cd in cards.items() if cd["anime"] == anime_name and format_rarity(cd["rarity"]) == rarity_name], key=lambda x: x[1]["name"])
    if not matched: return

    total_m     = len(matched)
    total_pages = max(1, (total_m - 1) // BROWSE_PER_PAGE + 1)
    start       = page * BROWSE_PER_PAGE
    end         = min(start + BROWSE_PER_PAGE, total_m)

    lines = "\n".join(f"  ✦ <b>{cd['name']}</b>  <code>{cid}</code>" for cid, cd in matched[start:end])
    text = (
        f"<b>「 {anime_name.upper()} ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📺 {anime_name}\n"
        f"🌟 <b>{rarity_name}</b>\n"
        f"🎴 <b>{total_m}</b> cards  ·  Page <b>{page+1}/{total_pages}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"{lines}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"acl|{safe_anime}|{safe_r}|{page-1}"))
    if end < total_m: nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"acl|{safe_anime}|{safe_r}|{page+1}"))

    buttons = []
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="◀️ Back to Rarity", callback_data=f"an|{safe_anime}")])
    buttons.append([InlineKeyboardButton(text="🏠 Anime List",      callback_data="back_anime")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data.startswith("gr|"))
async def global_rarity(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    await cq.answer()
    key   = cq.data[3:]
    db    = load_db()
    cards = db.get("global_cards", {})

    if key == "all":
        items = sorted(cards.items(), key=lambda x: (x[1]["anime"], x[1]["name"]))
        lines = "\n".join(f"  ✦ <b>{cd['name']}</b> — <i>{cd['anime']}</i>  [{format_rarity(cd['rarity'])}]" for _, cd in items[:80])
        extra = f"\n<i>...and {len(items)-80} more. Use anime filter.</i>" if len(items) > 80 else ""
        text  = f"<b>「 ALL CARDS ぁ 」</b>\n━━━━━━━━━━━━━━━━━\n🎴 Total: <b>{len(items)}</b>\n\n{lines}{extra}\n━━━━━━━━━━━━━━━━━"
    else:
        rarity_name = SAFE_RARITY.get(key)
        matched = sorted([(cid, cd) for cid, cd in cards.items() if format_rarity(cd["rarity"]) == rarity_name], key=lambda x: (x[1]["anime"], x[1]["name"]))
        lines = "\n".join(f"  ✦ <b>{cd['name']}</b> — <i>{cd['anime']}</i>" for _, cd in matched[:80])
        extra = f"\n<i>...and {len(matched)-80} more.</i>" if len(matched) > 80 else ""
        text  = f"<b>「 {rarity_name.upper()} ぁ 」</b>\n━━━━━━━━━━━━━━━━━\n🌟 <b>{rarity_name}</b>\n🎴 Total: <b>{len(matched)}</b>\n\n{lines}{extra}\n━━━━━━━━━━━━━━━━━"

    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Back", callback_data="back_anime")]]), parse_mode=ParseMode.HTML)

# ==========================================
# INLINE BROWSER EXECUTION
# ==========================================
@main_router.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    uid_int = inline_query.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    query_raw = inline_query.query.strip()
    parts = query_raw.split(maxsplit=1)
    target_user_id = str(inline_query.from_user.id)
    query = query_raw.lower()
    
    if parts and parts[0].isdigit():
        target_user_id = parts[0]
        query = parts[1].lower() if len(parts) > 1 else ""
        
    db           = ensure_user(str(inline_query.from_user.id), inline_query.from_user.first_name, inline_query.from_user.username)
    cards        = db["users"].get(target_user_id, {}).get("cards", {})
    global_cards = db.get("global_cards", {})
    results      = []
    
    items = list(cards.items())
    sort_pref = db["users"].get(target_user_id, {}).get("sort_pref", "default")
    if sort_pref == "rarity": items.sort(key=lambda x: RARITY_ORDER.get(format_rarity(x[1]["rarity"]), 99))
    elif sort_pref == "amount": items.sort(key=lambda x: x[1]["amount"], reverse=True)
    else: items.sort(key=lambda x: x[1]["name"].lower())

    for cid, cdata in items[:50]:
        if query and query not in cdata["name"].lower() and query not in cdata["rarity"].lower(): continue
        full = global_cards.get(cid, {})
        file_id = full.get("file_id", "")
        if not file_id or len(file_id) < 10: continue

        disp_rarity = format_rarity(cdata['rarity'])
        caption_text = (
            f"<b>「 ✦ 𝗖𝗔𝗥𝗗 🇮𝗡𝗙𝗢 ぁ ✦ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"➜ 🆔 <b><i>ID</i></b>         ➜  <code>{cid}</code>\n"
            f"➜ 👤 <b><i>Character</i></b>  ➜  <b>{cdata['name']}</b>\n"
            f"➜ 📺 <b><i>Anime</i></b>      ➜  <b>{full.get('anime', '?')}</b>\n"
            f"➜ 🌟 <b><i>Rarity</i></b>     ➜  <b>{disp_rarity}</b>\n"
            f"➜ 📦 <b><i>Owned</i></b>      ➜  <b>×{cdata['amount']}</b>\n"
            f"━━━━━━━━━━━━━━━━━"
        )

        if file_id.startswith("http://") or file_id.startswith("https://"):
            results.append(InlineQueryResultPhoto(id=cid, photo_url=file_id, thumbnail_url=file_id, caption=caption_text, parse_mode=ParseMode.HTML))
        else:
            results.append(InlineQueryResultCachedPhoto(id=cid, photo_file_id=file_id, caption=caption_text, parse_mode=ParseMode.HTML))

    if not results:
        results.append(InlineQueryResultArticle(id="empty", title="No cards found", description="Try a different search or claim cards first!", input_message_content=InputTextMessageContent(message_text="No cards match your search. Claim some in the group!", parse_mode=ParseMode.HTML)))

    try: await inline_query.answer(results, cache_time=10, is_personal=True)
    except Exception as e: print(f"[INLINE] Error: {e}")

# ==========================================
# WELCOME CONTROLLERS (/start & /help)
# ==========================================
def build_help_text() -> str:
    return (
        "<b>「 𝘊𝘖𝘔𝘔𝘈𝘕𝘋𝘚 ぁ 」\n"
        "━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>➷ /profile\n〻 View your profile & stats\n\n"
        "➷ /deck\n〻 View your card deck\n\n"
        "➷ /cards\n〻 Browse anime database\n\n"
        "➷ /flex [Name]\n〻 Showcase your cards\n\n"
        "➷ /gift [Name] (reply to msg)\n〻 Gift a card to a user\n\n"
        "➷ /leaderboard\n〻 Global collector ranking\n\n"
        "➷ /special [Name]\n〻 Set featured card\n\n"
        "➷ /daily\n〻 Claim daily shard allowance\n\n"
        "➷ /weekly\n〻 Claim weekly shard injection\n\n"
        "➷ /roll\n〻 Play bowling for 10 tries!\n\n"
        "➷ /throw\n〻 Play basketball for 10 tries!\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "々 Cards randomly appear in chats\n"
        "々 Type <code>/seize</code> [name] before others to grab them!</b>\n"
        "━━━━━━━━━━━━━━━━━"
    )

@main_router.message(Command("help"))
async def help_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    db = load_db(); pic = db.get("settings", {}).get("help_pic")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="メ Close", callback_data="close_msg")]])
    if pic: await message.reply_photo(photo=pic, caption=build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)
    else: await message.reply(build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "show_help")
async def show_help_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    await cq.answer(); db = load_db(); pic = db.get("settings", {}).get("help_pic")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="メ Close", callback_data="close_msg")]])
    await cq.message.delete()
    if pic: await cq.message.answer_photo(photo=pic, caption=build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)
    else: await cq.message.answer(build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)

def build_start_text(user_id: int, first_name: str) -> str:
    mention = f'<a href="tg://user?id={user_id}">{first_name}</a>'
    return (
        f"<b>Hҽყ {mention} ✨\n\n"
        f"I Aɱ <a href='https://t.me/Animenx_bot'>「 ANIME NEXUS ぁ 」</a> 🍫</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"➜ 🍜 Cσʅʅҽƈƚ ԃιϝϝҽɾɳƚ Aɳιɱҽ ƈαɾԃʂ 🎴\n"
        f"➜ 🥂 Bυιʅԃ ყσυɾ υɳιϙυҽ Cαɾԃ Dҽƈƙ ✦\n"
        f"➜ ⛺ Cσɱρҽƚҽ ωιƚԋ ƈσʅʅҽƈƚσɾʂ ɠʅσႦαʅʅყ 🌍\n\n"
        f"╰➤ Tσ υʂҽ ɱҽ, <a href='https://t.me/Animenx_bot?startgroup=true'> αԃԃ ɱҽ ƚσ ყσυɾ ɠɾσυρ </a>."
    )

def build_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Aԃԃ Tσ Gɾσυρ", url="https://t.me/Animenx_bot?startgroup=true")],
        [InlineKeyboardButton(text="🌐 Mαιɳ Gɾσυρ", url="https://t.me/your_main_group"), InlineKeyboardButton(text="📖 Hҽʅρ", callback_data="show_help")]
    ])

@main_router.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    # Check for deep links
    if command.args and command.args.startswith("buy_"):
        lid = command.args.split("_", 1)[1]
        buyer_id = str(message.from_user.id)
        db = ensure_user(buyer_id, message.from_user.first_name, message.from_user.username)
        
        if lid not in db.get("offline_store", {}):
            await message.reply("❌ This listing does not exist or has already been sold.", parse_mode=ParseMode.HTML)
            return
            
        listing = db["offline_store"][lid]
        buyer_id = str(message.from_user.id)
        
        if listing["seller_id"] == buyer_id:
            await message.reply("❌ You cannot buy your own listing.", parse_mode=ParseMode.HTML)
            return

        card_id = listing["card_id"]
        global_card = db["global_cards"][card_id]
        seller_name = db["users"].get(listing["seller_id"], {}).get("name", "Unknown")
        price = listing["price"]

        caption = (
            f"<b>「 PURCHASE CONFIRMATION 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Card:</b> {global_card['name']}\n"
            f"🌟 <b>Rarity:</b> {format_rarity(global_card['rarity'])}\n"
            f"🏷️ <b>Seller:</b> {seller_name}\n"
            f"💰 <b>Price:</b> {price} Shards 💠\n\n"
            f"<i>Do you wish to proceed with this purchase?</i>"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Confirm Purchase", callback_data=f"buyoff_{buyer_id}_{lid}")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")]
        ])
        await message.reply_photo(photo=global_card["file_id"], caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    # Default Start Behavior
    db = load_db(); pic = db.get("settings", {}).get("start_pic")
    if pic: 
        await message.reply_photo(photo=pic, caption=build_start_text(message.from_user.id, message.from_user.first_name), reply_markup=build_start_keyboard(), parse_mode=ParseMode.HTML)
    else: 
        await message.reply(build_start_text(message.from_user.id, message.from_user.first_name), reply_markup=build_start_keyboard(), parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data == "show_start")
async def show_start_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): 
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    await cq.answer(); db = load_db(); pic = db.get("settings", {}).get("start_pic")
    await cq.message.delete()
    if pic: await cq.message.answer_photo(photo=pic, caption=build_start_text(cq.from_user.id, cq.from_user.first_name), reply_markup=build_start_keyboard(), parse_mode=ParseMode.HTML)
    else: await cq.message.answer(build_start_text(cq.from_user.id, cq.from_user.first_name), reply_markup=build_start_keyboard(), parse_mode=ParseMode.HTML)

# ==========================================
# SHARDS BALANCE (/shards)
# ==========================================
@main_router.message(Command("shards"))
async def shards_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    db = ensure_user(str(uid_int), message.from_user.first_name, message.from_user.username)
    shards = db["users"][str(uid_int)].get("nexus_shards", 0)
    
    await message.reply(
        f"<b>「 💠 NEXUS SHARDS ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 {message.from_user.first_name}\n"
        f"💰 Balance ➜ <b>{shards} Shards</b>", 
        parse_mode=ParseMode.HTML
    )