import math
import difflib
import unicodedata
from aiogram import F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, WebAppInfo
)
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode, ChatMemberStatus

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from config import (
    bot, main_router, DECK_PER_PAGE, RARITY_ORDER,
    format_rarity, ensure_user, load_db, save_db, is_ghost_banned, is_shadow_banned
)
from handlers import smart_reply, smart_reply_photo, _check_action_cooldown
from vlog import log_action

# ==========================================
# NETLIFY WEB APP URL
# ==========================================
WEB_APP_DECK_URL = "https://lucky-kitten-a44721.netlify.app/deck.html"

# ==========================================
# FASTAPI WEB APP API ROUTER (/api/deck)
# ==========================================
deck_api = APIRouter(prefix="/api/deck", tags=["Deck"])

class BurnRequest(BaseModel):
    user_id: str
    card_id: str

class SpecialRequest(BaseModel):
    user_id: str
    card_id: str

@deck_api.get("/state/{user_id}")
async def get_deck_state(user_id: str):
    db = load_db()
    user_data = db.get("users", {}).get(user_id)
    if not user_data:
        ensure_user(user_id, "User", None)
        db = load_db()
        user_data = db["users"][user_id]

    cards = user_data.get("cards", {})
    global_cards = db.get("global_cards", {})
    
    enriched_cards = []
    for cid, cdata in cards.items():
        g_info = global_cards.get(cid, {})
        enriched_cards.append({
            "id": cid,
            "name": cdata.get("name", "Unknown"),
            "rarity": format_rarity(cdata.get("rarity", "Common")),
            "amount": cdata.get("amount", 1),
            "anime": g_info.get("anime", "Unknown"),
            "file_id": g_info.get("file_id", None)
        })

    return {
        "user_id": user_id,
        "name": user_data.get("name", "User"),
        "balance": user_data.get("nexus_shards", 0),
        "special_card": user_data.get("special_card"),
        "cards": enriched_cards
    }

@deck_api.post("/burn")
async def api_burn_card(req: BurnRequest):
    db = load_db()
    user_cards = db.get("users", {}).get(req.user_id, {}).get("cards", {})

    if req.card_id not in user_cards or user_cards[req.card_id]["amount"] <= 0:
        raise HTTPException(status_code=400, detail="Card not owned.")

    card_data = user_cards[req.card_id]
    rarity_normalized = format_rarity(card_data["rarity"])

    burn_payout = 150
    if rarity_normalized == "Elite ⚓": burn_payout = 450
    elif rarity_normalized == "Divine ❄️": burn_payout = 1800

    user_cards[req.card_id]["amount"] -= 1
    if user_cards[req.card_id]["amount"] <= 0:
        del user_cards[req.card_id]
        if db["users"][req.user_id].get("special_card") == req.card_id:
            db["users"][req.user_id]["special_card"] = None

    db["users"][req.user_id]["nexus_shards"] = db["users"][req.user_id].get("nexus_shards", 0) + burn_payout

    log_action(db, req.user_id, {
        "type": "web_burn",
        "card_name": card_data["name"],
        "rarity": rarity_normalized,
        "shards_earned": burn_payout
    })
    save_db()

    return {
        "success": True,
        "burned_card": card_data["name"],
        "shards_earned": burn_payout,
        "new_balance": db["users"][req.user_id]["nexus_shards"]
    }

@deck_api.post("/special")
async def api_set_special(req: SpecialRequest):
    db = load_db()
    user_cards = db.get("users", {}).get(req.user_id, {}).get("cards", {})

    if req.card_id not in user_cards:
        raise HTTPException(status_code=400, detail="Card not owned.")

    db["users"][req.user_id]["special_card"] = req.card_id
    save_db()

    return {"success": True, "special_card": req.card_id}


