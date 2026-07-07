import math
import time
import uuid
import random
import asyncio
import difflib
from datetime import datetime, timezone, timedelta
from aiogram import F
from aiogram.types import (
    Message, CallbackQuery, InlineQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CopyTextButton,
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
    ensure_user, ensure_group, get_mention, is_ghost_banned, is_shadow_banned
)

# In-memory mining tracking dictionary to prevent spam farming
user_mine_cooldowns = {}

# Per-user cooldown dict for burn/gift to prevent rapid double-executions
_action_cooldowns: dict[str, float] = {}
ACTION_COOLDOWN_SECS = 8

# Gifting limit configuration
GIFT_COOLDOWN = 300           # 5-minute cooldown between gifts for regular users
DAILY_GIFT_SEND_LIMIT = 3     # Maximum cards a user can send per day
DAILY_GIFT_RECEIVE_LIMIT = 3  # Maximum cards a user can receive per day
_gift_cooldowns: dict[str, float] = {}


def _check_action_cooldown(uid: str) -> bool:
    """Returns True if user is on cooldown (should block), False if allowed."""
    now = time.time()
    last = _action_cooldowns.get(uid, 0)
    if now - last < ACTION_COOLDOWN_SECS:
        return True
    _action_cooldowns[uid] = now
    return False


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


async def has_bot_in_bio(user_id: int) -> bool:
    try:
        bot_info = await bot.get_me()
        bot_username = f"@{bot_info.username}".lower()
        user_chat = await bot.get_chat(user_id)
        if user_chat.bio:
            return bot_username in user_chat.bio.lower()
    except Exception:
        pass
    return False

