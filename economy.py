"""
economy.py — Currency & trading economy for the card bot.

Everything about earning, sending, and trading Nexus Shards and cards
between users: /daily, /weekly, /roll, /throw, /sgive, /gift (+
confirm_gift_cb), /trade (+ accept_trade_cb, decline_trade_cb), /shards,
/referral, /redeem.

Split out of handlers.py to keep the economy surface area separate from
the rest of the bot (drops, profile/browsing, admin support tooling, etc).
"""
import time
import uuid
import random
import asyncio
import difflib
from datetime import datetime, timezone, timedelta
from aiogram import F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CopyTextButton,
)
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

import config
from config import (
    bot, main_router, ADMIN_IDS, format_rarity, load_db, save_db,
    ensure_user, get_mention, is_ghost_banned, is_shadow_banned,
    format_wait_mmss,
)
from vlog import log_action

# Per-user cooldown dict for gift/trade to prevent rapid double-executions
_action_cooldowns: dict[str, float] = {}
ACTION_COOLDOWN_SECS = 8

# # Gifting limit configuration
GIFT_COOLDOWN = 300           # 5-minute cooldown between gifts for regular users
DAILY_GIFT_SEND_LIMIT = 3     # Maximum cards a user can send per day
DAILY_GIFT_RECEIVE_LIMIT = 3  # Maximum cards a user can receive per day
_gift_cooldowns: dict[str, float] = {}

# Shards transfer cooldown tracking
_sgive_cooldowns: dict[str, float] = {}
SGIVE_COOLDOWN_SECS = 300   # seconds between transfers for regular users (5 min)
SGIVE_MIN_AMOUNT    = 10    # minimum shards per transfer
SGIVE_MAX_AMOUNT    = 15000 # maximum shards per transfer

# ==========================================
# /trade STATE & CONFIGURATION
# ==========================================
# In-memory pending trade offers: trade_id -> offer details.
# Not persisted to disk — a restart simply drops any offers still in flight,
# which is fine since nothing has moved between inventories yet at that point.
active_trades: dict[str, dict] = {}
TRADE_EXPIRY_SECS  = 300   # Unanswered offers auto-expire after 5 minutes
TRADE_COOLDOWN_SECS = 60   # 1 minute cooldown between trade offers (per initiator)
_trade_cooldowns: dict[str, float] = {}

# Basic 🃏 <-> Basic/Elite, Elite ⚓ <-> Basic/Elite, Divine ❄️ <-> Divine only
def _trade_rarities_compatible(rarity_a: str, rarity_b: str) -> bool:
    if rarity_a == "Divine ❄️" or rarity_b == "Divine ❄️":
        return rarity_a == "Divine ❄️" and rarity_b == "Divine ❄️"
    return True


def _check_action_cooldown(uid: str) -> bool:
    """Returns True if user is on cooldown (should block), False if allowed."""
    now = time.time()
    last = _action_cooldowns.get(uid, 0)
    if now - last < ACTION_COOLDOWN_SECS:
        return True
    _action_cooldowns[uid] = now
    return False


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
            f"━━━━━━━━━━━━━━━━━━━━\n"
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
            text="<b>「 🎉 REFERRAL BONUS ACTIVATED! 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                 "You claimed your first card! Your referral link is now active.\n"
                 "🎁 Awarded: <b>+50 welcome Shards!</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
-e 

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
        await message.reply(f"⏳ <b>Daily already claimed!</b>\nResets at midnight UTC — return in <b>{h}h {m}m</b>.", parse_mode=ParseMode.HTML)
        return

    bio_bonus    = await has_bot_in_bio(uid_int)
    base_reward  = 150
    bonus_reward = 150 if bio_bonus else 0
    total_reward = base_reward + bonus_reward

    db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + total_reward
    db["users"][user_id]["last_daily"]   = int(now_dt.timestamp())
    save_db()

    msg = (
        "<b>「 💠 DAILY SHARDS CLAIMED ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"٠࣪⭑ Daily Reward  <b>+{base_reward} Shards</b>\n"
    )
    if bio_bonus:
        msg += f"⟡ ݁₊ . Bio Bonus  <b>+{bonus_reward} Shards</b> (Bot username verified!)\n"
    else:
        msg += "💡 <i>Tip: Put our bot username in your profile Bio for an extra +100 Shards daily!</i>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n── Total Claimed <b>+{total_reward} Shards 💠</b>"
    await message.reply(msg, parse_mode=ParseMode.HTML)


# ==========================================
# WEEKLY REWARDS CLAIM SYSTEM (/weekly)
# ==========================================
_weekly_locks: dict[str, asyncio.Lock] = {}

