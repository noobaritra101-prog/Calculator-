import time
import uuid
import random
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode

import config
from config import (
    bot, main_router, load_db, save_db, ensure_user, 
    format_rarity, SHOP_PRICES, OFFLINE_STORE_GROUP
)

# ==========================================
# PRIVACY CHECK HELPER
# ==========================================
async def verify_user(cq: CallbackQuery, target_id: str) -> bool:
    """Ensures only the user who executed the command can use the buttons."""
    if str(cq.from_user.id) != str(target_id):
        await cq.answer("❌ This menu is not for you!", show_alert=True)
        return False
    return True

# ==========================================
# NEXUS MARKETPLACE (/store)
# ==========================================
@main_router.message(Command("store"))
async def store_cmd(message: Message):
    uid = str(message.from_user.id)
    ensure_user(uid, message.from_user.first_name, message.from_user.username)
    
    text = (
        "<b>「 🏪 NEXUS MARKETPLACE ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Choose a marketplace to browse:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Online Store", callback_data=f"st_on_{uid}")],
        [InlineKeyboardButton(text="🛍️ Manage Offline Store", callback_data=f"st_off_{uid}")],
        [InlineKeyboardButton(text="🛍️ Oϝϝʅιɳҽ Sƚσɾҽ", url="https://t.me/nexus_offstore")]
    ])
    
    db = load_db()
    pic = db.get("settings", {}).get("pic_store")
    if pic: 
        await message.reply_photo(photo=pic, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else: 
        await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data.startswith("st_main_"))