# ==========================================
# ANTI-CHEAT REFERRAL CONVERSION ENGINE
# ==========================================
async def check_and_reward_referral(user_id: str, db: dict):
    user_data = db["users"].get(user_id)
    if not user_data: return

    referrer_id = user_data.get("referred_by")
    if not referrer_id or user_data.get("referral_rewarded", False):
        return

    total_cards = sum(c.get("amount", 0) for c in user_data.get("cards", {}).values())
    if total_cards < 1:
        return

    # Mark as rewarded immediately to prevent double-payout
    user_data["referral_rewarded"] = True
    ensure_user(referrer_id, "User")

    db["users"][referrer_id]["nexus_shards"] = db["users"][referrer_id].get("nexus_shards", 0) + 100
    db["users"][user_id]["nexus_shards"]     = db["users"][user_id].get("nexus_shards", 0) + 50

    referrals = db["users"][referrer_id].setdefault("referrals", [])
    if user_id not in referrals:
        referrals.append(user_id)

    ref_count     = len(referrals)
    milestone_msg = ""

    def _give_card(rarity_filter):
        locked_animes = db.get("settings", {}).get("locked_animes", [])
        locked_animes_lower = [a.lower().strip() for a in locked_animes]

        pool = {k: v for k, v in db["global_cards"].items() 
                if format_rarity(v["rarity"]) == rarity_filter
                and v["anime"].lower().strip() not in locked_animes_lower}
                
        if pool:
            cid, cdata = random.choice(list(pool.items()))
            db["users"][referrer_id].setdefault("cards", {}).setdefault(
                cid, {"name": cdata["name"], "rarity": cdata["rarity"], "amount": 0}
            )["amount"] += 1
            return cdata
        return None

    if ref_count == 5:
        db["users"][referrer_id]["nexus_shards"] += 200
        card = _give_card("Basic 🃏")
        if card:
            milestone_msg = f"\n🎉 <b>5 Referrals Milestone!</b>\n🎁 Earned: 1x Basic card (<b>{card['name']}</b>) &amp; <b>+200 Shards</b>!"
    elif ref_count == 10:
        db["users"][referrer_id]["nexus_shards"] += 500
        card = _give_card("Elite ⚓")
        if card:
            milestone_msg = f"\n🎉 <b>10 Referrals Milestone!</b>\n🎁 Earned: 1x Elite card (<b>{card['name']}</b>) &amp; <b>+500 Shards</b>!"
    elif ref_count == 20:
        db["users"][referrer_id]["nexus_shards"] += 1500
        card = _give_card("Divine ❄️")
        if card:
            milestone_msg = f"\n🎉 <b>20 Referrals Milestone!</b>\n🎁 Earned: 1x Divine card (<b>{card['name']}</b>) &amp; <b>+1,500 Shards</b>!"
    elif ref_count > 20 and (ref_count - 20) % 20 == 0:
        db["users"][referrer_id]["nexus_shards"] += 2000
        card = _give_card("Divine ❄️")
        if card:
            milestone_msg = f"\n🎉 <b>+{ref_count} Referrals Milestone Loop!</b>\n🎁 Earned: 1x Divine card (<b>{card['name']}</b>) &amp; <b>+2,000 Shards</b>!"

    save_db()

    try:
        referred_name    = db["users"][user_id].get("name", "User")
        referred_mention = get_mention(user_id, referred_name)
        referrer_alert = (
            f"<b>「 👥 REFERRAL CONVERTED! 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"👤 {referred_mention} seized their first card and became active!\n"
            f"🎁 Awarded: <b>+100 Shards</b>\n"
            f"📊 Successful Referrals: <b>{ref_count}</b>"
            f"{milestone_msg}"
        )
        await bot.send_message(chat_id=int(referrer_id), text=referrer_alert, parse_mode=ParseMode.HTML)
    except Exception:
        pass

    try:
        await bot.send_message(
            chat_id=int(user_id),
            text="<b>「 🎉 REFERRAL BONUS ACTIVATED! 」</b>\n━━━━━━━━━━━━━━━━━\n"
                 "You claimed your first card! Your referral link is now active.\n"
                 "🎁 Awarded: <b>+50 welcome Shards!</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


# ==========================================
# DAILY REWARDS CLAIM SYSTEM (/daily)
# ==========================================
@main_router.message(Command("daily"))
async def daily_reward_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    now_dt     = datetime.now(timezone.utc)
    today_date = now_dt.date()
    last_claim = db["users"][user_id].get("last_daily", 0)
    last_date  = datetime.fromtimestamp(last_claim, tz=timezone.utc).date() if last_claim else None

    if last_date == today_date:
        tomorrow_midnight = datetime.combine(today_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        rem  = int((tomorrow_midnight - now_dt).total_seconds())
        h, r = divmod(rem, 3600)
        m, _ = divmod(r, 60)
        await smart_reply(message, f"⏳ <b>Daily already claimed!</b>\nResets at midnight UTC — return in <b>{h}h {m}m</b>.", parse_mode=ParseMode.HTML)
        return

    bio_bonus    = await has_bot_in_bio(uid_int)
    base_reward  = 50
    bonus_reward = 100 if bio_bonus else 0
    total_reward = base_reward + bonus_reward

    db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + total_reward
    db["users"][user_id]["last_daily"]   = int(now_dt.timestamp())
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
    await smart_reply(message, msg, parse_mode=ParseMode.HTML)


# ==========================================
# WEEKLY REWARDS CLAIM SYSTEM (/weekly)
# ==========================================
@main_router.message(Command("weekly"))
async def weekly_reward_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)
    db      = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    now       = int(time.time())
    last_claim = db["users"][user_id].get("last_weekly", 0)
    cooldown  = 7 * 24 * 3600

    if now - last_claim < cooldown:
        rem  = cooldown - (now - last_claim)
        d, r = divmod(rem, 86400)
        h, _ = divmod(r, 3600)
        await smart_reply(message, f"⏳ <b>Weekly already claimed!</b>\nReturn in <b>{d}d {h}h</b> to claim again.", parse_mode=ParseMode.HTML)
        return

    valid_rarities = ["Basic 🃏", "Elite ⚓"]
    locked_animes = db.get("settings", {}).get("locked_animes", [])
    locked_animes_lower = [a.lower().strip() for a in locked_animes]

    tier_pool = {k: v for k, v in db.get("global_cards", {}).items() 
                 if format_rarity(v["rarity"]) in valid_rarities
                 and v["anime"].lower().strip() not in locked_animes_lower}

    if not tier_pool:
        await smart_reply(message, "⚠️ Weekly reward system is temporarily unavailable because no unlocked Basic or Elite cards are currently registered in the database.", parse_mode=ParseMode.HTML)
        return

    card_id, card_data = random.choice(list(tier_pool.items()))

    bio_bonus    = await has_bot_in_bio(uid_int)
    base_reward  = 150
    bonus_reward = 50 if bio_bonus else 0
    total_reward = base_reward + bonus_reward

    db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + total_reward
    db["users"][user_id]["last_weekly"]  = now

    user_cards = db["users"][user_id].setdefault("cards", {})
    if card_id not in user_cards:
        user_cards[card_id] = {"name": card_data["name"], "rarity": card_data["rarity"], "amount": 0}
    user_cards[card_id]["amount"] += 1
    db["users"][user_id]["total_claimed"] = db["users"][user_id].get("total_claimed", 0) + 1
    save_db()

    display_rarity = format_rarity(card_data["rarity"])
    msg = (
        "<b>「 💠 WEEKLY CLAIM REWARDS ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🎁 Base Shards   ➜ <b>+{base_reward} Shards</b>\n"
    )
    if bio_bonus:
        msg += f"✨ Bio Bonus     ➜ <b>+{bonus_reward} Shards</b> (Verified)\n"
    else:
        msg += "💡 <i>Tip: Put our bot username in your Bio for +50 Shards!</i>\n"
    msg += (
        f"━━━━━━━━━━━━━━━━━\n"
        f"🎴 <b>Weekly Shard &amp; Card Drop:</b>\n"
        f"👤 Character     ➜ <b>{card_data['name']}</b>\n"
        f"📺 Anime         ➜ <b>{card_data.get('anime', 'Unknown')}</b>\n"
        f"🌟 Rarity        ➜ <b>{display_rarity}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 Total Balance ➜ <b>{db['users'][user_id]['nexus_shards']} Shards 💠</b>"
    )
    try:
        await smart_reply_photo(message, photo=card_data["file_id"], caption=msg, parse_mode=ParseMode.HTML, has_spoiler=True)
    except Exception:
        await smart_reply(message, msg, parse_mode=ParseMode.HTML)


# ==========================================
# 10-ROLL BOWLING SYSTEM COMMAND (/roll)
# ==========================================
@main_router.message(Command("roll"))
async def bowling_roll_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id   = str(uid_int)
    db        = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    user_data = db["users"][user_id]
    now       = int(time.time())

    if user_data.get("roll_reset", 0) != 0 and now >= user_data.get("roll_reset", 0):
        user_data["roll_count"] = 0
        user_data["roll_reset"] = 0

    if user_data.get("roll_count", 0) >= 10:
        rem  = user_data["roll_reset"] - now
        h, r = divmod(rem, 3600)
        m, _ = divmod(r, 60)
        await smart_reply(message, 
            f"⏳ <b>Out of rolls!</b>\n━━━━━━━━━━━━━━━━━\n"
            f"Your pins are resetting.\nReturn in <b>{h}h {m}m</b>.",
            parse_mode=ParseMode.HTML
        )
        return

    if user_data.get("roll_count", 0) == 0:
        user_data["roll_reset"] = now + (8 * 3600)

    user_data["roll_count"] += 1
    rolls_left = 10 - user_data["roll_count"]

    dice_msg = await message.answer_dice(emoji="🎳")
    await asyncio.sleep(4)

    shards_won = 0
    if dice_msg.dice.value == 6:
        shards_won = random.randint(25, 100)
        user_data["nexus_shards"] = user_data.get("nexus_shards", 0) + shards_won

    save_db()

    if shards_won:
        await smart_reply(message, 
            f"<b>「 STRIKE! ぁ 」</b>\n━━━━━━━━━━━━━━━━━\n"
            f"🎉 You knocked down all the pins!\n"
            f"💠 Earned: <b>{shards_won} Shards</b>\n"
            f"🎳 Rolls left: <b>{rolls_left}/10</b>",
            parse_mode=ParseMode.HTML
        )
    else:
        await smart_reply(message, 
            f"<b>「 MISS ぁ 」</b>\n━━━━━━━━━━━━━━━━━\n"
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

    user_id   = str(uid_int)
    db        = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    user_data = db["users"][user_id]
    now       = int(time.time())

    if user_data.get("throw_reset", 0) != 0 and now >= user_data.get("throw_reset", 0):
        user_data["throw_count"] = 0
        user_data["throw_reset"] = 0

    if user_data.get("throw_count", 0) >= 10:
        rem  = user_data["throw_reset"] - now
        h, r = divmod(rem, 3600)
        m, _ = divmod(r, 60)
        await smart_reply(message, 
            f"⏳ <b>Out of stamina!</b>\n━━━━━━━━━━━━━━━━━\n"
            f"You need to rest your arms.\nReturn in <b>{h}h {m}m</b>.",
            parse_mode=ParseMode.HTML
        )
        return

    if user_data.get("throw_count", 0) == 0:
        user_data["throw_reset"] = now + (8 * 3600)

    user_data["throw_count"] += 1
    throws_left = 10 - user_data["throw_count"]

    dice_msg = await message.answer_dice(emoji="🏀")
    await asyncio.sleep(4)

    shards_won = 0
    if dice_msg.dice.value >= 4:
        shards_won = random.randint(15, 60)
        user_data["nexus_shards"] = user_data.get("nexus_shards", 0) + shards_won

    save_db()

    if shards_won:
        await smart_reply(message, 
            f"<b>「 SWISH! ぁ 」</b>\n━━━━━━━━━━━━━━━━━\n"
            f"🎉 Nothing but net!\n"
            f"💠 Earned: <b>{shards_won} Shards</b>\n"
            f"🏀 Throws left: <b>{throws_left}/10</b>",
            parse_mode=ParseMode.HTML
        )
    else:
        await smart_reply(message, 
            f"<b>「 MISS ぁ 」</b>\n━━━━━━━━━━━━━━━━━\n"
            f"You missed the shot. Keep practicing!\n"
            f"🏀 Throws left: <b>{throws_left}/10</b>",
            parse_mode=ParseMode.HTML
        )


# ==========================================
# SHARD GIFT COMMAND (/sgive)
# Min/Max boundaries and cooldowns apply equally to both users and administrators.
# ==========================================
SGIVE_MIN        = 10       # Minimum transferable amount
SGIVE_MAX_USER   = 10_000   # Per-transfer cap for all users
SGIVE_COOLDOWN   = 300      # 5-minute cooldown between gifts
_sgive_cooldowns: dict[str, float] = {}

@main_router.message(Command("sgive"))
async def sgive_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    sender_id   = str(uid_int)
    sender_name = message.from_user.first_name

    # ── Resolve target ────────────────────────────────────────────────────────
    if not (message.reply_to_message and message.reply_to_message.from_user):
        await smart_reply(message, 
            "<b>「 💠 SHARD GIFT 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "Reply to a user's message with:\n"
            "<code>/sgive &lt;amount&gt;</code>\n\n"
            f"• Minimum transfer: <b>{SGIVE_MIN:,} Shards</b>\n"
            f"• Maximum per transfer: <b>{SGIVE_MAX_USER:,} Shards</b>\n"
            f"• Cooldown: <b>5 minutes</b> between gifts",
            parse_mode=ParseMode.HTML
        )
        return

    target_user = message.reply_to_message.from_user
    target_id   = str(target_user.id)
    target_name = target_user.first_name

    # ── Self-gift guard ────────────────────────────────────────────────────────
    if target_id == sender_id:
        await smart_reply(message, "You can't gift shards to yourself!", parse_mode=ParseMode.HTML)
        return

    # ── Bot guard ────────────────━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if target_user.is_bot:
        await smart_reply(message, "You can't send shards to a bot.", parse_mode=ParseMode.HTML)
        return

    # ── Parse amount ──────────────────────────────────────────────────────────
    amount_str = (command.args or "").strip()
    if not amount_str or not amount_str.isdigit() or int(amount_str) <= 0:
        await smart_reply(message, 
            "⚠️ Provide a valid amount.\nExample: <code>/sgive 200</code>",
            parse_mode=ParseMode.HTML
        )
        return

    amount = int(amount_str)

    if amount < SGIVE_MIN:
        await smart_reply(message, 
            f"Minimum gift amount is <b>{SGIVE_MIN:,} Shards</b>.",
            parse_mode=ParseMode.HTML
        )
        return

    # Maximum limit checks are enforced on everyone
    if amount > SGIVE_MAX_USER:
        await smart_reply(message, 
            f"Maximum gift per transfer is <b>{SGIVE_MAX_USER:,} Shards</b>.",
            parse_mode=ParseMode.HTML
        )
        return

    # Cooldown checks are enforced on everyone
    now = time.time()
    last_give = _sgive_cooldowns.get(sender_id, 0)
    if now - last_give < SGIVE_COOLDOWN:
        rem  = int(SGIVE_COOLDOWN - (now - last_give))
        m, s = divmod(rem, 60)
        await smart_reply(message, 
            f"⏳ <b>Gift cooldown active!</b>\nYou can gift again in <b>{m}m {s}s</b>.",
            parse_mode=ParseMode.HTML
        )
        return

    # ── Load & ensure both users exist ────────────────────────────────────────
    db = ensure_user(sender_id, sender_name, message.from_user.username)
    ensure_user(target_id, target_name, target_user.username)

    sender_bal = db["users"][sender_id].get("nexus_shards", 0)

    # Balance validation is enforced on everyone
    if sender_bal < amount:
        await smart_reply(message, 
            f"<b>Insufficient Shards!</b>\n"
            f"You have <b>{sender_bal:,} 💠</b> but tried to send <b>{amount:,} 💠</b>.",
            parse_mode=ParseMode.HTML
        )
        return

    # ── Execute transfer ──────────────────────────────────────────────────────
    db["users"][sender_id]["nexus_shards"] = sender_bal - amount
    db["users"][target_id]["nexus_shards"] = db["users"][target_id].get("nexus_shards", 0) + amount
    save_db()

    # Update cooldown state
    _sgive_cooldowns[sender_id] = now

    target_mention = get_mention(target_id, target_name)

    # ── Public confirmation ───────────────────────────────────────────────────
    confirm_text = f"You gave <b>{amount:,} Shards 💠</b> to {target_mention}"
    await smart_reply(message, confirm_text, parse_mode=ParseMode.HTML)


# ==========================================
# /setspawn - MESSAGE THRESHOLD CONFIG
# ==========================================
@main_router.message(Command("setspawn"))
async def set_spawn_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if message.chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await smart_reply(message, "⚠️ This command can only be used in groups.")
        return

    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR] and message.from_user.id not in ADMIN_IDS:
        await smart_reply(message, "⚠️ Only group admins can use this command.")
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
                await smart_reply(message, "⚠️ Invalid ranges! Minimum is 100, maximum is 500, and min must be less than max.")
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
    await smart_reply(message, text, reply_markup=kb, parse_mode=ParseMode.HTML)


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
    if roll <= 80:   target_rarity = "Basic 🃏"
    elif roll <= 98: target_rarity = "Elite ⚓"
    else:            target_rarity = "Divine ❄️"

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
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• 🆔 <b>Card ID:</b> <code>{card_id}</code>\n"
                    f"• 👤 <b>Card:</b> <b>{card_data['name']}</b>\n"
                    f"• 🌟 <b>Rarity:</b> {display_rarity}\n"
                    f"• 🏘️ <b>Group:</b> {group_title} (<code>{chat_id}</code>)\n"
                    f"• 🕐 <b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as log_err:
            print(f"[SPAWN LOG] Failed: {log_err}")
    except Exception as e:
        print(f"[DROP] Error: {e}")


@main_router.message(Command("seize"))
async def seize_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    chat_id = message.chat.id
    cid_str = str(chat_id)

    if cid_str not in active_drops: return
    if not command.args:
        await smart_reply(message, "⚠️ Provide the character name!\nFormat: <code>/seize</code> [name]", parse_mode=ParseMode.HTML)
        return

    drop_data   = active_drops[cid_str]
    card_id     = drop_data["card_id"]
    drop_time   = drop_data["time"]

    db          = load_db()
    global_card = db["global_cards"].get(card_id)
    if not global_card: return

    target_name = global_card["name"].lower()
    query       = command.args.lower().strip()

    matched = False
    if len(query) < 3 and query != target_name:
        matched = False
    elif query in target_name:
        matched = True
    else:
        ratio = difflib.SequenceMatcher(None, query, target_name).ratio()
        if ratio > 0.70:
            matched = True

    if not matched:
        await smart_reply(message, "🚫「 𝗪𝗥𝗢𝗡𝗚 𝗚𝗨𝗘𝗦𝗦 ぁ 」\n\n➜ 𝗧𝗿𝘆 𝗔𝗴𝗮𝗶𝗻", parse_mode=ParseMode.HTML)
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
        "<b>「 🎊 CARD SEIZED ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f" 🎊 <b><i>{get_mention(user_id, name)}</i></b> seized the card in <b>{time_taken}s</b>!\n\n"
        f" 👤 Character ➜  <b>{global_card['name']} 《{display_rarity}》</b>\n"
        f" 📺 Anime   ➜ <b>{global_card['anime']}</b>\n"
        f" 💠 Economy ➜ Earned <b>{total_earned}</b> Nexus Shards{bonus_breakdown}!\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "➜ 📖 Use /deck to <b>view your collection</b>."
    )
    seize_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View Collection 🫧", switch_inline_query_current_chat=f"card_user.{user_id}")]
    ])
    try:
        await smart_reply(message, winner_text, parse_mode=ParseMode.HTML, reply_markup=seize_kb)
    except Exception:
        pass


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
        await smart_reply(message, "⚠️ <b>Usage:</b> <code>/special <card name></code>", parse_mode=ParseMode.HTML)
        return

    query    = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})

    if not my_cards:
        await smart_reply(message, "You don't own any cards yet!", parse_mode=ParseMode.HTML)
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
        await smart_reply(message, f"You do not own a card matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data    = db["global_cards"].get(matched_cid, {})
    display_rarity = format_rarity(matched_data["rarity"])

    caption = (
        f"<b>「 SET SPECIAL CARD ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⤿ Are you sure you want to set <b>{matched_data['name']}「 {display_rarity}」</b> this as your <b>Special Card?</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes, Set Special", callback_data=f"setsp_{user_id}_{matched_cid}")],
        [InlineKeyboardButton(text="Cancel", callback_data="cancel_action")]
    ])
    await smart_reply_photo(message, 
        photo=global_data.get("file_id"), caption=caption,
        reply_markup=kb, parse_mode=ParseMode.HTML, has_spoiler=True
    )


