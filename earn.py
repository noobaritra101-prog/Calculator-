"""
==========================================
EARN — /earn (AdsGram ads shown directly in chat)
==========================================
Flow:
  1. /earn calls AdsGram's bot-ads endpoint (api.adsgram.ai/advbot),
     which returns a sponsor ad: image, caption, and two AdsGram-
     controlled links (their own "view"/"claim" buttons — required by
     their terms; this is how the bot earns AdsGram revenue).
  2. The bot sends that ad straight into the chat, with a third button
     of its own: "🎁 Claim Reward". That third button is the only thing
     that pays out our shards/cards — it's not connected to AdsGram in
     any way, it's purely our own gate.
  3. The claim button only unlocks after a short delay.

IMPORTANT LIMITATION (be aware of this)
  AdsGram's bot-only REST endpoint has no server-to-server "ad was
  actually watched" callback — that only exists in their Mini App SDK.
  Since this version intentionally avoids a Mini App, there is no way
  to cryptographically prove the user watched anything. The delay gate
  is a deterrent, not a verification. If airtight verification ever
  matters more than chat-native ads, the Mini App SDK is the only way
  to get a real "ad watched" signal — that's a trade-off being made
  here on purpose per your direction.

SAFETY CHECKLIST — how each item is enforced here:
  • Only 1 reward per ad / unique reward ID / no duplicate claims
      -> each ad message gets its own single-use token; "claimed" is
         set True before any reward is granted, and the token is then
         discarded, closing the double-claim race.
  • Cooldown between ads / daily limit
      -> EARN_AD_COOLDOWN_SECONDS between /earn requests,
         EARN_DAILY_LIMIT successful claims per UTC day.
  • Reward belongs to the correct Telegram user
      -> token is bound to the uid that requested it; the tapping user
         must match exactly or the claim is rejected.
  • Log every reward
      -> send_log() call with user ID, time, reward, token.
  • Ignore invalid/repeated callbacks, validate server-side
      -> token format is checked before any lookup; nothing from the
         client is trusted beyond "is this a token we issued."
  • Reject expired requests
      -> PENDING_TOKEN_TTL_SECONDS.
  • Rate limiting / auto-block abusers
      -> repeated invalid claim attempts run through config.check_spam,
         which shadow-bans on its existing threshold.
  • HTTPS only / keep credentials private
      -> AdsGram endpoint is https-only; token/block ID are server-side
         constants, never exposed to the client.

SETUP REQUIRED
  ADSGRAM_BLOCK_ID below — your numeric AdsGram block ID, no "bot-"
  prefix (their bots-API docs are explicit that this param is the
  digits only). If your dashboard shows it as "bot-36462", use "36462".
"""

import time
import secrets
import random
from datetime import datetime, timezone

import aiohttp
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode

from config import main_router, load_db, save_db, ensure_user, check_spam, get_mention
from a_handlers import send_log

# ==========================================
# SETTINGS
# ==========================================
ADSGRAM_TOKEN    = "c5eead7dd6164e11ba4569ecdba5eca2"
ADSGRAM_BLOCK_ID = "36462"     # numeric only, no "bot-" prefix
ADSGRAM_LANGUAGE = "en"
ADSGRAM_API_URL  = "https://api.adsgram.ai/advbot"

EARN_AD_COOLDOWN_SECONDS  = 5 * 60     # min gap between ad requests
EARN_DAILY_LIMIT          = 30         # max successful claims per UTC day
PENDING_TOKEN_TTL_SECONDS = 10 * 60    # token must be claimed within this window
AD_WATCH_DELAY_SECONDS    = 15         # min time before "Claim Reward" is honored

SHARD_REWARD_MIN     = 100
SHARD_REWARD_MAX     = 2000
SHARD_REWARD_CHANCE  = 0.5             # 50% shards, 50% a card — pure random pick
CARD_REWARD_RARITIES = ["Basic 🃏", "Elite ⚓"]   # Divine excluded from /earn rewards