@main_router.message(Command("weekly"))
async def weekly_reward_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)

    # Serialize per-user so rapid-fire /weekly spam can't slip multiple
    # claims through before the cooldown timestamp is saved.
    lock = _weekly_locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

        now       = int(time.time())
        last_claim = db["users"][user_id].get("last_weekly", 0)
        cooldown  = 7 * 24 * 3600

        if now - last_claim < cooldown:
            rem  = cooldown - (now - last_claim)
            d, r = divmod(rem, 86400)
            h, r = divmod(r, 3600)
            m, _ = divmod(r, 60)
            await message.reply(f"⏳ <b>Weekly already claimed!</b>\nReturn in <b>{d}d {h}h {m}m</b> to claim again.", parse_mode=ParseMode.HTML)
            return

        valid_rarities = ["Basic 🃏", "Elite ⚓"]
        locked_animes = db.get("settings", {}).get("locked_animes", [])
        locked_animes_lower = [a.lower().strip() for a in locked_animes]

        tier_pool = {k: v for k, v in db.get("global_cards", {}).items()
                     if format_rarity(v["rarity"]) in valid_rarities
                     and v["anime"].lower().strip() not in locked_animes_lower}

        if not tier_pool:
            await message.reply("Weekly reward system is temporarily unavailable because no unlocked Basic or Elite cards are currently registered in the database.", parse_mode=ParseMode.HTML)
            return

        card_id, card_data = random.choice(list(tier_pool.items()))

        bio_bonus    = await has_bot_in_bio(uid_int)
        base_reward  = 500
        bonus_reward = 300 if bio_bonus else 0
        total_reward = base_reward + bonus_reward

        db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + total_reward
        db["users"][user_id]["last_weekly"]  = now

        user_cards = db["users"][user_id].setdefault("cards", {})
        if card_id not in user_cards:
            user_cards[card_id] = {"name": card_data["name"], "rarity": card_data["rarity"], "amount": 0}
        user_cards[card_id]["amount"] += 1
        db["users"][user_id]["total_claimed"] = db["users"][user_id].get("total_claimed", 0) + 1
        save_db()

        log_action(db, user_id, {
            "type": "weekly_claim",
            "amount": total_reward,
            "bio_bonus": bio_bonus,
            "chat_id": message.chat.id,
            "chat_title": message.chat.title or message.chat.first_name or "DM"
        })

        display_rarity = format_rarity(card_data["rarity"])
        bonus_line = f" +{bonus_reward} Shards" if bio_bonus else " "
        msg = (
            "<b>「 💠 WEEKLY CLAIM REWARDS ぁ 」</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Card :</b> {card_data['name']}<b>〔{display_rarity}〕</b>\n"
            f"<b>Anime : </b> {card_data.get('anime', 'Unknown')}\n"
            f"<b>Base Shards : </b> +{base_reward} Shards 💠[<b>Bio Bonus:</b>{bonus_line}]\n"
        )
        if not bio_bonus:
            msg += f"<b>Note : </b> Add bot Usernames (@Animenx_bot) to your Bio To get Bonus .\n"
        msg += (
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote><b>Total Balance </b>: {db['users'][user_id]['nexus_shards']} Shards 💠</blockquote>"
        )
        try:
            await message.reply_photo(photo=card_data["file_id"], caption=msg, parse_mode=ParseMode.HTML, has_spoiler=True)
        except Exception:
            await message.reply(msg, parse_mode=ParseMode.HTML)


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
        await message.reply(
            f"⏳ <b>Out of rolls!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"Your pins are resetting.\nReturn in <b>{h}h {m}m</b>.",
            parse_mode=ParseMode.HTML
        )
        return

    if user_data.get("roll_count", 0) == 0:
        user_data["roll_reset"] = now + (8 * 3600)

    user_data["roll_count"] += 1
    rolls_left = 10 - user_data["roll_count"]
    # Save the spent roll immediately, before any Telegram call that could
    # fail (flood control, missing send rights, etc.). Previously this only
    # saved at the very end, so a failed/unhandled send meant the roll was
    # never actually deducted — letting a user retry for free and hammer
    # the same flood-controlled chat over and over.
    save_db()

    try:
        dice_msg = await message.answer_dice(emoji="🎳")
    except (TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest):
        return
    await asyncio.sleep(4)

    shards_won = 0
    if dice_msg.dice.value == 6:
        shards_won = random.randint(40, 60)
        user_data["nexus_shards"] = user_data.get("nexus_shards", 0) + shards_won

    save_db()

    if shards_won:
        log_action(db, user_id, {
            "type": "bowling_win",
            "amount": shards_won,
            "chat_id": message.chat.id,
            "chat_title": message.chat.title or message.chat.first_name or "DM"
        })

    try:
        if shards_won:
            await message.reply(
                f"<b>「 STRIKE! ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🎉 You knocked down all the pins!\n"
                f"💠 Earned: <b>{shards_won} Shards</b>\n"
                f"🎳 Rolls left: <b>{rolls_left}/10</b>",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply(
                f"<b>「 MISS ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"You didn't clear the pins. Keep trying!\n"
                f"🎳 Rolls left: <b>{rolls_left}/10</b>",
                parse_mode=ParseMode.HTML
            )
    except (TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest):
        pass


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
        await message.reply(
            f"⏳ <b>Out of stamina!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"You need to rest your arms.\nReturn in <b>{h}h {m}m</b>.",
            parse_mode=ParseMode.HTML
        )
        return

    if user_data.get("throw_count", 0) == 0:
        user_data["throw_reset"] = now + (8 * 3600)

    user_data["throw_count"] += 1
    throws_left = 10 - user_data["throw_count"]
    # See bowling_roll_cmd — save the spent throw immediately, before any
    # Telegram call that could fail (flood control, missing send rights,
    # etc.), so a failed send can't be retried for free.
    save_db()

    try:
        dice_msg = await message.answer_dice(emoji="🏀")
    except (TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest):
        return
    await asyncio.sleep(4)

    shards_won = 0
    if dice_msg.dice.value >= 4:
        shards_won = random.randint(40, 60)
        user_data["nexus_shards"] = user_data.get("nexus_shards", 0) + shards_won

    save_db()

    if shards_won:
        log_action(db, user_id, {
            "type": "basketball_win",
            "amount": shards_won,
            "chat_id": message.chat.id,
            "chat_title": message.chat.title or message.chat.first_name or "DM"
        })

    try:
        if shards_won:
            await message.reply(
                f"<b>「 SWISH! ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🎉 Nothing but net!\n"
                f"💠 Earned: <b>{shards_won} Shards</b>\n"
                f"🏀 Throws left: <b>{throws_left}/10</b>",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply(
                f"<b>「 MISS ぁ 」</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"You missed the shot. Keep practicing!\n"
                f"🏀 Throws left: <b>{throws_left}/10</b>",
                parse_mode=ParseMode.HTML
            )
    except (TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest):
        pass


# ==========================================
# SHARDS TRANSFER SYSTEM (/sgive)
# ==========================================
@main_router.message(Command("sgive"))
async def sgive_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    sender_id = str(uid_int)
    sender_name = message.from_user.first_name

    # Cooldown check for regular users to prevent double-spending/rapid spam
    now = time.time()
    if uid_int not in ADMIN_IDS:
        last_sgive = _sgive_cooldowns.get(sender_id, 0)
        if now - last_sgive < SGIVE_COOLDOWN_SECS:
            rem = int(SGIVE_COOLDOWN_SECS - (now - last_sgive))
            await message.reply(f"⏳ <b>Transfer cooldown active!</b>\nPlease wait <b>{format_wait_mmss(rem)}</b>.", parse_mode=ParseMode.HTML)
            return

    if not command.args:
        await message.reply("<b>Usage:</b> Reply to a user with <code>/sgive &lt;amount&gt;</code>", parse_mode=ParseMode.HTML)
        return

    args = command.args.split()
    target_id = None
    target_name = "User"
    amount_str = ""

    # Check if replying to a message
    if message.reply_to_message and message.reply_to_message.sender_chat:
        # Message was posted by a channel (e.g. an anonymous admin posting
        # "as the channel", or a linked-channel post) — there's no real user
        # account behind it to credit shards to.
        await message.reply("You cannot transfer shards to a channel.", parse_mode=ParseMode.HTML)
        return

    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.is_bot:
            await message.reply("You cannot transfer shards to a bot.", parse_mode=ParseMode.HTML)
            return
        target_id = str(message.reply_to_message.from_user.id)
        target_name = message.reply_to_message.from_user.first_name
        amount_str = args[0]
    else:
        await message.reply("<b>Usage:</b> Reply to a user with <code>/sgive &lt;amount&gt;</code>", parse_mode=ParseMode.HTML)
        return

    if not target_id:
        await message.reply("Could not resolve target user.", parse_mode=ParseMode.HTML)
        return

    if target_id == sender_id:
        await message.reply("You cannot transfer shards to yourself.", parse_mode=ParseMode.HTML)
        return

    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await message.reply("Amount must be a valid positive integer.", parse_mode=ParseMode.HTML)
        return

    if amount < SGIVE_MIN_AMOUNT:
        await message.reply(f"Minimum transfer amount is <b>{SGIVE_MIN_AMOUNT:,}</b> 💠.", parse_mode=ParseMode.HTML)
        return

    if amount > SGIVE_MAX_AMOUNT:
        await message.reply(f"Maximum transfer amount is <b>{SGIVE_MAX_AMOUNT:,}</b> 💠 per transfer.", parse_mode=ParseMode.HTML)
        return

    db = ensure_user(sender_id, sender_name, message.from_user.username)
    db = ensure_user(target_id, target_name)

    sender_bal = db["users"][sender_id].get("nexus_shards", 0)
    if sender_bal < amount:
        await message.reply(f"You do not have enough shards. Your balance: <b>{sender_bal:,}</b> 💠", parse_mode=ParseMode.HTML)
        return

    # ── Execute transfer ──────────────────────────────────────────────────────
    db["users"][sender_id]["nexus_shards"] = sender_bal - amount
    db["users"][target_id]["nexus_shards"] = db["users"][target_id].get("nexus_shards", 0) + amount

    chat_title = message.chat.title or "Private DM"
    log_action(db, sender_id, {
        "type": "sgive_sent", "amount": amount,
        "cp_id": target_id, "cp_name": target_name,
        "chat_id": message.chat.id, "chat_title": chat_title,
    })
    log_action(db, target_id, {
        "type": "sgive_received", "amount": amount,
        "cp_id": sender_id, "cp_name": sender_name,
        "chat_id": message.chat.id, "chat_title": chat_title,
    })
    save_db()

    # Update cooldown state
    _sgive_cooldowns[sender_id] = now

    target_mention = get_mention(target_id, target_name)
    sender_mention = get_mention(sender_id, sender_name)

    # ── Public Transfer Log ───────────────────────────────────────────────────
    log_text = (
        "↑↓ <b>SHARD TRANSFERRED</b>\n\n"
        f"<b>FROM:</b> {sender_id}\n"
        f"<b>TO:</b> {target_id}\n"
        f"<b>AMOUNT:</b> {amount:,} Shards 💠"
    )
    try:
        await bot.send_message(
            chat_id=config.PUBLIC_LOG_GROUP_ID,
            text=log_text,
            message_thread_id=config.LOG_THREAD_TRANSFER,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print(f"[LOG] Failed to send public transfer log to Topic {config.LOG_THREAD_TRANSFER}: {e}")

    # ── Public confirmation ───────────────────────────────────────────────────
    confirm_text = f"You gave <b>{amount:,} Shards 💠</b> to {target_mention}"
    await message.reply(confirm_text, parse_mode=ParseMode.HTML)
    
-e 

# ==========================================
# /gift (Spoiler + Confirmation with Daily Limits)
# ==========================================
@main_router.message(Command("gift"))
async def gift_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Reply to a user's message to gift them a card.", parse_mode=ParseMode.HTML)
        return

    target_user = message.reply_to_message.from_user
    if target_user.is_bot:
        await message.reply("You cannot gift cards to bots.", parse_mode=ParseMode.HTML)
        return
    if str(target_user.id) == str(message.from_user.id):
        await message.reply("You cannot gift a card to yourself.", parse_mode=ParseMode.HTML)
        return
    if not command.args:
        await message.reply("<b>Usage:</b> <code>/gift &lt;card name&gt;</code>", parse_mode=ParseMode.HTML)
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
            await message.reply(
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
        await message.reply(
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
        await message.reply(
            f"<b>Recipient limit reached!</b>\nThis user has already received their maximum of <b>{DAILY_GIFT_RECEIVE_LIMIT}</b> gifts today.",
            parse_mode=ParseMode.HTML
        )
        return

    query    = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})

    if not my_cards:
        await message.reply("You don't own any cards yet!", parse_mode=ParseMode.HTML)
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
        await message.reply(f"You do not own a card matching <b>{command.args}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data    = db["global_cards"].get(matched_cid, {})
    display_rarity = format_rarity(matched_data["rarity"])

    caption = (
        "<b>「 GIFT CARD ぁ 」\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Character : </b>"
        f"{matched_data['name']}\n"
        f"<b>Rarity :</b> {display_rarity}\n\n"
        f"<blockquote><b>⤿ Are you sure you want to gift this to {get_mention(target_user.id, target_user.first_name)}?</b></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Yes, Gift Card", callback_data=f"cfgift_{user_id}_{target_id}_{matched_cid}")],
        [InlineKeyboardButton(text="Cancel", callback_data=f"cancel_action_{user_id}")]
    ])
    await message.reply_photo(
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

    rarity_normalized = format_rarity(card_data["rarity"])
    target_name_for_log = db["users"][target_id].get("name", "User")
    chat_title = cq.message.chat.title or "Private DM"
    log_action(db, user_id, {
        "type": "gift_sent", "card_name": card_data["name"], "rarity": rarity_normalized,
        "cp_id": target_id, "cp_name": target_name_for_log,
        "chat_id": cq.message.chat.id, "chat_title": chat_title,
    })
    log_action(db, target_id, {
        "type": "gift_received", "card_name": card_data["name"], "rarity": rarity_normalized,
        "cp_id": user_id, "cp_name": cq.from_user.first_name,
        "chat_id": cq.message.chat.id, "chat_title": chat_title,
    })

    await check_and_reward_referral(target_id, db)
    save_db()

    target_name    = db["users"][target_id].get("name", "User")
    display_rarity = format_rarity(card_data["rarity"])

    caption = (
        f"<b>「 CARD GIFTED 🎁 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"You successfully gifted <b>{card_data['name']}</b> [{display_rarity}] to {get_mention(target_id, target_name)}!\n\n"
        f"📊 Daily Gifts Sent: <b>{sender_gift_data['sent']}/{DAILY_GIFT_SEND_LIMIT}</b>"
    )
    await cq.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=None)
    await cq.answer("🎁 Gift sent successfully!")
-e 


# ==========================================
# /trade CARD-FOR-CARD TRADING SYSTEM
# ==========================================
def _find_card_match(query: str, pool: dict):
    """Requires an EXACT (case-insensitive, whitespace-trimmed) full card
    name match against a {cid: cdata} pool.

    Trade previously reused the fuzzy/partial matcher shared with /gift and
    /burn, but that caused false matches whenever two cards shared a common
    substring — e.g. typing "Goku" could silently resolve to "Ultra Instinct
    Goku" (or vice versa) instead of the plain "Goku" card. Trades move real
    inventory both ways, so we require the full, exact name here rather than
    guessing which card was meant.
    """
    query = query.strip().lower()
    for cid, cdata in pool.items():
        if cdata.get("amount", 0) <= 0:
            continue
        if cdata["name"].strip().lower() == query:
            return (cid, cdata)
    return None


async def _expire_trade(trade_id: str):
    await asyncio.sleep(TRADE_EXPIRY_SECS)
    trade = active_trades.get(trade_id)
    if not trade or trade.get("status") != "pending":
        return
    trade["status"] = "expired"
    try:
        await bot.edit_message_text(
            chat_id=trade["chat_id"], message_id=trade["message_id"],
            text="<b>「 TRADE EXPIRED ⌛ 」</b>\n━━━━━━━━━━━━━━━━━━━━\nThis trade offer went unanswered and has expired.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    active_trades.pop(trade_id, None)


@main_router.message(Command("trade"))
async def trade_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Reply to a user's message to propose a trade.", parse_mode=ParseMode.HTML)
        return

    target_user = message.reply_to_message.from_user
    if target_user.is_bot:
        await message.reply("You cannot trade with bots.", parse_mode=ParseMode.HTML)
        return
    if str(target_user.id) == str(message.from_user.id):
        await message.reply("You cannot trade with yourself.", parse_mode=ParseMode.HTML)
        return

    if not command.args or "|" not in command.args:
        await message.reply(
            "<b>Usage:</b> <code>/trade your card name | their card name</code>\n"
            "<i>Reply to the user you want to trade with.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    raw_my, raw_their = command.args.split("|", 1)
    my_query    = raw_my.strip().lower()
    their_query = raw_their.strip().lower()
    if not my_query or not their_query:
        await message.reply(
            "<b>Usage:</b> <code>/trade your card name | their card name</code>",
            parse_mode=ParseMode.HTML
        )
        return

    user_id   = str(message.from_user.id)
    target_id = str(target_user.id)

    # Trade cooldown (non-admins) — 1 minute between offers
    now = time.time()
    if uid_int not in ADMIN_IDS:
        last_trade = _trade_cooldowns.get(user_id, 0)
        if now - last_trade < TRADE_COOLDOWN_SECS:
            rem = int(TRADE_COOLDOWN_SECS - (now - last_trade))
            await message.reply(f"⏳ <b>Trade cooldown active!</b>\nPlease wait <b>{rem}s</b> before offering another trade.", parse_mode=ParseMode.HTML)
            return

    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    db = ensure_user(target_id, target_user.first_name, target_user.username)

    my_cards    = db["users"][user_id].get("cards", {})
    their_cards = db["users"][target_id].get("cards", {})

    if not my_cards:
        await message.reply("You don't own any cards yet!", parse_mode=ParseMode.HTML)
        return
    if not their_cards:
        await message.reply(f"{get_mention(target_id, target_user.first_name)} doesn't own any cards yet!", parse_mode=ParseMode.HTML)
        return

    my_match    = _find_card_match(my_query, my_cards)
    their_match = _find_card_match(their_query, their_cards)

    if not my_match:
        await message.reply(
            f"You don't own a card named exactly <b>{raw_my.strip()}</b>.\n"
            f"<i>Trades need the full card name (e.g. \"Ultra Instinct Goku\", not just \"Goku\").</i>",
            parse_mode=ParseMode.HTML
        )
        return
    if not their_match:
        await message.reply(
            f"{get_mention(target_id, target_user.first_name)} doesn't own a card named exactly <b>{raw_their.strip()}</b>.\n"
            f"<i>Trades need the full card name (e.g. \"Ultra Instinct Goku\", not just \"Goku\").</i>",
            parse_mode=ParseMode.HTML
        )
        return

    my_cid, my_cdata       = my_match
    their_cid, their_cdata = their_match

    my_rarity    = format_rarity(my_cdata["rarity"])
    their_rarity = format_rarity(their_cdata["rarity"])

    if not _trade_rarities_compatible(my_rarity, their_rarity):
        await message.reply(
            "<b>Invalid trade!</b>\n\n"
            "🃏 <b>Basic</b> can trade for 🃏 Basic / ⚓ Elite\n"
            "⚓ <b>Elite</b> can trade for 🃏 Basic / ⚓ Elite\n"
            "❄️ <b>Divine</b> can trade for ❄️ Divine only",
            parse_mode=ParseMode.HTML
        )
        return

    trade_id = uuid.uuid4().hex[:12]
    active_trades[trade_id] = {
        "initiator_id": user_id, "initiator_name": message.from_user.first_name,
        "target_id": target_id, "target_name": target_user.first_name,
        "my_cid": my_cid, "their_cid": their_cid,
        "status": "pending", "created": now,
    }

    caption = (
        "<b>「 TRADE OFFER 🔄 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{get_mention(user_id, message.from_user.first_name)} wants to trade with {get_mention(target_id, target_user.first_name)}!\n\n"
        f"Offering ― <b>{my_cdata['name']}</b> [{my_rarity}]\n"
        f"Wants ― <b>{their_cdata['name']}</b> [{their_rarity}]\n\n"
        f"{get_mention(target_id, target_user.first_name)} choose your actions !"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Accept", callback_data=f"trd_acc_{trade_id}"),
            InlineKeyboardButton(text="❌ Decline", callback_data=f"trd_dec_{trade_id}")
        ]
    ])
    sent = await message.reply(caption, reply_markup=kb, parse_mode=ParseMode.HTML)
    active_trades[trade_id]["message_id"] = sent.message_id
    active_trades[trade_id]["chat_id"]    = sent.chat.id

    if uid_int not in ADMIN_IDS:
        _trade_cooldowns[user_id] = now

    asyncio.create_task(_expire_trade(trade_id))


@main_router.callback_query(F.data.startswith("trd_acc_"))
async def accept_trade_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await cq.answer("🔇 You are currently restricted.", show_alert=True)
        return

    trade_id = cq.data.split("trd_acc_", 1)[1]
    trade = active_trades.get(trade_id)
    if not trade or trade.get("status") != "pending":
        await cq.answer("This trade offer is no longer active.", show_alert=True)
        return

    if str(uid_int) != trade["target_id"]:
        await cq.answer("This trade offer is not for you!", show_alert=True)
        return

    if _check_action_cooldown(f"trade_{trade['target_id']}"):
        await cq.answer("⏳ Please wait a moment before responding again.", show_alert=True)
        return

    db = load_db()
    sender_id, target_id = trade["initiator_id"], trade["target_id"]
    my_cid, their_cid = trade["my_cid"], trade["their_cid"]

    sender_cards = db["users"].get(sender_id, {}).get("cards", {})
    target_cards = db["users"].get(target_id, {}).get("cards", {})

    # Re-validate ownership at accept-time — inventories may have changed since the offer was made
    if my_cid not in sender_cards or sender_cards[my_cid].get("amount", 0) <= 0 or \
       their_cid not in target_cards or target_cards[their_cid].get("amount", 0) <= 0:
        trade["status"] = "cancelled"
        active_trades.pop(trade_id, None)
        await cq.answer("One of the cards is no longer available!", show_alert=True)
        try:
            await cq.message.edit_text(
                "<b>「 TRADE CANCELLED 」</b>\n━━━━━━━━━━━━━━━━━━━━\nOne of the cards is no longer available.",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return

    my_data    = dict(sender_cards[my_cid])
    their_data = dict(target_cards[their_cid])

    # Remove offered card from sender, add it to target
    sender_cards[my_cid]["amount"] -= 1
    if sender_cards[my_cid]["amount"] <= 0:
        del sender_cards[my_cid]
        if db["users"][sender_id].get("special_card") == my_cid:
            db["users"][sender_id]["special_card"] = None
    target_cards.setdefault(my_cid, {"name": my_data["name"], "rarity": my_data["rarity"], "amount": 0})
    target_cards[my_cid]["amount"] += 1

    # Remove requested card from target, add it to sender
    target_cards[their_cid]["amount"] -= 1
    if target_cards[their_cid]["amount"] <= 0:
        del target_cards[their_cid]
        if db["users"][target_id].get("special_card") == their_cid:
            db["users"][target_id]["special_card"] = None
    sender_cards.setdefault(their_cid, {"name": their_data["name"], "rarity": their_data["rarity"], "amount": 0})
    sender_cards[their_cid]["amount"] += 1

    chat_title = cq.message.chat.title or "Private DM"
    log_action(db, sender_id, {
        "type": "trade_sent", "card_name": my_data["name"], "rarity": format_rarity(my_data["rarity"]),
        "cp_id": target_id, "cp_name": db["users"][target_id].get("name", "User"),
        "chat_id": cq.message.chat.id, "chat_title": chat_title,
    })
    log_action(db, target_id, {
        "type": "trade_sent", "card_name": their_data["name"], "rarity": format_rarity(their_data["rarity"]),
        "cp_id": sender_id, "cp_name": db["users"][sender_id].get("name", "User"),
        "chat_id": cq.message.chat.id, "chat_title": chat_title,
    })
    save_db()
    await config.flush_db_now()

    trade["status"] = "completed"
    active_trades.pop(trade_id, None)

    my_rarity_disp    = format_rarity(my_data["rarity"])
    their_rarity_disp = format_rarity(their_data["rarity"])

    caption = (
        "<b>「 TRADE COMPLETED ✅ 」</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{get_mention(sender_id, trade['initiator_name'])} traded away <b>{my_data['name']}</b> [{my_rarity_disp}]\n"
        f"{get_mention(target_id, trade['target_name'])} traded away <b>{their_data['name']}</b> [{their_rarity_disp}]\n\n"
        f"Trade Successfully completed !"
    )
    try:
        await cq.message.edit_text(caption, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cq.answer("🔄 Trade completed!")

    # ── Public Trade Log ──────────────────────────────────────────────────
    my_emoji    = my_rarity_disp.split()[-1]
    their_emoji = their_rarity_disp.split()[-1]
    log_text = (
        "⇄ <b>CARD TRADE COMPLETED</b>\n\n"
        f"<b>FROM:</b> {sender_id}\n"
        f"<b>CARD:</b> {my_data['name']} {my_emoji}\n\n"
        f"<b>TO:</b> {target_id}\n"
        f"<b>CARD:</b> {their_data['name']} {their_emoji}"
    )
    try:
        await bot.send_message(
            chat_id=config.PUBLIC_LOG_GROUP_ID,
            text=log_text,
            message_thread_id=config.LOG_THREAD_TRADE,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print(f"[LOG] Failed to send public trade log to Topic {config.LOG_THREAD_TRADE}: {e}")


@main_router.callback_query(F.data.startswith("trd_dec_"))
async def decline_trade_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id

    trade_id = cq.data.split("trd_dec_", 1)[1]
    trade = active_trades.get(trade_id)
    if not trade or trade.get("status") != "pending":
        await cq.answer("This trade offer is no longer active.", show_alert=True)
        return

    # Either side can back out of a pending offer
    if str(uid_int) not in (trade["target_id"], trade["initiator_id"]):
        await cq.answer("This trade offer is not for you!", show_alert=True)
        return

    trade["status"] = "declined"
    active_trades.pop(trade_id, None)

    try:
        await cq.message.edit_text(
            "<b>「 TRADE DECLINED ❌ 」</b>\n━━━━━━━━━━━━━━━━━━━━\nThis trade offer was declined.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    await cq.answer("Trade declined.")

-e 


# ==========================================
# SHARDS BALANCE (/shards)
# ==========================================
@main_router.message(Command("shards"))
async def shards_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return
    db     = ensure_user(str(uid_int), message.from_user.first_name, message.from_user.username)
    shards = db["users"][str(uid_int)].get("nexus_shards", 0)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Shards", url="https://t.me/Animenx_bot/webdeck")]
    ])
    await message.reply(
        f"<b>「 💠 NEXUS SHARDS ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Your Current Shards ⦂ {shards} </b>💠 ",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )




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
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b><i>Verification Rule:</i></b> Invited users must seize <b>at least 1 card</b> to validate and trigger payouts.\n\n"
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
        f"━━━━━━━━━━━━━━━━━━━━"
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
                    callback_data=f"close_msg|{uid_int}"
                )
            ]
        ]
    )

    await message.reply(
        msg,
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

# ==========================================
# PROMOTIONAL CODES ENGINE (/redeem)
# ==========================================
REDEEM_DUPLICATE_CHANCE = 0.20  # 20% chance to intentionally award a card the user already owns


@main_router.message(Command("redeem"))
async def redeem_promo_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    # Small local helper: a promo redemption already updated the database by
    # the time most of these replies fire, so a send failure (flood control,
    # or the bot lacking permission to post text in this chat) shouldn't
    # blow up as an unhandled exception — just drop the confirmation text.
    async def safe_reply(text, **kwargs):
        try:
            await message.reply(text, **kwargs)
        except (TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest):
            pass

    if not command.args:
        await safe_reply("<b>Usage:</b> <code>/redeem &lt;CODE&gt;</code>\nExample: <code>/redeem SUMMERSHARDS</code>", parse_mode=ParseMode.HTML)
        return

    code = command.args.upper().strip()
    db   = load_db()
    promos = db.setdefault("promos", {})

    if code not in promos:
        await safe_reply("Invalid, expired, or incorrect promo code.", parse_mode=ParseMode.HTML)
        return

    promo   = promos[code]
    user_id = str(uid_int)

    if user_id in promo.setdefault("claimed_by", []):
        await safe_reply("You have already claimed this promo code!", parse_mode=ParseMode.HTML)
        return

    if len(promo["claimed_by"]) >= promo["max_claims"]:
        await safe_reply("This promo code has reached its maximum claim limit and is expired.", parse_mode=ParseMode.HTML)
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
                user_cards = db["users"][user_id].setdefault("cards", {})

                # 20% chance to deliberately hand out a duplicate (a card
                # within this rarity pool the user already owns); otherwise
                # prefer a card they don't own yet, falling back to the
                # full pool if every card in this rarity is already owned.
                owned_in_pool   = {k: v for k, v in card_pool.items() if k in user_cards}
                unowned_in_pool = {k: v for k, v in card_pool.items() if k not in user_cards}

                if owned_in_pool and (random.random() < REDEEM_DUPLICATE_CHANCE or not unowned_in_pool):
                    card_id, card_data = random.choice(list(owned_in_pool.items()))
                else:
                    card_id, card_data = random.choice(list((unowned_in_pool or card_pool).items()))

                if card_id not in user_cards:
                    user_cards[card_id] = {"name": card_data["name"], "rarity": card_data["rarity"], "amount": 0}
                user_cards[card_id]["amount"] += quantity
                db["users"][user_id]["total_claimed"] = db["users"][user_id].get("total_claimed", 0) + quantity
                cards_awarded.append((card_data, quantity))

    promo["claimed_by"].append(user_id)
    await check_and_reward_referral(user_id, db)
    save_db()

    if shards_awarded > 0:
        log_action(db, user_id, {
            "type": "promo_shards",
            "amount": shards_awarded,
            "code": code,
            "chat_id": message.chat.id,
            "chat_title": message.chat.title or message.chat.first_name or "DM"
        })

    msg_lines = [
        f"<b>「 🎁 PROMO CODE REDEEMED 」</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🎫 Code: <code>{code}</code>\n",
        f"📦 <b>Acquired Rewards:</b>"
    ]
    if shards_awarded > 0:
        msg_lines.append(f" • 💠 <b>Nexus Shards:</b> +{shards_awarded}")
    for cdata, qty in cards_awarded:
        disp_rarity = format_rarity(cdata["rarity"])
        msg_lines.append(f" • 🎴 <b>{cdata['name']}</b> ({disp_rarity}) x{qty}")
    msg_lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    caption = "\n".join(msg_lines)

    if cards_awarded:
        first_card_data = cards_awarded[0][0]
        try:
            await message.reply_photo(photo=first_card_data["file_id"], caption=caption, parse_mode=ParseMode.HTML, has_spoiler=True)
        except Exception:
            await safe_reply(caption, parse_mode=ParseMode.HTML)
    else:
        await safe_reply(caption, parse_mode=ParseMode.HTML)