@main_router.callback_query(F.data.startswith("setsp_"))
async def confirm_special_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    parts   = cq.data.split("_", 2)
    owner_id = parts[1]
    card_id = parts[2]
    user_id = str(cq.from_user.id)

    if user_id != owner_id:
        await cq.answer("This menu is not for you!", show_alert=True)
        return

    db = load_db()
    if card_id not in db["users"].get(user_id, {}).get("cards", {}):
        await cq.answer("You don't own this card anymore!", show_alert=True)
        return

    db["users"][user_id]["special_card"] = card_id
    save_db()

    cdata          = db["users"][user_id]["cards"][card_id]
    display_rarity = format_rarity(cdata["rarity"])
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
# /gift (Spoiler + Confirmation with Daily Limits)
# ==========================================
@main_router.message(Command("gift"))
async def gift_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await smart_reply(message, "⚠️ Reply to a user's message to gift them a card.", parse_mode=ParseMode.HTML)
        return

    target_user = message.reply_to_message.from_user
    if target_user.is_bot:
        await smart_reply(message, "You cannot gift cards to bots.", parse_mode=ParseMode.HTML)
        return
    if str(target_user.id) == str(message.from_user.id):
        await smart_reply(message, "You cannot gift a card to yourself.", parse_mode=ParseMode.HTML)
        return
    if not command.args:
        await smart_reply(message, "⚠️ <b>Usage:</b> <code>/gift <card name></code>", parse_mode=ParseMode.HTML)
        return

    user_id   = str(message.from_user.id)
    target_id = str(target_user.id)

    # Cooldown check (non-admins)
    now = time.time()
    if uid_int not in ADMIN_IDS:
        last_gift = _gift_cooldowns.get(user_id, 0)
        if now - last_gift < GIFT_COOLDOWN:
            rem  = int(GIFT_COOLDOWN - (now - last_gift))
            m, s = divmod(rem, 60)
            await smart_reply(message, 
                f"⏳ <b>Gift cooldown active!</b>\nYou can gift another card in <b>{m}m {s}s</b>.",
                parse_mode=ParseMode.HTML
            )
            return

    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    db = ensure_user(target_id, target_user.first_name, target_user.username)

    today = config.get_shop_rotation_seed()

    # Dynamic Sender daily check schema normalization
    sender_gift_data = db["users"][user_id].setdefault("daily_gifts", {})
    if not isinstance(sender_gift_data, dict):
        db["users"][user_id]["daily_gifts"] = {"date": today, "sent": 0, "received": 0}
        sender_gift_data = db["users"][user_id]["daily_gifts"]
    else:
        if sender_gift_data.get("date") != today:
            sender_gift_data["date"] = today
            sender_gift_data["sent"] = 0
            sender_gift_data["received"] = 0
        else:
            if "sent" not in sender_gift_data: sender_gift_data["sent"] = 0
            if "received" not in sender_gift_data: sender_gift_data["received"] = 0

    if uid_int not in ADMIN_IDS and sender_gift_data["sent"] >= DAILY_GIFT_SEND_LIMIT:
        await smart_reply(message, 
            f"<b>Daily limit reached!</b>\nYou have already sent your limit of <b>{DAILY_GIFT_SEND_LIMIT}</b> gifts today.",
            parse_mode=ParseMode.HTML
        )
        return

    # Dynamic Receiver daily check schema normalization
    receiver_gift_data = db["users"][target_id].setdefault("daily_gifts", {})
    if not isinstance(receiver_gift_data, dict):
        db["users"][target_id]["daily_gifts"] = {"date": today, "sent": 0, "received": 0}
        receiver_gift_data = db["users"][target_id]["daily_gifts"]
    else:
        if receiver_gift_data.get("date") != today:
            receiver_gift_data["date"] = today
            receiver_gift_data["sent"] = 0
            receiver_gift_data["received"] = 0
        else:
            if "sent" not in receiver_gift_data: receiver_gift_data["sent"] = 0
            if "received" not in receiver_gift_data: receiver_gift_data["received"] = 0

    if int(target_id) not in ADMIN_IDS and receiver_gift_data["received"] >= DAILY_GIFT_RECEIVE_LIMIT:
        await smart_reply(message, 
            f"<b>Recipient limit reached!</b>\nThis user has already received their maximum of <b>{DAILY_GIFT_RECEIVE_LIMIT}</b> gifts today.",
            parse_mode=ParseMode.HTML
        )
        return

    query    = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})

    if not my_cards:
        await smart_reply(message, "You don't own any cards yet!", parse_mode=ParseMode.HTML)
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
        await smart_reply(message, f"You do not own a card matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data    = db["global_cards"].get(matched_cid, {})
    display_rarity = format_rarity(matched_data["rarity"])

    caption = (
        f"<b>「 GIFT CARD ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 Character ┊ <b>{matched_data['name']}</b>\n"
        f"🌟 Rarity    ┊ {display_rarity}\n\n"
        f"Are you sure you want to gift this to {get_mention(target_user.id, target_user.first_name)}?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Yes, Gift Card", callback_data=f"cfgift_{user_id}_{target_id}_{matched_cid}")],
        [InlineKeyboardButton(text="Cancel", callback_data="cancel_action")]
    ])
    await smart_reply_photo(message, 
        photo=global_data.get("file_id"), caption=caption,
        reply_markup=kb, parse_mode=ParseMode.HTML, has_spoiler=True
    )