# In-memory pending claim sessions, keyed by token.
# { "uid": str, "created_at": float, "claimed": bool }
pending_earn: dict = {}


# ==========================================
# ADSGRAM API CALL
# ==========================================
async def fetch_adsgram_ad(tg_user_id: int) -> dict | None:
    params = {
        "tgid": tg_user_id,
        "blockid": ADSGRAM_BLOCK_ID,
        "language": ADSGRAM_LANGUAGE,
        "token": ADSGRAM_TOKEN,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(ADSGRAM_API_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get("click_url"):
                    return None
                return data
    except Exception as e:
        print(f"[EARN] AdsGram fetch error: {e}")
        return None


# ==========================================
# REWARD LOGIC
# ==========================================
def roll_reward(db: dict) -> dict:
    """Returns {'type': 'shards', 'amount': int} or {'type': 'card', 'card_id', 'name', 'rarity'}."""
    if random.random() < SHARD_REWARD_CHANCE:
        return {"type": "shards", "amount": random.randint(SHARD_REWARD_MIN, SHARD_REWARD_MAX)}

    global_cards = db.get("global_cards", {})
    pool = [(cid, c) for cid, c in global_cards.items() if c.get("rarity") in CARD_REWARD_RARITIES]
    if not pool:
        return {"type": "shards", "amount": random.randint(SHARD_REWARD_MIN, SHARD_REWARD_MAX)}

    card_id, card = random.choice(pool)
    return {"type": "card", "card_id": card_id, "name": card["name"], "rarity": card["rarity"]}


def grant_reward(db: dict, uid: str, reward: dict):
    if reward["type"] == "shards":
        db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + reward["amount"]
    else:
        cards = db["users"][uid]["cards"]
        cid = reward["card_id"]
        if cid not in cards:
            cards[cid] = {"name": reward["name"], "rarity": reward["rarity"], "amount": 0}
        cards[cid]["amount"] += 1


def _reward_label(reward: dict) -> str:
    if reward["type"] == "shards":
        return f"{reward['amount']} Shards 💠"
    return f"{reward['name']} [{reward['rarity']}]"


def _cleanup_expired_tokens():
    now = time.time()
    dead = [t for t, s in pending_earn.items() if now - s["created_at"] > PENDING_TOKEN_TTL_SECONDS]
    for t in dead:
        pending_earn.pop(t, None)


# ==========================================
# /earn COMMAND
# ==========================================
@main_router.message(Command("earn"))
async def earn_cmd(message: Message):
    uid = str(message.from_user.id)
    db = ensure_user(uid, message.from_user.first_name, message.from_user.username)
    user_data = db["users"][uid]

    now = time.time()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if user_data.get("ads_date") != today_str:
        user_data["ads_date"] = today_str
        user_data["ads_watched_today"] = 0
        save_db()

    if user_data.get("ads_watched_today", 0) >= EARN_DAILY_LIMIT:
        await message.reply(
            f"📅 <b>Daily ad limit reached!</b>\nYou can watch up to {EARN_DAILY_LIMIT} ads per day — come back tomorrow.",
            parse_mode=ParseMode.HTML
        )
        return

    last_request = user_data.get("last_ad_request", 0)
    if now - last_request < EARN_AD_COOLDOWN_SECONDS:
        rem = int(EARN_AD_COOLDOWN_SECONDS - (now - last_request))
        m, s = divmod(rem, 60)
        await message.reply(f"⏳ <b>Slow down!</b>\nYou can request another ad in <b>{m}m {s}s</b>.", parse_mode=ParseMode.HTML)
        return

    ad = await fetch_adsgram_ad(message.from_user.id)
    if not ad:
        await message.reply("😕 No ad is available right now — try again in a bit.", parse_mode=ParseMode.HTML)
        return

    _cleanup_expired_tokens()
    token = secrets.token_hex(8)
    pending_earn[token] = {"uid": uid, "created_at": now, "claimed": False}

    user_data["last_ad_request"] = now
    save_db()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ad.get("button_name", "View Ad"), url=ad["click_url"])],
        [InlineKeyboardButton(text=ad.get("button_reward_name", "Claim"), url=ad.get("reward_url", ad["click_url"]))],
        [InlineKeyboardButton(text="🎁 Claim Bot Reward", callback_data=f"earn_claim_{token}")]
    ])

    caption = ad.get("text_html", "Sponsored")

    try:
        if ad.get("image_url"):
            await message.answer_photo(
                photo=ad["image_url"],
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                protect_content=True
            )
        else:
            await message.answer(
                caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                protect_content=True
            )
    except Exception as e:
        print(f"[EARN] Send error: {e}")
        pending_earn.pop(token, None)
        await message.reply("😕 Couldn't load that ad — try again.", parse_mode=ParseMode.HTML)


