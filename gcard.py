# --- START OF FILE gcard.py ---

import time
import random
import asyncio
import difflib
import io
import re

from PIL import Image, ImageFilter
from aiogram import F
from aiogram.types import Message, BufferedInputFile, InputMediaPhoto
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

import config
from config import (
    bot, main_router, load_db, format_rarity,
    get_mention, is_ghost_banned, is_shadow_banned
)

# ==========================================
# GUESS-THE-CARD MINIGAME SETTINGS
# ==========================================
GCARD_ROUND_TIMEOUT_SECS = 45      # time players have to guess before reveal

# Only these regions of the card art get blurred — everything else (the
# character's face/body/artwork) stays fully visible. Regions are fractions
# of (width, height) so they scale to any image size: (x0, y0, x1, y1).
NAME_BLUR_REGIONS = [
    (0.00, 0.06, 0.20, 0.66),   # left edge: vertical kanji + big vertical name text
    (0.55, 0.00, 1.00, 0.16),   # top-right: name / anime title / kanji / quote badge
    (0.10, 0.61, 0.90, 0.69),   # center: italic quote attribution ("— Character Name")
    (0.00, 0.96, 0.32, 1.00),   # footer: card ID code (often encodes the surname)
]
NAME_BLUR_RADIUS = 18

# ── In-memory state ──────────────────────────────────────────────────────────
active_gcard: dict = {}   # str(chat_id) -> {"card_id","time","message_id"}


def _blur_card_image(raw_bytes: bytes) -> bytes:
    """Blurs only the specific name-bearing regions of the card, leaving the
    main character artwork fully visible."""
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    for (fx0, fy0, fx1, fy1) in NAME_BLUR_REGIONS:
        box = (int(fx0 * w), int(fy0 * h), int(fx1 * w), int(fy1 * h))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        region = img.crop(box)
        blurred_region = region.filter(ImageFilter.GaussianBlur(radius=NAME_BLUR_RADIUS))
        img.paste(blurred_region, box)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    out.seek(0)
    return out.getvalue()


async def _reveal_gcard(chat_id: int, msg_id: int, original_file_id: str, caption: str):
    """Swaps the round's blurred photo back to the real card art with a reveal caption."""
    try:
        await bot.edit_message_media(
            chat_id=chat_id, message_id=msg_id,
            media=InputMediaPhoto(media=original_file_id, caption=caption, parse_mode=ParseMode.HTML)
        )
    except TelegramBadRequest:
        pass
    except Exception:
        pass