@main_router.callback_query(F.data.startswith("cfgift_"))
async def confirm_gift_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    parts     = cq.data.split("_", 3)
    sender_id = parts[1]
    target_id = parts[2]
    card_id   = parts[3]
    user_id   = str(cq.from_user.id)

    if user_id != sender_id:
        await cq.answer("This menu is not for you!", show_alert=True)
        return

    # Double check cooldown before processing gift (non-admins)
    now = time.time()
    if uid_int not in ADMIN_IDS:
        last_gift = _gift_cooldowns.get(user_id, 0)
        if now - last_gift < GIFT_COOLDOWN:
            rem  = int(GIFT_COOLDOWN - (now - last_gift))
            m, s = divmod(rem, 60)
            await cq.answer(f"⏳ Cooldown active! Wait {m}m {s}s.", show_alert=True)
            return

    db = load_db()

    # Double-check daily counts on execution
    today = config.get_shop_rotation_seed()

    # Sender daily count check and robust normalization
    sender_gift_data = db["users"][user_id].setdefault("daily_gifts", {})
    if not isinstance(sender_gift_data, dict):
        db["users"][user_id]["daily_gifts"] = {"date": today, "sent": 0, "received": 0}
        sender_gift_data = db["users"][user_id]["daily_gifts"]
    else:
        if sender_gift_data.get("date") != today:
            sender_gift_data["date"] = today
            sender_gift_data["sent"] = 0
            sender_gift_data["received"] = 0
        else:
            if "sent" not in sender_gift_data: sender_gift_data["sent"] = 0
            if "received" not in sender_gift_data: sender_gift_data["received"] = 0

    if uid_int not in ADMIN_IDS and sender_gift_data["sent"] >= DAILY_GIFT_SEND_LIMIT:
        await cq.answer("Daily sending limit reached!", show_alert=True)
        return

    # Receiver daily count check and robust normalization
    receiver_gift_data = db["users"][target_id].setdefault("daily_gifts", {})
    if not isinstance(receiver_gift_data, dict):
        db["users"][target_id]["daily_gifts"] = {"date": today, "sent": 0, "received": 0}
        receiver_gift_data = db["users"][target_id]["daily_gifts"]
    else:
        if receiver_gift_data.get("date") != today:
            receiver_gift_data["date"] = today
            receiver_gift_data["sent"] = 0
            receiver_gift_data["received"] = 0
        else:
            if "sent" not in receiver_gift_data: receiver_gift_data["sent"] = 0
            if "received" not in receiver_gift_data: receiver_gift_data["received"] = 0

    if int(target_id) not in ADMIN_IDS and receiver_gift_data["received"] >= DAILY_GIFT_RECEIVE_LIMIT:
        await cq.answer("Recipient daily receipt limit reached!", show_alert=True)
        return

    my_cards = db["users"].get(user_id, {}).get("cards", {})

    if card_id not in my_cards or my_cards[card_id]["amount"] <= 0:
        await cq.answer("You don't own this card anymore!", show_alert=True)
        return

    if _check_action_cooldown(f"gift_{user_id}"):
        await cq.answer("⏳ Please wait a moment before gifting again.", show_alert=True)
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

    # Record limit parameters on successful execution (Cooldown is only for regular users)
    if uid_int not in ADMIN_IDS:
        _gift_cooldowns[user_id] = now
        
    # We now increment parameters for both admins and regular users to show accurate visual tracking
    sender_gift_data["sent"] += 1
    receiver_gift_data["received"] += 1

    await check_and_reward_referral(target_id, db)
    save_db()

    target_name    = db["users"][target_id].get("name", "User")
    display_rarity = format_rarity(card_data["rarity"])

    caption = (
        f"<b>「 CARD GIFTED 🎁 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"You successfully gifted <b>{card_data['name']}</b> [{display_rarity}] to {get_mention(target_id, target_name)}!\n\n"
        f"📊 Daily Gifts Sent: <b>{sender_gift_data['sent']}/{DAILY_GIFT_SEND_LIMIT}</b>"
    )
    await cq.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=None)
    await cq.answer("🎁 Gift sent successfully!")