# ==========================================
# CLAIM CALLBACK
# ==========================================
@main_router.callback_query(lambda cq: cq.data and cq.data.startswith("earn_claim_"))
async def earn_claim_cb(cq: CallbackQuery):
    uid_int = cq.from_user.id
    uid = str(uid_int)
    token = cq.data.split("_", 2)[2]

    if not token or len(token) > 64 or not all(c in "0123456789abcdef" for c in token):
        check_spam(uid_int)
        await cq.answer("⚠️ Invalid reward.", show_alert=True)
        return

    _cleanup_expired_tokens()
    session = pending_earn.get(token)

    if not session:
        check_spam(uid_int)
        await cq.answer("This ad has expired — use /earn again.", show_alert=True)
        return

    if session["claimed"]:
        check_spam(uid_int)
        await cq.answer("You've already claimed this one.", show_alert=True)
        return

    if session["uid"] != uid:
        check_spam(uid_int)
        await cq.answer("⚠️ This isn't your reward!", show_alert=True)
        return

    elapsed = time.time() - session["created_at"]
    if elapsed < AD_WATCH_DELAY_SECONDS:
        remaining = int(AD_WATCH_DELAY_SECONDS - elapsed)
        await cq.answer(f"⏳ Give the ad a few more seconds... ({remaining}s)", show_alert=True)
        return

    if elapsed > PENDING_TOKEN_TTL_SECONDS:
        pending_earn.pop(token, None)
        await cq.answer("This ad has expired — use /earn again.", show_alert=True)
        return

    # Close the claim window immediately — before any reward is granted —
    # so a double-fire of this callback can never pay out twice.
    session["claimed"] = True
    pending_earn.pop(token, None)

    db = ensure_user(uid, cq.from_user.first_name, cq.from_user.username)
    reward = roll_reward(db)
    grant_reward(db, uid, reward)
    db["users"][uid]["ads_watched_today"] = db["users"][uid].get("ads_watched_today", 0) + 1
    save_db()

    await cq.answer(f"🎉 You got {_reward_label(reward)}!", show_alert=True)

    result_text = f"🎉 <b>Reward claimed!</b>\nYou got <b>{_reward_label(reward)}</b>!"
    try:
        await cq.message.edit_caption(caption=result_text, parse_mode=ParseMode.HTML, reply_markup=None)
    except Exception:
        try:
            await cq.message.edit_text(result_text, parse_mode=ParseMode.HTML, reply_markup=None)
        except Exception:
            pass

    await send_log(
        "<b>「 🎬 AD REWARD CLAIMED 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"• 👤 <b>User:</b> {get_mention(uid_int, cq.from_user.first_name)} (<code>{uid}</code>)\n"
        f"• 🎁 <b>Reward:</b> {_reward_label(reward)}\n"
        f"• 🆔 <b>Completion ID:</b> <code>{token}</code>\n"
        f"• 🕐 <b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        "━━━━━━━━━━━━━━━━━"
    )