# ==========================================
# /webdeck COMMAND (OPEN NETLIFY WEB APP)
# ==========================================
@main_router.message(Command("webdeck"))
async def open_web_deck_cmd(message: Message):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎴 Open Card Deck Web", web_app=WebAppInfo(url=WEB_APP_DECK_URL))]
    ])

    await smart_reply(
        message,
        "<b>「 🎴 CARDS COLLECTION WEB 」</b>\n━━━━━━━━━━━━━━━━━\n"
        "Explore your anime card deck in 3D, inspect stats, filter by anime/rarity, and recycle duplicate cards for <b>Nexus Shards 💠</b>!",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


# ==========================================
# DECK DISPLAY LAYER (/deck)
# ==========================================
_ZERO_WIDTH_CHARS = {
    "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\ufeff",
    "\u2060", "\u2061", "\u2062", "\u2063", "\u2064",
}

def sanitize_display_name(name: str, max_len: int = 24) -> str:
    """Strips zero-width/invisible characters and Unicode combining marks
    which otherwise break message layout wherever a display name gets rendered."""
    if not name:
        return "User"
    cleaned = "".join(ch for ch in str(name) if ch not in _ZERO_WIDTH_CHARS)
    cleaned = "".join(ch for ch in cleaned if unicodedata.category(ch) not in ("Mn", "Mc", "Me"))
    cleaned = cleaned.strip()
    return cleaned[:max_len] if cleaned else "User"


async def send_deck_page(message, db: dict, user_id: str, page=0, edit=False, mult=1):
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

    safe_name = sanitize_display_name(user_name)
    safe_name = safe_name.replace("<", "&lt;").replace(">", "&gt;")
    name_link = f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    text = f"『 𝗖𝗔𝗥𝗗 𝗗𝗘𝗖𝗞 - {name_link} 』\n━━━━━━━━━━━━━━━━━\n\n"

    anime_owned_count = {}
    for _, _, a in enriched:
        anime_owned_count[a] = anime_owned_count.get(a, 0) + 1

    anime_total_count = {}
    for cdata in global_cards.values():
        a = cdata.get("anime", "Unknown")
        anime_total_count[a] = anime_total_count.get(a, 0) + 1

    current_anime = None
    for cid, cdata, anime in page_items:
        if anime != current_anime:
            if current_anime is not None: text += "\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n\n"
            obtained = anime_owned_count.get(anime, 0)
            total    = anime_total_count.get(anime, 0)
            text += f"𝗔𝗻𝗶𝗺𝗲  - <b>{anime} ↧</b>  ({obtained}/{total})\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
            current_anime = anime
            
        disp_rarity = format_rarity(cdata["rarity"])
        
        if cid == special_card_id:
            text += f"✨ <b><i><code>{cdata['name']}</code></i> - [{disp_rarity}]  ×{cdata['amount']} </b>\n"
        else:
            text += f"✦ <b><i><code>{cdata['name']}</code></i> - [{disp_rarity}]  ×{cdata['amount']} </b>\n"

    if current_anime is not None: text += "\n﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"

    MAX_MULT  = min(25, max(2, total_pages // 3))
    show_fast = total_pages > 3
    mult      = max(1, min(mult, MAX_MULT)) if show_fast else 1

    has_prev  = page > 0
    has_next  = end < len(enriched)
    prev_page = max(0, page - mult)
    next_page = min(total_pages - 1, page + mult)

    prev_label = f"❮ {mult}x" if mult > 1 else "❮"
    next_label = f"x{mult} ❯" if mult > 1 else "❯"

    prev_btn = InlineKeyboardButton(
        text=prev_label,
        callback_data=f"deck_prev_{user_id}_{prev_page}_{mult}" if has_prev else "dedge_prev"
    )
    next_btn = InlineKeyboardButton(
        text=next_label,
        callback_data=f"deck_next_{user_id}_{next_page}_{mult}" if has_next else "dedge_next"
    )

    nav_buttons = [prev_btn]
    if show_fast:
        nav_buttons.append(InlineKeyboardButton(text="Fast ⏩", callback_data=f"deck_fast_{user_id}_{page}_{mult}"))
    nav_buttons.append(next_btn)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⌈ 𝗣𝗮𝗴𝗲 {page+1}/{total_pages} ⌋", callback_data=f"page_alert_{page+1}")],
        nav_buttons,
        [InlineKeyboardButton(text="View Collection 🫧", switch_inline_query_current_chat=f"card_user.{user_id}")],
        [InlineKeyboardButton(text="🗑️", callback_data=f"deckdel_{user_id}")]
    ])

    caption_too_long = len(text) > 1000

    if display_pic and not caption_too_long:
        if edit and isinstance(message, CallbackQuery):
            try:
                await message.message.edit_media(InputMediaPhoto(media=display_pic, caption=text, parse_mode=ParseMode.HTML), reply_markup=keyboard)
            except Exception as e:
                print(f"[deck] edit_media failed, falling back to text: {e}")
                try:
                    await message.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
                except Exception as e2:
                    print(f"[deck] text fallback also failed: {e2}")
        else:
            target = message.message if isinstance(message, CallbackQuery) else message
            await smart_reply_photo(target, photo=display_pic, caption=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        if edit and isinstance(message, CallbackQuery):
            try:
                await message.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            except Exception as e:
                print(f"[deck] edit_text failed: {e}")
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
    mult = int(parts[4]) if len(parts) > 4 else 1

    if str(callback_query.from_user.id) != owner_id:
        await callback_query.answer("Not your deck!", show_alert=True)
        return

    db = load_db()

    if direction == "fast":
        cards_count = len(db["users"].get(owner_id, {}).get("cards", {}))
        total_pages = max(1, math.ceil(cards_count / DECK_PER_PAGE))
        max_mult    = min(25, max(2, total_pages // 3))

        if mult >= max_mult:
            new_mult = 1
        else:
            new_mult = mult * 2 if mult >= 1 else 2
        await send_deck_page(callback_query, db, owner_id, int(page_str), edit=True, mult=new_mult)
        await callback_query.answer(f"Speed changed to {new_mult}x", show_alert=True)
        return

    await send_deck_page(callback_query, db, owner_id, int(page_str), edit=True, mult=mult)
    await callback_query.answer()


@main_router.callback_query(F.data.in_({"dedge_prev", "dedge_next"}))
async def deck_edge_cb(callback_query: CallbackQuery):
    await callback_query.answer("No more pages.", show_alert=False)


@main_router.callback_query(F.data.startswith("deckdel_"))
async def deck_delete_cb(callback_query: CallbackQuery):
    uid_int = callback_query.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        await callback_query.answer("🔇 You are currently restricted.", show_alert=True)
        return

    owner_id = callback_query.data.split("deckdel_", 1)[1]
    if str(uid_int) != owner_id:
        await callback_query.answer("Only the deck owner can delete this.", show_alert=True)
        return

    try:
        await callback_query.message.delete()
    except Exception:
        pass
    await callback_query.answer()


@main_router.callback_query(F.data.startswith("page_alert_"))
async def page_indicator_alert(callback_query: CallbackQuery):
    page_num = callback_query.data.split("_")[2]
    await callback_query.answer(f"ℹ️ You are currently on page {page_num}.", show_alert=True)


@main_router.callback_query(F.data == "noop")
async def noop_cb(callback_query: CallbackQuery):
    await callback_query.answer()

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
        [InlineKeyboardButton(text="Cancel", callback_data=f"cancel_action_{user_id}")]
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
# /flex SHOWCASE COMMAND
# ==========================================
@main_router.message(Command("flex"))
async def flex_cmd(message: Message, command: CommandObject):
    uid_int = message.from_user.id
    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int): return

    user_id = str(uid_int)
    db      = ensure_user(user_id, message.from_user.first_name, message.from_user.username)

    if not command.args:
        await message.reply("⚠️ <b>Usage:</b> <code>/flex &lt;card name&gt;</code>", parse_mode=ParseMode.HTML)
        return

    query    = command.args.lower().strip()
    my_cards = db["users"][user_id].get("cards", {})

    if not my_cards:
        await message.reply("You don't own any cards to flex!", parse_mode=ParseMode.HTML)
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

    safe_name = str(message.from_user.first_name).replace("<", "&lt;").replace(">", "&gt;")
    mention = f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    
    caption = (
        f"<i><b>Ooooh! Check out {mention}'s card!</b></i>\n\n"
        f"<b>⦿ <i>Character </i>» {matched_data['name']} ⟪ {global_data.get('anime', 'Unknown')} ⟫ \n"
        f"⦾ <i>Rarity </i>» {display_rarity}\n"
        f"⬤ <i>Owned</i>  » x{matched_data['amount']}</b>"
    )

    try:
        await message.reply_photo(
            photo=global_data.get("file_id"),
            caption=caption,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await message.reply(caption, parse_mode=ParseMode.HTML)

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
        [InlineKeyboardButton(text="Cancel", callback_data=f"cancel_action_{user_id}")]
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

    log_action(db, uid, {
        "type": "burn", "card_name": card_data["name"], "rarity": rarity_normalized,
        "shards_earned": burn_payout,
        "chat_id": cq.message.chat.id, "chat_title": cq.message.chat.title or "Private DM",
    })
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