# ==========================================
# /flex SHOWCASE COMMAND
# ==========================================
@main_router.message(Command("flex"))
async def flex_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)
    db      = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    if not command.args:
        await smart_reply(message, "⚠️ <b>Usage:</b> <code>/flex &lt;card name&gt;</code>", parse_mode=ParseMode.HTML)
        return

    query    = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})

    if not my_cards:
        await smart_reply(message, "You don't own any cards to flex!", parse_mode=ParseMode.HTML)
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
        await smart_reply(message, f"You do not own a card matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data    = db["global_cards"].get(matched_cid, {})
    display_rarity = format_rarity(matched_data["rarity"])

    safe_name = str(message.from_user.first_name).replace("<", "&lt;").replace(">", "&gt;")
    mention = f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    
    caption = (
        f"<i><b>Ooooh! Check out {mention}'s card!</b></i>\n\n"
        f"<b>⦿ <i>Character </i>» {matched_data['name']} ⟪ {global_data.get('anime', 'Unknown')} ⟫ \n"
        f"⦾ <i>Rarity </i>» {display_rarity}\n"
        f"⬤ <i>Owned</i>  » x{matched_data['amount']}</b>"
    )

    try:
        await smart_reply_photo(message, 
            photo=global_data.get("file_id"),
            caption=caption,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await smart_reply(message, caption, parse_mode=ParseMode.HTML)


# ==========================================
# GLOBAL CANCELLATION & CLOSE HANDLERS
# ==========================================
@main_router.callback_query(F.data == "cancel_action")
async def cancel_action_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    try:
        await cq.message.edit_caption(caption="Action cancelled.", reply_markup=None)
    except Exception:
        await cq.message.edit_text("Action cancelled.", reply_markup=None)
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
async def send_deck_page(message, db: dict, user_id: str, page=0, edit=False):
    user_data = db["users"][user_id]
    cards     = user_data.get("cards", {})
    items     = list(cards.items())
    user_name = user_data.get("name", "User")

    if not items:
        text = "<b>「 COLLECTION EMPTY ぁ 」</b>\n━━━━━━━━━━━━━━━━━\nYou haven't collected any cards yet!\nWait for a drop in the group."
        if edit and isinstance(message, CallbackQuery): await message.message.edit_text(text, parse_mode=ParseMode.HTML)
        else:
            target = message.message if isinstance(message, CallbackQuery) else message
            await smart_reply(target, text, parse_mode=ParseMode.HTML)
        return

    global_cards = db.get("global_cards", {})
    enriched = []
    for cid, cdata in items:
        anime = global_cards.get(cid, {}).get("anime", "Unknown")
        enriched.append((cid, cdata, anime))

    sort_pref = user_data.get("sort_pref", "default")
    if sort_pref == "rarity":   enriched.sort(key=lambda x: (x[2], RARITY_ORDER.get(format_rarity(x[1]["rarity"]), 99)))
    elif sort_pref == "name":   enriched.sort(key=lambda x: (x[2], x[1]["name"].lower()))
    elif sort_pref == "amount": enriched.sort(key=lambda x: (x[2], x[1]["amount"]), reverse=True)
    else:                       enriched.sort(key=lambda x: x[2])

    total_pages = max(1, math.ceil(len(enriched) / DECK_PER_PAGE))
    if page >= total_pages: page = total_pages - 1
    if page < 0:            page = 0

    start      = page * DECK_PER_PAGE
    end        = min(start + DECK_PER_PAGE, len(enriched))
    page_items = enriched[start:end]

    display_pic = None
    special_card_id = user_data.get("special_card")
    
    if special_card_id and special_card_id in cards:
        display_pic = global_cards.get(special_card_id, {}).get("file_id")
    elif enriched:
        display_pic = global_cards.get(enriched[0][0], {}).get("file_id")

    safe_name = str(user_name).replace("<", "&lt;").replace(">", "&gt;")
    text = f"『  ぁ 𝘾𝘼𝙍𝘿 𝘿𝙀𝘾𝙆  - {safe_name} 』\n━━━━━━━━━━━━━━━━━\n\n"

    current_anime = None
    for cid, cdata, anime in page_items:
        if anime != current_anime:
            if current_anime is not None: text += "\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n\n"
            text += f"𝗔𝗻𝗶𝗺𝗲  - <b>{anime} ↧</b>\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
            current_anime = anime
            
        disp_rarity = format_rarity(cdata["rarity"])
        
        if cid == special_card_id:
            text += f"✨ <b><i><code>{cdata['name']}</code></i> - [{disp_rarity}]  ×{cdata['amount']} </b>\n"
        else:
            text += f"✦ <b><i><code>{cdata['name']}</code></i> - [{disp_rarity}]  ×{cdata['amount']} </b>\n"

    if current_anime is not None: text += "\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"

    nav_buttons = []
    nav_buttons.append(
        InlineKeyboardButton(text="❮", callback_data=f"deck_prev_{user_id}_{page-1}") if page > 0
        else InlineKeyboardButton(text="❮", callback_data="noop")
    )
    nav_buttons.append(
        InlineKeyboardButton(text="❯", callback_data=f"deck_next_{user_id}_{page+1}") if end < len(enriched)
        else InlineKeyboardButton(text="❯", callback_data="noop")
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⌈ 𝗣𝗮𝗴𝗲 {page+1}/{total_pages} ⌋", callback_data=f"page_alert_{page+1}")],
        nav_buttons,
        [InlineKeyboardButton(text="View Collection 🫧", switch_inline_query_current_chat=f"card_user.{user_id}")]
    ])

    if display_pic:
        if edit and isinstance(message, CallbackQuery):
            try: await message.message.edit_media(InputMediaPhoto(media=display_pic, caption=text, parse_mode=ParseMode.HTML), reply_markup=keyboard)
            except Exception: pass
        else:
            target = message.message if isinstance(message, CallbackQuery) else message
            await smart_reply_photo(target, photo=display_pic, caption=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        if edit and isinstance(message, CallbackQuery):
            await message.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            target = message.message if isinstance(message, CallbackQuery) else message
            await smart_reply(target, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@main_router.message(Command("deck"))
async def view_deck_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    try:
        member = await bot.get_chat_member(config.MAIN_GROUP_USERNAME, message.from_user.id)
        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
            raise Exception("Not member")
    except Exception as e:
        print(f"[deck_access] get_chat_member failed for {message.from_user.id}: {e}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✦ Join Group", url=config.MAIN_GROUP_LINK)],
            [InlineKeyboardButton(text="↻ Try Again", callback_data="check_deck_access")]
        ])
        await smart_reply(message, 
            "⚠️「 𝗔𝗖𝗖𝗘𝗦𝗦 𝗗𝗘𝗡𝗜𝗘𝗗 ぁ 」\n\n"
            "🧿 𝗧𝗼 𝘃𝗶𝗲𝘄 𝘆𝗼𝘂𝗿 𝗱𝗲𝗰𝗸, "
            "𝘆𝗼𝘂 𝗺𝘂𝘀𝘁 𝗷𝗼𝗶𝗻 𝗼𝘂𝗿 𝗠𝗮𝗶𝗻 𝗚𝗿𝗼𝘂𝗽.",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
        return

    user_id = str(message.from_user.id)
    db      = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    await send_deck_page(message, db, user_id, page=0, edit=False)


@main_router.callback_query(F.data == "check_deck_access")
async def check_deck_access_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    try:
        member = await bot.get_chat_member(config.MAIN_GROUP_USERNAME, cq.from_user.id)
        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
            await cq.answer("You haven't joined the group yet!", show_alert=True)
            return
    except Exception as e:
        print(f"[deck_access] get_chat_member failed for {cq.from_user.id}: {e}")
        await cq.answer("You haven't joined the group yet!", show_alert=True)
        return

    await cq.message.delete()
    user_id = str(cq.from_user.id)
    db = ensure_user(user_id, cq.from_user.first_name, cq.from_user.username)
    await send_deck_page(cq, db, user_id, page=0, edit=False)
    await cq.answer("✅ Access Granted!")


@main_router.callback_query(F.data.startswith("deck_"))
async def deck_nav_cb(callback_query: CallbackQuery):
    uid_int = callback_query.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await callback_query.answer("🔇 You are currently restricted.", show_alert=True)
        return

    parts                = callback_query.data.split("_")
    direction, owner_id, page_str = parts[1], parts[2], parts[3]
    if str(callback_query.from_user.id) != owner_id:
        await callback_query.answer("Not your deck!", show_alert=True)
        return
    db = load_db()
    await send_deck_page(callback_query, db, owner_id, int(page_str), edit=True)
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
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id      = str(message.from_user.id)
    db           = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
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
            InlineKeyboardButton(text="🔤 Name",   callback_data=f"setsort_{user_id}_name")
        ],
        [
            InlineKeyboardButton(text="📦 Amount",  callback_data=f"setsort_{user_id}_amount"),
            InlineKeyboardButton(text="🔄 Default", callback_data=f"setsort_{user_id}_default")
        ]
    ])
    await smart_reply(message, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


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
    if is_ghost_banned(uid_int): return

    user_id  = str(message.from_user.id)
    name     = message.from_user.first_name
    username = message.from_user.username
    db       = ensure_user(user_id, name, username)
    user_data = db["users"][user_id]
    cards     = user_data.get("cards", {})

    unique_cards = len(cards)
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
    if int(user_id) in config.shadow_banned and config.shadow_banned[int(user_id)] > now:
        rem    = int(config.shadow_banned[int(user_id)] - now)
        m, s   = divmod(rem, 60)
        ban_status = f"Restricted 🔇 ({m}m {s}s remaining)"
    else:
        ban_status = "None 🟢"

    safe_name = str(name).replace("<", "&lt;").replace(">", "&gt;")
    name_link = f'<a href="tg://user?id={user_id}">{safe_name}</a>'

    profile_text = (
        "「 𝙉𝙀𝙓𝙐𝙎 : 𝙋𝙍𝙊𝙁𝙄𝙇𝙀 ぁ 」\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"❖ 𝙉𝙖𝙢𝙚          ➜ {name_link}\n"
        f"❖ 𝙐𝙨𝙚𝙧𝙣𝙖𝙢𝙚     ➜ {uname_display}\n"
        f"❖ 𝙐𝙨𝙚𝙧 𝙄𝘿       ➜ <code>{user_id}</code>\n"
        f"❖ 𝙔𝙚𝙖𝙧 𝙅ο𝙞𝙣𝙚𝙙   ➜ {joined_year}\n\n"
        f"❖ 𝙏ο𝙩𝙖𝙡 𝘾𝙖𝙧𝙙𝙨   ➜ {unique_cards}\n"
        f"❖ 𝙍𝙖𝙣𝙠          ➜ #{rank}\n"
        f"❖ 𝙉𝙚𝙭𝙪𝙨 𝙎𝙝𝙖𝙧𝙙𝙨  ➜ <b>{shards} 💠</b>\n"
        f"❖ 𝙎𝙝𝙖𝙙ο𝙬 𝘽𝙖𝙣   ➜ {ban_status}\n\n"
        "━━━━━━━━━━━━━━━━━"
    )

    keyboard  = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Close", callback_data="close_msg")]])
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
@main_router.message(Command("leaderboard", "top"))
async def leaderboard(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    db      = load_db()
    top     = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)
    user_id = str(message.from_user.id)

    user_rank = 0
    for i, (uid, ud) in enumerate(top):
        if uid == user_id:
            user_rank = i + 1
            break

    rank_text = f"#{user_rank}" if user_rank > 0 else "Unranked"
    symbols   = ["✦", "✧", "❖"] + ["◈"] * 7

    text = (
        "<b>「  𝘓𝘌𝘈𝘋𝘌𝘙𝘉𝘖𝘈𝘙𝘋 ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "〄 <b>𝙏ο𝙥 𝘾ο𝙡𝙡𝙚𝙘𝙩ο𝙧𝙨</b>\n\n"
    )
    for i, (uid, ud) in enumerate(top[:10]):
        sym       = symbols[i % 10]
        safe_name = str(ud.get("name", "Unknown")).replace("<", "&lt;").replace(">", "&gt;")
        text += f"{sym} <b>{safe_name}</b> ┊ 🎴 {len(ud.get('cards', {}))}\n"

    text += "\n━━━━━━━━━━━━━━━━━"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❖ Your Rank - {rank_text}", callback_data="noop")],
        [InlineKeyboardButton(text="✕ Close", callback_data="close_msg")]
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

    db           = ensure_user(str(inline_query.from_user.id), inline_query.from_user.first_name, inline_query.from_user.username)
    cards        = db["users"].get(target_user_id, {}).get("cards", {})
    global_cards = db.get("global_cards", {})
    results      = []

    items     = list(cards.items())
    sort_pref = db["users"].get(target_user_id, {}).get("sort_pref", "default")
    if sort_pref == "rarity":   items.sort(key=lambda x: RARITY_ORDER.get(format_rarity(x[1]["rarity"]), 99))
    elif sort_pref == "amount": items.sort(key=lambda x: x[1]["amount"], reverse=True)
    else:                       items.sort(key=lambda x: x[1]["name"].lower())

    for cid, cdata in items[:50]:
        if query and query not in cdata["name"].lower() and query not in cdata["rarity"].lower(): continue
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
        results.append(InlineQueryResultArticle(
            id="empty", title="No cards found",
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
# WELCOME CONTROLLERS (/start & /help)
# ==========================================
def build_help_text() -> str:
    return (
        "<b>「 𝘊𝘖𝘔𝘔𝘈𝘕𝘋𝘚 ぁ 」\n"
        "━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>➷ /profile\n〻 View your profile &amp; stats\n\n"
        "➷ /deck\n〻 View your card deck\n\n"
        "➷ /flex [Name]\n〻 Showcase your cards\n\n"
        "➷ /gift [Name] (reply to msg)\n〻 Gift a card to a user\n\n"
        "➷ /leaderboard\n〻 Global collector ranking\n\n"
        "➷ /special [Name]\n〻 Set featured card\n\n"
        "➷ /daily\n〻 Claim daily shard allowance\n\n"
        "➷ /weekly\n〻 Claim weekly shards &amp; a Basic or Elite card!\n\n"
        "➷ /roll\n〻 Play bowling for 10 tries!\n\n"
        "➷ /throw\n〻 Play basketball for 10 tries!\n\n"
        "➷ /burn [Name]\n〻 Burn a card for quick Shards!\n\n"
        "➷ /referral\n〻 View your referral status and link!\n\n"
        "➷ /redeem [Code]\n〻 Redeem active promotional codes!\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "々 Cards randomly appear in chats\n"
        "々 Type <code>/seize</code> [name] before others to grab them!</b>\n"
        "━━━━━━━━━━━━━━━━━"
    )


@main_router.message(Command("help"))
async def help_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    db  = load_db()
    pic = db.get("settings", {}).get("help_pic")
    kb  = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="メ Close", callback_data="close_msg")]])
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
    kb  = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="メ Close", callback_data="close_msg")]])
    await cq.message.delete()
    if pic: await cq.message.answer_photo(photo=pic, caption=build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)
    else:   await cq.message.answer(build_help_text(), reply_markup=kb, parse_mode=ParseMode.HTML)