async def store_main_cb(cq: CallbackQuery):
    uid = cq.data.split("_")[2]
    if not await verify_user(cq, uid): return

    text = (
        "<b>「 🏪 NEXUS MARKETPLACE ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Choose a marketplace to browse:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Online Store", callback_data=f"st_on_{uid}")],
        [InlineKeyboardButton(text="🛍️ Manage Offline Store", callback_data=f"st_off_{uid}")],
        [InlineKeyboardButton(text="🛍️ Oϝϝʅιɳҽ Sƚσɾҽ", url="https://t.me/nexus_offstore")]
    ])
    
    db = load_db()
    pic = db.get("settings", {}).get("pic_store")
    
    try:
        if pic:
            await cq.message.edit_media(InputMediaPhoto(media=pic, caption=text, parse_mode=ParseMode.HTML), reply_markup=kb)
        else:
            if cq.message.photo:
                await cq.message.edit_caption(caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cq.answer()


@main_router.callback_query(F.data.startswith("st_on_"))
async def store_online_cb(cq: CallbackQuery):
    uid = cq.data.split("_")[2]
    if not await verify_user(cq, uid): return

    db = ensure_user(uid, cq.from_user.first_name, cq.from_user.username)
    
    today = config.get_shop_rotation_seed()
    dp = db["users"][uid].setdefault("daily_purchases", {})
    
    if dp.get("date") != today:
        db["users"][uid]["daily_purchases"] = {
            "date": today,
            "bought": [],
            "free_refreshes_used": 0,
            "paid_refreshes_used": 0,
            "refresh_seed_offset": 0
        }
        save_db()
        dp = db["users"][uid]["daily_purchases"]
        
    bought_list = dp.setdefault("bought", [])
    offset = dp.setdefault("refresh_seed_offset", 0)
    
    # Generate unique stock seed based on offset
    seed = f"{today}_{uid}_{offset}"
    random.seed(seed)

    basics = {k: v for k, v in db["global_cards"].items() if format_rarity(v["rarity"]) == "Basic 🃏"}
    elites = {k: v for k, v in db["global_cards"].items() if format_rarity(v["rarity"]) == "Elite ⚓"}
    divines = {k: v for k, v in db["global_cards"].items() if format_rarity(v["rarity"]) == "Divine ❄️"}

    if not basics or not elites or not divines:
        random.seed()
        await cq.answer("⚠️ Store is resting. Not enough cards in the global database.", show_alert=True)
        return

    c_b = random.choice(list(basics.items()))
    c_e = random.choice(list(elites.items()))
    c_d = random.choice(list(divines.items()))
    random.seed()

    text = (
        "<b>「 🛒 ONLINE STORE ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<i>Your personalized daily stock. Resets at midnight UTC.</i>\n\n"
        f"🃏 <b>{c_b[1]['name']}</b> ➜ {SHOP_PRICES['Basic 🃏']} 💠\n"
        f"⚓ <b>{c_e[1]['name']}</b> ➜ {SHOP_PRICES['Elite ⚓']} 💠\n"
        f"❄️ <b>{c_d[1]['name']}</b> ➜ {SHOP_PRICES['Divine ❄️']} 💠\n"
        "━━━━━━━━━━━━━━━━━"
    )

    btn_b = InlineKeyboardButton(text=f"Buy {c_b[1]['name']}", callback_data=f"buyon_{uid}_{c_b[0]}") if c_b[0] not in bought_list else InlineKeyboardButton(text="❌ Sold Out (Basic)", callback_data="noop")
    btn_e = InlineKeyboardButton(text=f"Buy {c_e[1]['name']}", callback_data=f"buyon_{uid}_{c_e[0]}") if c_e[0] not in bought_list else InlineKeyboardButton(text="❌ Sold Out (Elite)", callback_data="noop")
    btn_d = InlineKeyboardButton(text=f"Buy {c_d[1]['name']}", callback_data=f"buyon_{uid}_{c_d[0]}") if c_d[0] not in bought_list else InlineKeyboardButton(text="❌ Sold Out (Divine)", callback_data="noop")

    # Refresh Row Logic
    refresh_buttons = []
    free_used = dp.setdefault("free_refreshes_used", 0)
    paid_used = dp.setdefault("paid_refreshes_used", 0)

    if free_used < 1:
        refresh_buttons.append(InlineKeyboardButton(text="🔄 Free Refresh", callback_data=f"stonref_free_{uid}"))
    elif paid_used < 1:
        refresh_buttons.append(InlineKeyboardButton(text="🔄 Refresh (200 Shards 💠)", callback_data=f"stonref_paid_{uid}"))

    kb_list = [
        [btn_b], 
        [btn_e], 
        [btn_d]
    ]
    if refresh_buttons:
        kb_list.append(refresh_buttons)
    kb_list.append([InlineKeyboardButton(text="◀️ Back", callback_data=f"st_main_{uid}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
    pic = db.get("settings", {}).get("pic_online_store")
    
    try:
        if pic:
            await cq.message.edit_media(InputMediaPhoto(media=pic, caption=text, parse_mode=ParseMode.HTML), reply_markup=kb)
        else:
            if cq.message.photo:
                await cq.message.edit_caption(caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cq.answer()


@main_router.callback_query(F.data.startswith("stonref_"))
async def online_store_refresh_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    ref_type = parts[1]
    uid = parts[2]
    if not await verify_user(cq, uid): return

    db = load_db()
    user_data = db["users"][uid]
    dp = user_data.setdefault("daily_purchases", {})
    
    today = config.get_shop_rotation_seed()
    if dp.get("date") != today:
        dp["date"] = today
        dp["bought"] = []
        dp["free_refreshes_used"] = 0
        dp["paid_refreshes_used"] = 0
        dp["refresh_seed_offset"] = 0

    if ref_type == "free":
        if dp.setdefault("free_refreshes_used", 0) >= 1:
            await cq.answer("❌ Free refresh already claimed!", show_alert=True)
            return
        dp["free_refreshes_used"] = 1
        dp["refresh_seed_offset"] = dp.get("refresh_seed_offset", 0) + 1
        dp["bought"] = []
        save_db()
        await cq.answer("🔄 Store refreshed successfully!", show_alert=True)
        
    elif ref_type == "paid":
        if dp.setdefault("free_refreshes_used", 0) < 1:
            await cq.answer("💡 Please use your Free Refresh first!", show_alert=True)
            return
        if dp.setdefault("paid_refreshes_used", 0) >= 1:
            await cq.answer("❌ Paid refresh already claimed!", show_alert=True)
            return
        if user_data.get("nexus_shards", 0) < 200:
            await cq.answer("❌ Insufficient Shards! You need 200 Shards 💠.", show_alert=True)
            return
        
        user_data["nexus_shards"] -= 200
        dp["paid_refreshes_used"] = 1
        dp["refresh_seed_offset"] = dp.get("refresh_seed_offset", 0) + 1
        dp["bought"] = []
        save_db()
        await cq.answer("🔄 Store refreshed! -200 Shards 💠", show_alert=True)

    await store_online_cb(cq)


@main_router.callback_query(F.data.startswith("buyon_"))
async def buy_online_confirm_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid, card_id = parts[1], parts[2]
    if not await verify_user(cq, uid): return

    db = load_db()
    if card_id not in db["global_cards"]:
        await cq.answer("❌ This card no longer exists.", show_alert=True)
        return

    card_data = db["global_cards"][card_id]
    rarity = format_rarity(card_data["rarity"])
    price = SHOP_PRICES.get(rarity, 99999)

    caption = (
        f"<b>「 PURCHASE CONFIRMATION 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Card:</b> {card_data['name']}\n"
        f"🌟 <b>Rarity:</b> {rarity}\n"
        f"💰 <b>Price:</b> {price} Shards 💠\n\n"
        f"<i>Do you wish to proceed with this purchase?</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Purchase", callback_data=f"cbon_{uid}_{card_id}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"st_on_{uid}")]
    ])
    
    try:
        if cq.message.photo:
            await cq.message.edit_media(InputMediaPhoto(media=card_data["file_id"], caption=caption, parse_mode=ParseMode.HTML, has_spoiler=True), reply_markup=kb)
        else:
            await cq.message.delete()
            await bot.send_photo(chat_id=cq.message.chat.id, photo=card_data["file_id"], caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML, has_spoiler=True)
    except Exception:
        pass
    await cq.answer()

@main_router.callback_query(F.data.startswith("cbon_"))
async def buy_online_execute_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid, card_id = parts[1], parts[2]
    if not await verify_user(cq, uid): return

    db = load_db()
    if card_id not in db["global_cards"]:
        await cq.answer("❌ This card no longer exists.", show_alert=True)
        return

    today = config.get_shop_rotation_seed()
    if db["users"][uid].setdefault("daily_purchases", {}).get("date") != today:
        db["users"][uid]["daily_purchases"] = {
            "date": today,
            "bought": [],
            "free_refreshes_used": 0,
            "paid_refreshes_used": 0,
            "refresh_seed_offset": 0
        }

    if card_id in db["users"][uid]["daily_purchases"].setdefault("bought", []):
        await cq.answer("❌ You already bought this card today!", show_alert=True)
        return

    card_data = db["global_cards"][card_id]
    rarity = format_rarity(card_data["rarity"])
    price = SHOP_PRICES.get(rarity, 99999)

    user_data = db["users"][uid]
    current_shards = user_data.get("nexus_shards", 0)

    if current_shards < price:
        await cq.answer(f"❌ Not enough Shards! You need {price} 💠.", show_alert=True)
        return

    db["users"][uid]["nexus_shards"] -= price
    
    if card_id not in db["users"][uid]["cards"]:
        db["users"][uid]["cards"][card_id] = {"name": card_data["name"], "rarity": card_data["rarity"], "amount": 0}
    db["users"][uid]["cards"][card_id]["amount"] += 1
    db["users"][uid]["total_claimed"] = db["users"][uid].get("total_claimed", 0) + 1
    
    db["users"][uid]["daily_purchases"]["bought"].append(card_id)
    save_db()
    
    success_text = (
        f"<b>「 PURCHASE COMPLETE ✅ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"You successfully bought <b>{card_data['name']}</b> for {price} Shards!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Back to Store", callback_data=f"st_on_{uid}")]])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=success_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(success_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception: pass
    await cq.answer(f"✅ Purchased {card_data['name']}!", show_alert=True)

# ==========================================
# OFFLINE STORE CONSIGNMENT (/sell & Mgmt)
# ==========================================
@main_router.message(Command("sell"))
async def sell_cmd(message: Message, command: CommandObject):
    if not command.args:
        await message.reply("⚠️ <b>Usage:</b> <code>/sell <card name> <price></code>\nExample: <code>/sell goku 500</code>", parse_mode=ParseMode.HTML)
        return

    parts = command.args.rsplit(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("⚠️ Invalid format. Make sure you specify the price at the end.\nExample: <code>/sell naruto 250</code>", parse_mode=ParseMode.HTML)
        return

    query = parts[0].lower().strip()
    price = int(parts[1])

    if price < 1:
        await message.reply("❌ Price must be at least 1 Shard.", parse_mode=ParseMode.HTML)
        return

    user_id = str(message.from_user.id)
    db = ensure_user(user_id, message.from_user.first_name, message.from_user.username)
    my_cards = db["users"][user_id].get("cards", {})

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

    if not best_match:
        await message.reply(f"❌ You do not own a card matching <b>{parts[0]}</b>.", parse_mode=ParseMode.HTML)
        return

    matched_cid, matched_data = best_match
    global_data = db["global_cards"].get(matched_cid, {})
    
    caption = (
        f"<b>「 SELL CONFIRMATION ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 Character ➜ <b>{matched_data['name']}</b>\n"
        f"🌟 Rarity    ➜ {format_rarity(matched_data['rarity'])}\n"
        f"💰 Price     ➜ <b>{price} 💠</b>\n\n"
        f"<i>By confirming, this card will be removed from your deck and sent to the Offline Store group.</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Listing", callback_data=f"listsell_{user_id}_{matched_cid}_{price}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")]
    ])
    await message.reply_photo(photo=global_data.get("file_id"), caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML, has_spoiler=True)

@main_router.callback_query(F.data.startswith("listsell_"))
async def confirm_sell_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid, card_id, price = parts[1], parts[2], int(parts[3])
    if not await verify_user(cq, uid): return

    db = load_db()
    my_cards = db["users"].get(uid, {}).get("cards", {})

    if card_id not in my_cards or my_cards[card_id]["amount"] <= 0:
        await cq.answer("❌ You don't own this card anymore!", show_alert=True)
        return

    my_cards[card_id]["amount"] -= 1
    if my_cards[card_id]["amount"] <= 0:
        del my_cards[card_id]
        if db["users"][uid].get("special_card") == card_id:
            db["users"][uid]["special_card"] = None

    listing_id = str(uuid.uuid4())[:8]
    bot_info = await bot.get_me()
    deep_link = f"https://t.me/{bot_info.username}?start=buy_{listing_id}"

    global_data = db["global_cards"][card_id]
    seller_name = db["users"][uid]["name"]
    
    post_text = (
        f"<b>「 🛍️ OFFLINE STORE LISTING 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Card:</b> {global_data['name']}\n"
        f"🌟 <b>Rarity:</b> {format_rarity(global_data['rarity'])}\n"
        f"📺 <b>Anime:</b> {global_data['anime']}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🏷️ <b>Seller:</b> {seller_name}\n"
        f"💰 <b>Price:</b> {price} Shards 💠"
    )

    group_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🛒 Buy for {price} 💠", url=deep_link)]
    ])

    try:
        msg = await bot.send_photo(
            chat_id=OFFLINE_STORE_GROUP,
            photo=global_data["file_id"],
            caption=post_text,
            reply_markup=group_kb,
            parse_mode=ParseMode.HTML,
            has_spoiler=True
        )
        
        db["offline_store"][listing_id] = {
            "seller_id": uid,
            "card_id": card_id,
            "price": price,
            "msg_id": msg.message_id
        }
        save_db()

        try:
            if cq.message.photo:
                await cq.message.edit_caption(caption="✅ <b>Listing created successfully!</b>\nYour card has been moved to the Offline Store.", reply_markup=None, parse_mode=ParseMode.HTML)
            else:
                await cq.message.edit_text("✅ <b>Listing created successfully!</b>\nYour card has been moved to the Offline Store.", reply_markup=None, parse_mode=ParseMode.HTML)
        except Exception: pass
        await cq.answer()
        
    except Exception as e:
        if card_id not in my_cards:
            my_cards[card_id] = {"name": global_data["name"], "rarity": global_data["rarity"], "amount": 0}
        my_cards[card_id]["amount"] += 1
        save_db()
        await cq.answer(f"❌ Failed to list in group: {e}", show_alert=True)

@main_router.callback_query(F.data.startswith("st_off_"))
async def offline_listings_mgr(cq: CallbackQuery):
    uid = cq.data.split("_")[2]
    if not await verify_user(cq, uid): return

    db = load_db()
    my_listings = {lid: data for lid, data in db.get("offline_store", {}).items() if data["seller_id"] == uid}
    
    if not my_listings:
        text = "<b>「 🛍️ OFFLINE STORE ぁ 」</b>\n━━━━━━━━━━━━━━━━━\nYou currently have no active listings.\nUse <code>/sell &lt;card&gt; &lt;price&gt;</code> to list an item."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛍️ Oϝϝʅιɳҽ Sƚσɾҽ", url="https://t.me/nexus_offstore")],
            [InlineKeyboardButton(text="◀️ Back", callback_data=f"st_main_{uid}")]
        ])
    else:
        text = "<b>「 🛍️ MY LISTINGS ぁ 」</b>\n━━━━━━━━━━━━━━━━━\nSelect a listing to remove it and retrieve your card:\n\n"
        buttons = []
        for lid, data in my_listings.items():
            card_name = db["global_cards"].get(data["card_id"], {}).get("name", "Unknown")
            btn_text = f"❌ Remove {card_name} ({data['price']} 💠)"
            buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"rm_list_{uid}_{lid}")])
            
        buttons.append([InlineKeyboardButton(text="🛍️ Oϝϝʅιɳҽ Sƚσɾҽ", url="https://t.me/nexus_offstore")])
        buttons.append([InlineKeyboardButton(text="◀️ Back", callback_data=f"st_main_{uid}")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    pic = db.get("settings", {}).get("pic_offline_store")
    
    try:
        if pic:
            await cq.message.edit_media(InputMediaPhoto(media=pic, caption=text, parse_mode=ParseMode.HTML), reply_markup=kb)
        else:
            if cq.message.photo:
                await cq.message.edit_caption(caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await cq.answer()

@main_router.callback_query(F.data.startswith("rm_list_"))
async def remove_listing_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid, lid = parts[2], parts[3]
    if not await verify_user(cq, uid): return

    db = load_db()
    if lid not in db.get("offline_store", {}):
        await cq.answer("❌ Listing not found or already sold.", show_alert=True)
        return
        
    listing = db["offline_store"][lid]
    
    card_id = listing["card_id"]
    global_card = db["global_cards"].get(card_id)
    
    my_cards = db["users"][uid].setdefault("cards", {})
    if card_id not in my_cards:
        my_cards[card_id] = {"name": global_card["name"], "rarity": global_card["rarity"], "amount": 0}
    my_cards[card_id]["amount"] += 1
    
    try:
        await bot.delete_message(OFFLINE_STORE_GROUP, listing["msg_id"])
    except Exception: pass
    
    del db["offline_store"][lid]
    save_db()
    
    await cq.answer("✅ Listing removed! The card was returned to your deck.", show_alert=True)
    cq.data = f"st_off_{uid}"
    await offline_listings_mgr(cq)

@main_router.callback_query(F.data.startswith("buyoff_"))
async def execute_offline_buy_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid, lid = parts[1], parts[2]
    if not await verify_user(cq, uid): return

    db = ensure_user(uid, cq.from_user.first_name, cq.from_user.username)
    
    if lid not in db.get("offline_store", {}):
        try: await cq.message.edit_caption(caption="❌ This listing is no longer available.", reply_markup=None)
        except Exception: pass
        await cq.answer("❌ Listing sold or removed.", show_alert=True)
        return

    listing = db["offline_store"][lid]
    price = listing["price"]
    seller_id = listing["seller_id"]
    card_id = listing["card_id"]

    buyer_data = db["users"].get(uid, {})
    if buyer_data.get("nexus_shards", 0) < price:
        await cq.answer("❌ You don't have enough Shards to complete this transaction.", show_alert=True)
        return

    db["users"][uid]["nexus_shards"] -= price
    
    if seller_id in db["users"]:
        db["users"][seller_id]["nexus_shards"] = db["users"][seller_id].get("nexus_shards", 0) + price
    else:
        db["users"][seller_id] = {
            "name": "Unknown",
            "nexus_shards": price,
            "cards": {},
            "joined": int(time.time()),
            "stocks": {},
            "daily_purchases": {"date": "", "bought": []}
        }

    buyer_cards = db["users"][uid].setdefault("cards", {})
    global_card = db["global_cards"][card_id]
    
    if card_id not in buyer_cards:
        buyer_cards[card_id] = {"name": global_card["name"], "rarity": global_card["rarity"], "amount": 0}
    buyer_cards[card_id]["amount"] += 1

    del db["offline_store"][lid]
    save_db()

    buyer_name = db["users"][uid].get("name", "User")
    
    try:
        sold_text = (
            f"<b>「 🛍️ SOLD 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Card:</b> {global_card['name']}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"✅ <i>Purchased by {buyer_name} for {price} 💠</i>"
        )
        await bot.edit_message_caption(chat_id=OFFLINE_STORE_GROUP, message_id=listing["msg_id"], caption=sold_text, reply_markup=None, parse_mode=ParseMode.HTML)
    except Exception: pass

    try:
        seller_msg = (
            f"<b>「 🛍️ ITEM SOLD! 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🎉 Great news! Your offline listing has been sold.\n\n"
            f"👤 <b>Card:</b> {global_card['name']}\n"
            f"💰 <b>Earned:</b> +{price} Shards 💠\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<i>The shards have been successfully deposited into your account!</i>"
        )
        await bot.send_message(chat_id=int(seller_id), text=seller_msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        pass

    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=f"✅ <b>Purchase Complete!</b>\nYou bought <b>{global_card['name']}</b> for {price} Shards.", reply_markup=None, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(f"✅ <b>Purchase Complete!</b>\nYou bought <b>{global_card['name']}</b> for {price} Shards.", reply_markup=None, parse_mode=ParseMode.HTML)
    except Exception: pass
    await cq.answer("✅ Purchase successful!", show_alert=True)