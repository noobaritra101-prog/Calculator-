import time
import random
import asyncio
import difflib
import io

from PIL import Image, ImageFilter
from aiogram.types import Message, BufferedInputFile, InputMediaPhoto, ReactionTypeEmoji
from aiogram.filters import Command, CommandObject
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
GCARD_CHAT_COOLDOWN_SECS = 30      # wait between rounds in the same chat
GCARD_ROUND_TIMEOUT_SECS = 45      # time players have to guess before reveal
GCARD_BLUR_RADIUS = 22             # Gaussian blur strength applied to the card art

# ── In-memory state ──────────────────────────────────────────────────────────
active_gcard: dict          = {}   # str(chat_id) -> {"card_id","time","message_id"}
_gcard_chat_cooldown: dict  = {}   # str(chat_id) -> last round-start timestamp
_blur_cache: dict           = {}   # original file_id -> blurred file_id (avoid re-blurring)


def _blur_image_bytes(raw_bytes: bytes, radius: int = GCARD_BLUR_RADIUS) -> bytes:
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    blurred = img.filter(ImageFilter.GaussianBlur(radius=radius))
    out = io.BytesIO()
    blurred.save(out, format="JPEG", quality=85)
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


@main_router.message(Command("gcard"))
async def gcard_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        return

    chat_id = message.chat.id
    cid_str = str(chat_id)

    if cid_str in active_gcard:
        await message.reply(
            "⚠️ A guessing round is already live here! Use <code>/gguess [name]</code> to answer it.",
            parse_mode=ParseMode.HTML
        )
        return

    last_start = _gcard_chat_cooldown.get(cid_str, 0)
    elapsed = time.time() - last_start
    if elapsed < GCARD_CHAT_COOLDOWN_SECS:
        wait = int(GCARD_CHAT_COOLDOWN_SECS - elapsed)
        await message.reply(f"⏳ Please wait {wait}s before starting another round here.", parse_mode=ParseMode.HTML)
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
        "💮 Use /gguess [character name] to answer!"
    )

    try:
        if original_file_id in _blur_cache:
            msg = await bot.send_photo(
                chat_id=chat_id, photo=_blur_cache[original_file_id],
                caption=caption, parse_mode=ParseMode.HTML
            )
        else:
            file_info  = await bot.get_file(original_file_id)
            file_bytes = await bot.download_file(file_info.file_path)
            blurred_bytes = _blur_image_bytes(file_bytes.getvalue())
            photo_input = BufferedInputFile(blurred_bytes, filename="gcard_blur.jpg")
            msg = await bot.send_photo(
                chat_id=chat_id, photo=photo_input,
                caption=caption, parse_mode=ParseMode.HTML
            )
            _blur_cache[original_file_id] = msg.photo[-1].file_id
    except Exception as e:
        await message.reply(f"❌ Failed to start round: {e}", parse_mode=ParseMode.HTML)
        return

    active_gcard[cid_str] = {"card_id": card_id, "time": time.time(), "message_id": msg.message_id}
    _gcard_chat_cooldown[cid_str] = time.time()
    asyncio.create_task(_expire_gcard(cid_str, msg.message_id, chat_id))


@main_router.message(Command("gguess"))
async def gguess_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        return

    chat_id = message.chat.id
    cid_str = str(chat_id)

    if cid_str not in active_gcard:
        return
    if not command.args:
        await message.reply("⚠️ Provide the character name!\nFormat: <code>/gguess</code> [name]", parse_mode=ParseMode.HTML)
        return

    game_data  = active_gcard[cid_str]
    card_id    = game_data["card_id"]
    start_time = game_data["time"]
    msg_id     = game_data["message_id"]

    db = load_db()
    card_data = db["global_cards"].get(card_id)
    if not card_data:
        return

    target_name = card_data["name"].lower()
    query = command.args.lower().strip()

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
        await message.reply("🚫「 𝗪𝗥𝗢𝗡𝗚 𝗚𝗨𝗘𝗦𝗦 ぁ 」\n\n➜ 𝗧𝗿𝘆 𝗔𝗴𝗮𝗶𝗻", parse_mode=ParseMode.HTML)
        return

    time_taken = round(time.time() - start_time, 2)
    del active_gcard[cid_str]

    try:
        await bot.set_message_reaction(
            chat_id=chat_id, message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji="🎉")]
        )
    except Exception:
        pass

    user_id = str(uid_int)
    name    = message.from_user.first_name
    display_rarity = format_rarity(card_data["rarity"])

    reveal_caption = (
        "<b>「 🎊 GUESSED CORRECTLY ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🎊 <b><i>{get_mention(user_id, name)}</i></b> guessed it in <b>{time_taken}s</b>!\n\n"
        f"👤 Character ➜ <b>{card_data['name']} 《{display_rarity}》</b>\n"
        f"📺 Anime    ➜ <b>{card_data['anime']}</b>"
    )
    await _reveal_gcard(chat_id, msg_id, card_data.get("file_id"), reveal_caption)