def build_start_text(user_id: int, first_name: str) -> str:
    safe_name = str(first_name).replace("<", "&lt;").replace(">", "&gt;")
    mention   = f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    return (
        f"<b>Hҽყ {mention} ✨\n\n"
        f"I Aɱ <a href='https://t.me/Animenx_bot'>「 ANIME NEXUS ぁ 」</a> 🍫</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"➜ 🍜 Cσʅʅҽƈƚ   ԃιϝϝҽɾɳƚ Aɳιɱҽ ƈαɾԃʂ 🎴\n"
        f"➜ 🥂 Bυιʅԃ   ყσυɾ υɳιϙυҽ Cαɾԃ Dҽƈƙ ✦\n"
        f"➜ ⛺ Cσɱρҽƚҽ ωιƚԋ ƈσʅʅҽƈƚσɾʂ ɠʅσႦαʅʅყ 🌍\n\n"
        f"╰➤ Tσ υʂҽ ɱҽ, <a href='https://t.me/Animenx_bot?startgroup=true'> αԃԃ   ɱҽ ƚσ   ყσυɾ ɠɾσυρ </a>."
    )


def build_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Aԃԃ Tσ Gɾσυρ", url="https://t.me/Animenx_bot?startgroup=true")],
        [InlineKeyboardButton(text="🌐 Mαιɳ Gɾσυρ", url=config.MAIN_GROUP_LINK),
         InlineKeyboardButton(text="📖 Hҽʅρ", callback_data="show_help")]
    ])


