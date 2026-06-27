"""
==========================================
EARN — /earn (AdsGram rewarded ads)
==========================================
Lets a player watch a sponsored AdsGram ad and claim a reward —
either Nexus Shards or a random card (Basic to Elite rarity, never
Divine). One claim per user per 12 hours.

INTEGRATION NOTE
AdsGram's bot-only API (api.adsgram.ai/advbot) has no server-to-server
"ad watched" callback — that only exists in their Mini App JS SDK. So
this flow shows the ad, then unlocks a "Claim Reward" button after a
short delay (AD_WATCH_DELAY_SECONDS) instead of trusting an instant
claim. It's a soft gate, not a verified one — true verification would
require building a Mini App around AdsGram's AdController SDK instead
of this REST endpoint.

SETUP REQUIRED
  ADSGRAM_BLOCK_ID below is a placeholder — fill in your numeric Block
  ID from the AdsGram dashboard (Monetize bots -> Create block). Do not
  include the "bot-" prefix.
"""

import time
import random
import uuid

import aiohttp
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode

from config import main_router, load_db, save_db, ensure_user, RARITIES

# ==========================================
# SETTINGS
# ==========================================
ADSGRAM_TOKEN       = "c5eead7dd6164e11ba4569ecdba5eca2"
ADSGRAM_BLOCK_ID    = "REPLACE_WITH_YOUR_BLOCK_ID"   # numeric only, no "bot-" prefix
ADSGRAM_LANGUAGE    = "en"
ADSGRAM_API_URL     = "https://api.adsgram.ai/advbot"

EARN_COOLDOWN_SECONDS  = 12 * 3600
AD_WATCH_DELAY_SECONDS = 15     # min time before "Claim Reward" is honored

SHARD_REWARD_MIN    = 100
SHARD_REWARD_MAX    = 2000
SHARD_REWARD_CHANCE = 0.5       # 50% shards, 50% a card — pure random pick

CARD_REWARD_RARITIES = ["Basic 🃏", "Elite ⚓"]   # Divine excluded from /earn rewards

# In-memory pending claim sessions, keyed by str(user_id).
# { "shown_at": float, "claimed": bool }
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
        # No eligible cards exist yet — fall back to shards.
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


# ==========================================
# /earn COMMAND
# ==========================================
@main_router.message(Command("earn"))
async def earn_cmd(message: Message):
    uid = str(message.from_user.id)
    db = ensure_user(uid, message.from_user.first_name, message.from_user.username)

    now = time.time()
    last_earn = db["users"][uid].get("last_earn", 0)
    if now - last_earn < EARN_COOLDOWN_SECONDS:
        rem  = int(EARN_COOLDOWN_SECONDS - (now - last_earn))
        h, r = divmod(rem, 3600)
        m, _ = divmod(r, 60)
        await message.reply(
            f"⏳ <b>You've already claimed your reward!</b>\nCome back in <b>{h}h {m}m</b>.",
            parse_mode=ParseMode.HTML
        )
        return

    if uid in pending_earn and not pending_earn[uid]["claimed"]:
        await message.reply("⚠️ You already have an ad waiting — scroll up and claim it, or wait for it to expire.", parse_mode=ParseMode.HTML)
        return

    ad = await fetch_adsgram_ad(message.from_user.id)
    if not ad:
        await message.reply("😕 No ad is available right now — try again in a bit.", parse_mode=ParseMode.HTML)
        return

    pending_earn[uid] = {"shown_at": time.time(), "claimed": False}

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ad.get("button_name", "View Ad"), url=ad["click_url"])],
        [InlineKeyboardButton(text=ad.get("button_reward_name", "Claim"), url=ad.get("reward_url", ad["click_url"]))],
        [InlineKeyboardButton(text="🎁 Claim Bot Reward", callback_data=f"earn_claim_{uid}")]
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
        pending_earn.pop(uid, None)
        await message.reply("😕 Couldn't load that ad — try again.", parse_mode=ParseMode.HTML)


# ==========================================
# CLAIM CALLBACK
# ==========================================
@main_router.callback_query(lambda cq: cq.data and cq.data.startswith("earn_claim_"))
async def earn_claim_cb(cq: CallbackQuery):
    owner_id = cq.data.split("_", 2)[2]
    if str(cq.from_user.id) != owner_id:
        await cq.answer("⚠️ This isn't your reward!", show_alert=True)
        return

    session = pending_earn.get(owner_id)
    if not session:
        await cq.answer("This ad has expired — use /earn again.", show_alert=True)
        return

    if session["claimed"]:
        await cq.answer("You've already claimed this one.", show_alert=True)
        return

    elapsed = time.time() - session["shown_at"]
    if elapsed < AD_WATCH_DELAY_SECONDS:
        remaining = int(AD_WATCH_DELAY_SECONDS - elapsed)
        await cq.answer(f"⏳ Give the ad a few more seconds... ({remaining}s)", show_alert=True)
        return

    db  = load_db()
    uid = owner_id
    if uid not in db["users"]:
        await cq.answer("Something went wrong — try /earn again.", show_alert=True)
        return

    reward = roll_reward(db)
    grant_reward(db, uid, reward)
    db["users"][uid]["last_earn"] = time.time()
    save_db()

    session["claimed"] = True
    pending_earn.pop(owner_id, None)

    if reward["type"] == "shards":
        result_text = f"🎉 <b>Reward claimed!</b>\n💠 You got <b>{reward['amount']} Nexus Shards</b>!"
        await cq.answer(f"🎉 +{reward['amount']} Shards!", show_alert=True)
    else:
        result_text = f"🎉 <b>Reward claimed!</b>\n🃏 You got <b>{reward['name']}</b> [{reward['rarity']}]!"
        await cq.answer(f"🎉 You got {reward['name']}!", show_alert=True)

    try:
        await cq.message.edit_caption(caption=result_text, parse_mode=ParseMode.HTML, reply_markup=None)
    except Exception:
        try:
            await cq.message.edit_text(result_text, parse_mode=ParseMode.HTML, reply_markup=None)
        except Exception:
            pass