async def _delete_message_after_delay(chat_id: int, message_id: int, delay: int = 120):
    """Safely deletes a targeted message after a specific delay in seconds."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _expire_gcard(cid_str: str, msg_id: int, chat_id: int):
    await asyncio.sleep(GCARD_ROUND_TIMEOUT_SECS)
    if cid_str in active_gcard and active_gcard[cid_str].get("message_id") == msg_id:
        card_id = active_gcard[cid_str]["card_id"]
        del active_gcard[cid_str]

        db = load_db()
        card_data = db.get("global_cards", {}).get(card_id, {})
        name  = card_data.get("name", "?")
        anime = card_data.get("anime", "?")

        reveal_caption = (
            "<b>「 ⏰ TIME'S UP ぁ 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "😔 Nobody guessed it in time!\n\n"
            f"👤 It was ➜ <b>{name}</b>\n"
            f"📺 Anime  ➜ <b>{anime}</b>"
        )
        await _reveal_gcard(chat_id, msg_id, card_data.get("file_id"), reveal_caption)
        
        # Clean up the revealed card from the group after 2 minutes
        asyncio.create_task(_delete_message_after_delay(chat_id, msg_id, 120))


@main_router.message(Command("gcard"))
async def gcard_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        return

    chat_id = message.chat.id
    cid_str = str(chat_id)

    if cid_str in active_gcard:
        await message.reply(
            "⚠️ A guessing round is already live here! Just type the character's name in chat to answer it.",
            parse_mode=ParseMode.HTML
        )
        return

    db = load_db()
    if not db.get("global_cards"):
        await message.reply("❌ No cards exist in the system yet.", parse_mode=ParseMode.HTML)
        return

    card_id, card_data = random.choice(list(db["global_cards"].items()))
    display_rarity = format_rarity(card_data["rarity"])
    original_file_id = card_data["file_id"]

    caption = (
        "<b>「 🎴 GUESS THE CARD ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "✦ <b><i>Who's hiding behind the blur?</i></b>\n\n"
        f"🌟 Rarity ➜ <b>{display_rarity}</b>\n"
        f"⏱️ You have <b>{GCARD_ROUND_TIMEOUT_SECS}s</b> to guess!\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💮 Just type the character's name in chat to answer!"
    )

    try:
        file_info  = await bot.get_file(original_file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        blurred_bytes = _blur_card_image(file_bytes.getvalue())
        photo_input = BufferedInputFile(blurred_bytes, filename="gcard_blur.jpg")
        msg = await bot.send_photo(
            chat_id=chat_id, photo=photo_input,
            caption=caption, parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.reply(f"❌ Failed to start round: {e}", parse_mode=ParseMode.HTML)
        return

    active_gcard[cid_str] = {"card_id": card_id, "time": time.time(), "message_id": msg.message_id}
    asyncio.create_task(_expire_gcard(cid_str, msg.message_id, chat_id))


# Plain-text guess listener — NOT a command. Only fires on non-"/" text, and
# only does anything at all if this chat currently has a round running, so it
# never interferes with any other command handler in the bot.
@main_router.message(F.text, ~F.text.startswith("/"))
async def gcard_plain_guess_listener(message: Message):
    chat_id = message.chat.id
    cid_str = str(chat_id)

    if cid_str not in active_gcard:
        return  # no round running here — just an ordinary chat message

    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        return

    game_data  = active_gcard[cid_str]
    card_id    = game_data["card_id"]
    start_time = game_data["time"]
    msg_id     = game_data["message_id"]

    db = load_db()
    card_data = db["global_cards"].get(card_id)
    if not card_data:
        return

    target_name = card_data["name"].lower().strip()
    query = message.text.lower().strip()

    # Split targets into distinct alphanumeric parts to handle component guesses
    target_parts = re.findall(r'\b\w+\b', target_name)
    
    matched = False
    
    # 1. Direct match on the complete phrase
    if query == target_name:
        matched = True
    elif len(query) >= 3:
        # 2. Check if query matches any specific sub-part of the card name
        for part in target_parts:
            if len(part) < 3:
                continue
            # Exact match with a component part
            if query == part or part in query:
                matched = True
                break
            # Fuzzy match with an individual component part
            if difflib.SequenceMatcher(None, query, part).ratio() > 0.75:
                matched = True
                break
        
        # 3. Overall fuzzy match against the full combined name
        if not matched:
            if difflib.SequenceMatcher(None, query, target_name).ratio() > 0.70:
                matched = True

    if not matched:
        return  # wrong/unrelated message — stay silent, don't spam the chat

    time_taken = round(time.time() - start_time, 2)
    del active_gcard[cid_str]

    user_id = str(uid_int)
    name    = message.from_user.first_name
    display_rarity = format_rarity(card_data["rarity"])

    winner_text = (
        "<b>「 🎊 GUESSED CORRECTLY ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🎊 <b><i>{get_mention(user_id, name)}</i></b> guessed it in <b>{time_taken}s</b>!\n\n"
        f"👤 Character ➜ <b>{card_data['name']} 《{display_rarity}》</b>\n"
        f"📺 Anime    ➜ <b>{card_data['anime']}</b>"
    )
    
    # Send the win notification as a clean, brand-new text message
    winner_msg = await bot.send_message(
        chat_id=chat_id,
        text=winner_text,
        parse_mode=ParseMode.HTML
    )

    reveal_caption = (
        f"<b>「 🎴 REVEALED 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{card_data['name']}</b> 《{display_rarity}》\n"
        f"📺 {card_data['anime']}"
    )
    await _reveal_gcard(chat_id, msg_id, card_data.get("file_id"), reveal_caption)

    # Clean up both the revealed card message and the victory text after 2 minutes
    asyncio.create_task(_delete_message_after_delay(chat_id, msg_id, 120))
    asyncio.create_task(_delete_message_after_delay(chat_id, winner_msg.message_id, 120))

# --- END OF FILE gcard.py ---