@main_router.message(Command("start"))
async def start_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

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
                            "━━━━━━━━━━━━━━━━━\n"
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

        seller_name = db["users"].get(listing["seller_id"], {}).get("name", "Unknown")
        price       = listing["price"]

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
            [InlineKeyboardButton(text="Cancel", callback_data="cancel_action")]
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
# SHARDS BALANCE (/shards)
# ==========================================
@main_router.message(Command("shards"))
async def shards_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    db     = ensure_user(str(uid_int), message.from_user.first_name, message.from_user.username)
    shards = db["users"][str(uid_int)].get("nexus_shards", 0)
    await smart_reply(message, 
        f"<b>「 💠 NEXUS SHARDS ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<b>Your Current Shards ⦂ {shards} </b>💠 ",
        parse_mode=ParseMode.HTML
    )


# ==========================================
# CARD BURNING RECYCLING SYSTEM (/burn)
# ==========================================
@main_router.message(Command("burn"))
async def burn_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    if not command.args:
        await smart_reply(message, "⚠️ <b>Usage:</b> <code>/burn &lt;card name&gt;</code>\nExample: <code>/burn naruto</code>", parse_mode=ParseMode.HTML)
        return

    query    = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})

    if not my_cards:
        await smart_reply(message, "You do not own any cards to burn.", parse_mode=ParseMode.HTML)
        return

    best_match = None
    best_ratio = 0.0

    for cid, cdata in my_cards.items():
        if cdata["amount"] <= 0: continue
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
        await smart_reply(message, f"You do not own any cards matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data       = db["global_cards"].get(matched_cid, {})
    rarity_normalized = format_rarity(matched_data["rarity"])

    burn_payout = 150
    if rarity_normalized == "Elite ⚓":   burn_payout = 450
    elif rarity_normalized == "Divine ❄️": burn_payout = 1800

    caption = (
        f"<b>「 🔥 BURN CONFIRMATION 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <b>WARNING:</b> This card will be permanently destroyed!\n\n"
        f"👤 Character ➜ <b>{matched_data['name']}</b>\n"
        f"🌟 Rarity    ➜ <b>{rarity_normalized}</b>\n"
        f"💠 Returns   ➜ <b>+{burn_payout} Shards</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<i>Are you sure you want to proceed? This action is irreversible.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Confirm Destruction", callback_data=f"cfburn_{user_id}_{matched_cid}")],
        [InlineKeyboardButton(text="Cancel", callback_data="cancel_action")]
    ])
    await smart_reply_photo(message, photo=global_data.get("file_id"), caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data.startswith("cfburn_"))
async def confirm_burn_cb(cq: CallbackQuery):
    parts = cq.data.split("_", 2)
    uid   = parts[1]
    card_id = parts[2]

    if str(cq.from_user.id) != uid:
        await cq.answer("This menu is not for you!", show_alert=True)
        return

    if _check_action_cooldown(f"burn_{uid}"):
        await cq.answer("⏳ Please wait a moment before burning again.", show_alert=True)
        return

    db       = load_db()
    my_cards = db["users"].get(uid, {}).get("cards", {})

    if card_id not in my_cards or my_cards[card_id]["amount"] <= 0:
        await cq.answer("You don't own this card anymore!", show_alert=True)
        return

    card_data         = my_cards[card_id]
    rarity_normalized = format_rarity(card_data["rarity"])

    burn_payout = 150
    if rarity_normalized == "Elite ⚓":   burn_payout = 450
    elif rarity_normalized == "Divine ❄️": burn_payout = 1800

    my_cards[card_id]["amount"] -= 1
    if my_cards[card_id]["amount"] <= 0:
        del my_cards[card_id]
        if db["users"][uid].get("special_card") == card_id:
            db["users"][uid]["special_card"] = None

    db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + burn_payout
    save_db()

    caption = (
        f"<b>「 🔥 CARD INCINERATED 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Card: <b>{card_data['name']}</b> [{rarity_normalized}]\n"
        f"Action: Destroyed and recycled.\n\n"
        f"💰 Earned: <b>+{burn_payout} Nexus Shards</b> 💠"
    )
    await cq.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=None)
    await cq.answer("🔥 Card burned successfully!")


# ==========================================
# REFERRAL OVERVIEW MENU (/referral)
# ==========================================
@main_router.message(Command("referral", "refer"))
async def referral_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id  = str(uid_int)
    db       = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    bot_info = await bot.get_me()

    ref_link       = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    referred_users = db["users"][user_id].get("referrals", [])
    ref_count      = len(referred_users)

    if ref_count < 5:
        next_milestone = "<b><i>5</i></b> (Reward: 1x Basic Card 🃏 &amp; 200 Shards)"
        progress       = f"<b><i>{ref_count}/5</i></b>"
    elif ref_count < 10:
        next_milestone = "<b><i>10</i></b> (Reward: 1x Elite Card ⚓ &amp; 500 Shards)"
        progress       = f"<b><i>{ref_count}/10</i></b>"
    elif ref_count < 20:
        next_milestone = "<b><i>20</i></b> (Reward: 1x Divine Card ❄️ &amp; 1500 Shards)"
        progress       = f"<b><i>{ref_count}/20</i></b>"
    else:
        target_loop    = 20 + (((ref_count - 20) // 20) + 1) * 20
        next_milestone = f"<b><i>{target_loop}</i></b> (Reward: 1x Divine Card ❄️ &amp; 2000 Shards)"
        progress       = f"<b><i>{ref_count}/{target_loop}</i></b>"

    msg = (
        f"<b>「 👥 REFERRAL PROGRAM ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ <b><i>Verification Rule:</i></b> Invited users must seize <b>at least 1 card</b> to validate and trigger payouts.\n\n"
        f"🔗 <b><i>Your Unique Invite Link:</i></b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"📊 <b><i>Your Referral Stats:</i></b>\n"
        f"  ├ Successful Invites: <b>{ref_count}</b>\n"
        f"  ├ Next Milestone: {next_milestone}\n"
        f"  └ Progress: {progress}\n\n"
        f"<blockquote expandable> 🏆 <b><i>Reward Milestone Rules:</i></b>\n"
        f"◍ Per Successful Invite: <b><i>+100 Shards</i></b> (Invited gets <b><i>+50</i></b>)\n"
        f"◍ Reach 5 Invites: <b><i>Basic Card 🃏 + 200 💠</i></b>\n"
        f"◍ Reach 10 Invites: <b><i>Elite Card ⚓ + 500 💠</i></b>\n"
        f"◍ Reach 20 Invites: <b><i>Divine Card ❄️ + 1,500 💠</i></b>\n"
        f"◍ Every 20 Invites after: <b><i>Divine Card ❄️ + 2,000 💠</i></b></blockquote>\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    
    # "Copy Link" launches Telegram's share portal allowing mobile users to copy to clipboard in 1 tap
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Share Link",
                    url=f"https://t.me/share/url?url={ref_link}&text=Join%20the%20Anime%20Nexus%20card%20collection%20adventure!"
                ),
                InlineKeyboardButton(
                    text=" Copy Link",
                    copy_text=CopyTextButton(text=ref_link)
                )
            ],
            [
                InlineKeyboardButton(
                    text="✕ Close",
                    callback_data="close_msg"
                )
            ]
        ]
    )

    await smart_reply(message, 
        msg,
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

# ==========================================
# PROMOTIONAL CODES ENGINE (/redeem)
# ==========================================
@main_router.message(Command("redeem"))
async def redeem_promo_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if not command.args:
        await smart_reply(message, "⚠️ <b>Usage:</b> <code>/redeem &lt;CODE&gt;</code>\nExample: <code>/redeem SUMMERSHARDS</code>", parse_mode=ParseMode.HTML)
        return

    code = command.args.upper().strip()
    db   = load_db()
    promos = db.setdefault("promos", {})

    if code not in promos:
        await smart_reply(message, "Invalid, expired, or incorrect promo code.", parse_mode=ParseMode.HTML)
        return

    promo   = promos[code]
    user_id = str(uid_int)

    if user_id in promo.setdefault("claimed_by", []):
        await smart_reply(message, "You have already claimed this promo code!", parse_mode=ParseMode.HTML)
        return

    if len(promo["claimed_by"]) >= promo["max_claims"]:
        await smart_reply(message, "This promo code has reached its maximum claim limit and is expired.", parse_mode=ParseMode.HTML)
        return

    ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    rewards_to_process = []
    if "rewards" in promo:
        rewards_to_process = promo["rewards"]
    else:
        legacy_type = promo.get("type", "shards")
        if legacy_type == "shards":
            rewards_to_process = [{"type": "shards", "shards": promo.get("shards", 0)}]
        elif legacy_type == "card":
            rewards_to_process = [{"type": "card", "rarity": promo.get("rarity", "Basic 🃏"), "amount": promo.get("amount", 1)}]

    shards_awarded = 0
    cards_awarded  = []

    locked_animes = db.get("settings", {}).get("locked_animes", [])
    locked_animes_lower = [a.lower().strip() for a in locked_animes]

    for reward in rewards_to_process:
        if reward["type"] == "shards":
            shards_awarded += reward["shards"]
            db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + reward["shards"]

        elif reward["type"] == "card":
            target_rarity = format_rarity(reward["rarity"])
            card_pool     = {k: v for k, v in db.get("global_cards", {}).items()
                             if format_rarity(v["rarity"]) == target_rarity
                             and v["anime"].lower().strip() not in locked_animes_lower}

            if card_pool:
                quantity = reward.get("amount", 1)
                card_id, card_data = random.choice(list(card_pool.items()))

                user_cards = db["users"][user_id].setdefault("cards", {})
                if card_id not in user_cards:
                    user_cards[card_id] = {"name": card_data["name"], "rarity": card_data["rarity"], "amount": 0}
                user_cards[card_id]["amount"] += quantity
                db["users"][user_id]["total_claimed"] = db["users"][user_id].get("total_claimed", 0) + quantity
                cards_awarded.append((card_data, quantity))

    promo["claimed_by"].append(user_id)
    await check_and_reward_referral(user_id, db)
    save_db()

    msg_lines = [
        f"<b>「 🎁 PROMO CODE REDEEMED 」</b>",
        f"━━━━━━━━━━━━━━━━━",
        f"🎫 Code: <code>{code}</code>\n",
        f"📦 <b>Acquired Rewards:</b>"
    ]
    if shards_awarded > 0:
        msg_lines.append(f" • 💠 <b>Nexus Shards:</b> +{shards_awarded}")
    for cdata, qty in cards_awarded:
        disp_rarity = format_rarity(cdata["rarity"])
        msg_lines.append(f" • 🎴 <b>{cdata['name']}</b> ({disp_rarity}) x{qty}")
    msg_lines.append("\n━━━━━━━━━━━━━━━━━")
    caption = "\n".join(msg_lines)

    if cards_awarded:
        first_card_data = cards_awarded[0][0]
        try:
            await smart_reply_photo(message, photo=first_card_data["file_id"], caption=caption, parse_mode=ParseMode.HTML, has_spoiler=True)
        except Exception:
            await smart_reply(message, caption, parse_mode=ParseMode.HTML)
    else:
        await smart_reply(message, caption, parse_mode=ParseMode.HTML)


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
        await smart_reply(message, "⚠️ <b>Usage:</b> <code>/search &lt;card name&gt;</code>\nExample: <code>/search Makima</code>", parse_mode=ParseMode.HTML)
        return

    user_id = str(uid_int)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    if not db["users"].get(user_id, {}).get("cards"):
        await smart_reply(message, "You don't own any cards yet. Collect some first!", parse_mode=ParseMode.HTML)
        return

    card_id = _find_owned_card(db, user_id, command.args)
    if not card_id:
        await smart_reply(message, f"You don't own any card matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    global_data = db["global_cards"][card_id]
    caption     = _build_card_lookup_caption(global_data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌐 𝗪𝗵𝗼 𝗼𝘄𝗻? ({WHOOWNS_COST} 💠)", callback_data=f"whoowns_{user_id}_{card_id}")]
    ])

    try:
        await smart_reply_photo(message, 
            photo=global_data.get("file_id"),
            caption=caption,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
            has_spoiler=True
        )
    except Exception:
        await smart_reply(message, caption, reply_markup=kb, parse_mode=ParseMode.HTML)


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

    db["users"][searcher_id]["nexus_shards"] = balance - WHOOWNS_COST
    save_db()

    owner_lines = "\n".join(f"{name} ({uid}) - {amount}" for uid, name, amount in owners)
    caption = _build_card_lookup_caption(global_card) + "\n\n" + owner_lines

    await cq.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=None)
    await cq.answer(f"{WHOOWNS_COST} 💠 Shards deducted.")
