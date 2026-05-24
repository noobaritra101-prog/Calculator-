import time
import random
import asyncio
import io
from PIL import Image, ImageDraw
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, InputMediaPhoto
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

import config
from config import bot, main_router, load_db, save_db, ensure_user, STOCKS

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
# MARKET ENGINE (Runs every 2 hours)
# ==========================================
async def market_engine_loop():
    while True:
        await asyncio.sleep(config.MARKET_UPDATE_INTERVAL)
        db = load_db()
        market = db.setdefault("market", {})

        for sym, stock_info in STOCKS.items():
            if sym not in market:
                market[sym] = {"current_price": stock_info["base_price"], "history": [stock_info["base_price"]] * 12}
            
            old_price = market[sym]["current_price"]
            volatility = stock_info["volatility"]
            
            total_shares_held = sum(u.get("stocks", {}).get(sym, {}).get("shares", 0) for u in db["users"].values())
            player_influence = (total_shares_held * 0.0001)  
            
            rng_shift = random.uniform(-volatility, volatility)
            new_price = int(old_price + (old_price * rng_shift) + player_influence)
            
            if new_price < 5: new_price = 5
            if new_price > 50000: new_price = int(old_price * 0.9) 
            
            market[sym]["current_price"] = new_price
            market[sym]["history"].append(new_price)
            if len(market[sym]["history"]) > 24: 
                market[sym]["history"].pop(0)

        save_db()
        print(f"[MARKET] Engine updated stock prices at {time.strftime('%X')}")

# ==========================================
# PILLOW GRAPH GENERATOR
# ==========================================
def generate_stock_graph(symbol: str, history: list) -> io.BytesIO:
    width, height = 600, 300
    img = Image.new("RGB", (width, height), (20, 24, 30))
    draw = ImageDraw.Draw(img)
    
    color_up = (46, 204, 113)   
    color_down = (231, 76, 60)  
    grid_color = (40, 48, 60)
    
    is_up = history[-1] >= history[0] if len(history) > 1 else True
    line_color = color_up if is_up else color_down

    for i in range(0, width, 50): draw.line([(i, 0), (i, height)], fill=grid_color, width=1)
    for i in range(0, height, 50): draw.line([(0, i), (width, i)], fill=grid_color, width=1)

    if len(history) > 1:
        max_price = max(history) + 10
        min_price = min(history) - 10 if min(history) > 10 else 0
        price_range = max_price - min_price if max_price != min_price else 1
        
        x_step = width / (len(history) - 1)
        points = []
        
        for i, price in enumerate(history):
            x = i * x_step
            y = height - ((price - min_price) / price_range * height)
            points.append((x, y))
            
        draw.line(points, fill=line_color, width=4)
        for pt in points:
            draw.ellipse((pt[0]-4, pt[1]-4, pt[0]+4, pt[1]+4), fill=(255, 255, 255))

    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

# ==========================================
# STOCK MARKET UI HANDLERS
# ==========================================
@main_router.message(Command("stockmarket"))
async def stockmarket_cmd(message: Message):
    uid = str(message.from_user.id)
    ensure_user(uid, message.from_user.first_name, message.from_user.username)
    
    text = (
        "<b>「 📈 NEXUS STOCK EXCHANGE ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<b>📖 Market Guide for Beginners:</b>\n"
        "💠 <b>Stocks:</b> Buy shares of anime factions. Buy low, sell high.\n"
        "📊 <b>Volatility:</b> High volatility means higher risk, but massive potential profit.\n"
        "⏱️ <b>Updates:</b> Prices shift every 5 minutes based on RNG and player trading volume.\n"
        "🏦 <b>Fees:</b> The Exchange takes a 1.5% cut on all trades.\n"
        "━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Buy Stocks", callback_data=f"sm_bl_{uid}"),
         InlineKeyboardButton(text="💵 Sell Stocks", callback_data=f"sm_sl_{uid}")],
        [InlineKeyboardButton(text="💼 Your Portfolio", callback_data=f"sm_p_{uid}")],
        [InlineKeyboardButton(text="🔄 Refresh Market", callback_data=f"sm_m_{uid}")]
    ])
    
    db = load_db()
    pic = db.get("settings", {}).get("pic_stockmarket")
    
    if pic: await message.reply_photo(photo=pic, caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else: await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@main_router.callback_query(F.data.startswith("sm_m_"))
async def sm_main_cb(cq: CallbackQuery):
    uid = cq.data.split("_")[2]
    if not await verify_user(cq, uid): return

    text = (
        "<b>「 📈 NEXUS STOCK EXCHANGE ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<b>📖 Market Guide for Beginners:</b>\n"
        "💠 <b>Stocks:</b> Buy shares of anime factions. Buy low, sell high.\n"
        "📊 <b>Volatility:</b> High volatility means higher risk, but massive potential profit.\n"
        "⏱️ <b>Updates:</b> Prices shift every 5 minutes based on RNG and player trading volume.\n"
        "🏦 <b>Fees:</b> The Exchange takes a 1.5% cut on all trades.\n"
        "━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Buy Stocks", callback_data=f"sm_bl_{uid}"),
         InlineKeyboardButton(text="💵 Sell Stocks", callback_data=f"sm_sl_{uid}")],
        [InlineKeyboardButton(text="💼 Your Portfolio", callback_data=f"sm_p_{uid}")],
        [InlineKeyboardButton(text="🔄 Refresh Market", callback_data=f"sm_m_{uid}")]
    ])
    
    db = load_db()
    pic = db.get("settings", {}).get("pic_stockmarket")
    
    try:
        if pic:
            await cq.message.edit_media(InputMediaPhoto(media=pic, caption=text, parse_mode=ParseMode.HTML), reply_markup=kb)
        else:
            if cq.message.photo:
                await cq.message.edit_caption(caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await cq.answer("🔄 Market Refreshed!", show_alert=False)
    except TelegramBadRequest:
        await cq.answer("⏱️ Prices haven't changed yet.", show_alert=False)
    except Exception:
        pass

@main_router.callback_query(F.data.startswith("sm_bl_"))
async def sm_buy_list_cb(cq: CallbackQuery):
    uid = cq.data.split("_")[2]
    if not await verify_user(cq, uid): return

    db = load_db()
    market = db.get("market", {})
    
    text = "<b>「 🛒 MARKET LISTINGS 」</b>\n━━━━━━━━━━━━━━━━━\nSelect a stock to view its graph and buy options:\n\n"
    buttons = []
    row = []
    
    for idx, (sym, data) in enumerate(STOCKS.items(), start=1):
        price = market.get(sym, {}).get("current_price", data["base_price"])
        history = market.get(sym, {}).get("history", [price])
        
        old_price = history[0] if history else price
        diff = price - old_price
        pct = (diff / old_price * 100) if old_price > 0 else 0
        
        emoji = "📈" if diff >= 0 else "📉"
        sign = "+" if diff > 0 else ""
        pct_str = f" ({sign}{int(pct)}%)" if diff != 0 else " (0%)"
        
        text += f"<b>{idx})</b> {emoji} {data['name']} ({sym}) - {price} 💠{pct_str}\n"
        row.append(InlineKeyboardButton(text=str(idx), callback_data=f"sm_v_{uid}_{sym}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
            
    if row: buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="🔄 Refresh", callback_data=f"sm_bl_{uid}"),
        InlineKeyboardButton(text="❮ Back", callback_data=f"sm_m_{uid}")
    ])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)
        await cq.answer()
    except TelegramBadRequest:
        await cq.answer("⏱️ Prices haven't changed yet.", show_alert=False)
    except Exception: pass

@main_router.callback_query(F.data.startswith("sm_v_"))
async def sm_view_stock_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid, sym = parts[2], parts[3]
    if not await verify_user(cq, uid): return

    db = load_db()
    market = db.get("market", {}).get(sym, {})
    
    stock = STOCKS[sym]
    price = market.get("current_price", stock["base_price"])
    history = market.get("history", [price])
    
    trend = "📈 UP" if len(history) > 1 and history[-1] >= history[0] else "📉 DOWN"
    
    vol = stock["volatility"]
    if vol <= 0.08: risk_level = "🟢 Low Risk (Stable)"
    elif vol <= 0.15: risk_level = "🟡 Medium Risk (Moderate)"
    elif vol <= 0.25: risk_level = "🟠 High Risk (Volatile)"
    else: risk_level = "🔴 Extreme Risk (Gamble)"
    
    caption = (
        f"<b>「 {stock['name']} ({sym}) 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Current Price:</b> {price} 💠\n"
        f"📊 <b>24h Trend:</b> {trend}\n"
        f"⚠️ <b>Asset Class:</b> {risk_level}\n\n"
        f"<i>Fee: 1.5% will be added to your purchase.</i>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Buy x1", callback_data=f"sm_cb_{uid}_{sym}_1"),
         InlineKeyboardButton(text="Buy x5", callback_data=f"sm_cb_{uid}_{sym}_5"),
         InlineKeyboardButton(text="Buy x10", callback_data=f"sm_cb_{uid}_{sym}_10")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data=f"sm_v_{uid}_{sym}"),
         InlineKeyboardButton(text="❮ Back to Market", callback_data=f"sm_bl_{uid}")]
    ])
    
    graph_bytes = generate_stock_graph(sym, history)
    photo = BufferedInputFile(graph_bytes.read(), filename=f"{sym}_graph.png")
    
    try:
        if cq.message.photo:
            await cq.message.edit_media(InputMediaPhoto(media=photo, caption=caption, parse_mode=ParseMode.HTML), reply_markup=kb)
        else:
            await cq.message.delete()
            await cq.message.answer_photo(photo=photo, caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML)
        await cq.answer("🔄 Graph refreshed!", show_alert=False)
    except TelegramBadRequest:
        await cq.answer("⏱️ No market changes yet.", show_alert=False)
    except Exception:
        pass

# ==========================================
# CONFIRM & EXECUTE BUY
# ==========================================
@main_router.callback_query(F.data.startswith("sm_cb_"))
async def sm_confirm_buy_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    if len(parts) != 5: return
    uid, sym, amount = parts[2], parts[3], int(parts[4])
    if not await verify_user(cq, uid): return
    
    db = load_db()
    price = db.get("market", {}).get(sym, {}).get("current_price", STOCKS[sym]["base_price"])
    base_cost = price * amount
    fee = int(base_cost * config.MARKET_FEE_PCT)
    total_cost = base_cost + fee
    
    caption = (
        f"<b>「 CONFIRM PURCHASE ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>Stock:</b> {STOCKS[sym]['name']} ({sym})\n"
        f"📦 <b>Amount:</b> {amount} shares\n"
        f"💵 <b>Base Cost:</b> {base_cost} 💠\n"
        f"🏦 <b>Broker Fee (1.5%):</b> {fee} 💠\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Total Deducted:</b> {total_cost} 💠\n\n"
        f"<i>Do you want to proceed?</i>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Buy", callback_data=f"sm_xb_{uid}_{sym}_{amount}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"sm_v_{uid}_{sym}")]
    ])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(caption, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception: pass
    await cq.answer()

@main_router.callback_query(F.data.startswith("sm_xb_"))
async def sm_execute_buy_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    if len(parts) != 5: return
    uid, sym, amount = parts[2], parts[3], int(parts[4])
    if not await verify_user(cq, uid): return
    
    db = ensure_user(uid, cq.from_user.first_name, cq.from_user.username)
    price = db.get("market", {}).get(sym, {}).get("current_price", STOCKS[sym]["base_price"])
    
    base_cost = price * amount
    fee = int(base_cost * config.MARKET_FEE_PCT)
    total_cost = base_cost + fee
    
    if db["users"][uid].get("nexus_shards", 0) < total_cost:
        await cq.answer(f"❌ Insufficient Shards! You need {total_cost} 💠.", show_alert=True)
        return
        
    db["users"][uid]["nexus_shards"] -= total_cost
    user_stocks = db["users"][uid].setdefault("stocks", {})
    
    if sym not in user_stocks: user_stocks[sym] = {"shares": 0, "avg_price": 0.0}
        
    old_shares = user_stocks[sym]["shares"]
    old_avg = user_stocks[sym]["avg_price"]
    
    new_shares = old_shares + amount
    new_avg = ((old_shares * old_avg) + base_cost) / new_shares
    
    user_stocks[sym]["shares"] = new_shares
    user_stocks[sym]["avg_price"] = new_avg
    save_db()
    
    success_text = (
        f"<b>「 PURCHASE SUCCESS ✅ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"You successfully acquired <b>{amount}x {sym}</b>!\n"
        f"💰 <b>Cost:</b> {total_cost} 💠 (Including {fee} fee)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❮ Back", callback_data=f"sm_bl_{uid}")]])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=success_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(success_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception: pass
    await cq.answer(f"✅ Bought {amount}x {sym}!", show_alert=True)

# ==========================================
# PORTFOLIO VIEW
# ==========================================
@main_router.callback_query(F.data.startswith("sm_p_"))
async def sm_portfolio_cb(cq: CallbackQuery):
    uid = cq.data.split("_")[2]
    if not await verify_user(cq, uid): return

    db = ensure_user(uid, cq.from_user.first_name, cq.from_user.username)
    my_stocks = db["users"][uid].get("stocks", {})
    market = db.get("market", {})
    
    if not my_stocks:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❮️ Back", callback_data=f"sm_m_{uid}")]])
        text_empty = "<b>「 💼 YOUR PORTFOLIO 」</b>\n━━━━━━━━━━━━━━━━━\nYou do not own any stocks."
        try:
            if cq.message.photo:
                await cq.message.edit_caption(caption=text_empty, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await cq.message.edit_text(text_empty, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception: pass
        return

    text = f"<b>「 💼 {cq.from_user.first_name}'s PORTFOLIO 」</b>\n━━━━━━━━━━━━━━━━━\n\n"
    total_val = 0
    total_prof = 0
    
    for sym, data in my_stocks.items():
        if data["shares"] <= 0: continue
        current_price = market.get(sym, {}).get("current_price", STOCKS[sym]["base_price"])
        avg_price = data["avg_price"]
        shares = data["shares"]
        
        val = current_price * shares
        prof = int((current_price - avg_price) * shares)
        total_val += val
        total_prof += prof
        
        status = "🟢" if prof >= 0 else "🔴"
        text += f"🏢 <b>{STOCKS[sym]['name']} ({sym})</b>\n"
        text += f"📦 Shares: {shares} | Avg Buy: {int(avg_price)} 💠\n"
        text += f"💰 Value: {val} 💠 | P/L: {prof} {status}\n\n"
        
    status_total = "🟢" if total_prof >= 0 else "🔴"
    text += "━━━━━━━━━━━━━━━━━\n"
    text += f"🏦 <b>Total Value:</b> {total_val} 💠\n"
    text += f"📈 <b>Net Profit:</b> {total_prof} {status_total}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Sell Stocks", callback_data=f"sm_sl_{uid}")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data=f"sm_p_{uid}"),
         InlineKeyboardButton(text="❮ Main Menu", callback_data=f"sm_m_{uid}")]
    ])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await cq.answer()
    except TelegramBadRequest:
        await cq.answer("⏱️ Portfolio values haven't changed yet.", show_alert=False)
    except Exception: pass

# ==========================================
# SELL FLOW & EXECUTION
# ==========================================
@main_router.callback_query(F.data.startswith("sm_sl_"))
async def sm_sell_list_cb(cq: CallbackQuery):
    uid = cq.data.split("_")[2]
    if not await verify_user(cq, uid): return

    db = ensure_user(uid, cq.from_user.first_name, cq.from_user.username)
    my_stocks = db["users"][uid].get("stocks", {})
    market = db.get("market", {})
    
    buttons = []
    row = []
    text = "<b>「 💵 SELL STOCKS 」</b>\n━━━━━━━━━━━━━━━━━\nSelect a stock from your portfolio to sell:\n\n"
    
    idx = 1
    for sym, data in my_stocks.items():
        if data["shares"] > 0:
            current_price = market.get(sym, {}).get("current_price", STOCKS.get(sym, {}).get("base_price", 0))
            text += f"<b>{idx})</b> 💵 {STOCKS.get(sym, {}).get('name', sym)}\n   └ 📦 Shares: {data['shares']} | Price: {current_price} 💠\n"
            
            row.append(InlineKeyboardButton(text=str(idx), callback_data=f"sm_sv_{uid}_{sym}"))
            if len(row) == 5:
                buttons.append(row)
                row = []
            idx += 1
            
    if idx == 1:
        await cq.answer("❌ You don't own any stocks to sell.", show_alert=True)
        return
        
    if row: buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="🔄 Refresh", callback_data=f"sm_sl_{uid}"),
        InlineKeyboardButton(text="❮️ Back", callback_data=f"sm_m_{uid}")
    ])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)
        await cq.answer()
    except TelegramBadRequest:
        await cq.answer("⏱️ Prices haven't changed yet.", show_alert=False)
    except Exception: pass

@main_router.callback_query(F.data.startswith("sm_sv_"))
async def sm_sellview_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid, sym = parts[2], parts[3]
    if not await verify_user(cq, uid): return

    db = load_db()
    shares_owned = db["users"].get(uid, {}).get("stocks", {}).get(sym, {}).get("shares", 0)
    
    if shares_owned <= 0:
        await cq.answer("❌ You don't own this stock anymore.", show_alert=True)
        return
        
    current_price = db.get("market", {}).get(sym, {}).get("current_price", STOCKS[sym]["base_price"])
    
    text = (
        f"<b>「 SELL {sym} 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>You own:</b> {shares_owned} shares\n"
        f"💵 <b>Market Price:</b> {current_price} 💠\n\n"
        f"<i>A 1.5% broker fee is deducted from the payout.</i>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Sell x1", callback_data=f"sm_cs_{uid}_{sym}_1"),
         InlineKeyboardButton(text="Sell x5", callback_data=f"sm_cs_{uid}_{sym}_5"),
         InlineKeyboardButton(text="Sell x10", callback_data=f"sm_cs_{uid}_{sym}_10")],
        [InlineKeyboardButton(text="Sell ALL", callback_data=f"sm_cs_{uid}_{sym}_{shares_owned}")],
        [InlineKeyboardButton(text="❮ Back", callback_data=f"sm_sl_{uid}")]
    ])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception: pass

@main_router.callback_query(F.data.startswith("sm_cs_"))
async def sm_confirm_sell_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    if len(parts) != 5: return
    uid, sym, amount = parts[2], parts[3], int(parts[4])
    if not await verify_user(cq, uid): return
    
    db = load_db()
    shares_owned = db["users"].get(uid, {}).get("stocks", {}).get(sym, {}).get("shares", 0)
    
    if shares_owned < amount:
        await cq.answer("❌ You don't have enough shares to sell this amount.", show_alert=True)
        return
        
    price = db.get("market", {}).get(sym, {}).get("current_price", STOCKS[sym]["base_price"])
    gross_payout = price * amount
    fee = int(gross_payout * config.MARKET_FEE_PCT)
    net_payout = gross_payout - fee
    
    caption = (
        f"<b>「 CONFIRM SALE ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>Stock:</b> {STOCKS[sym]['name']} ({sym})\n"
        f"📦 <b>Selling:</b> {amount} shares\n"
        f"💵 <b>Market Value:</b> {gross_payout} 💠\n"
        f"🏦 <b>Broker Fee (1.5%):</b> -{fee} 💠\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Net Payout:</b> {net_payout} 💠\n\n"
        f"<i>Do you want to proceed?</i>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Sell", callback_data=f"sm_xs_{uid}_{sym}_{amount}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"sm_sv_{uid}_{sym}")]
    ])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(caption, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception: pass
    await cq.answer()

@main_router.callback_query(F.data.startswith("sm_xs_"))
async def sm_execute_sell_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    if len(parts) != 5: return
    uid, sym, amount = parts[2], parts[3], int(parts[4])
    if not await verify_user(cq, uid): return
    
    db = load_db()
    user_stocks = db["users"].get(uid, {}).get("stocks", {})
    shares_owned = user_stocks.get(sym, {}).get("shares", 0)
    
    if shares_owned < amount:
        await cq.answer(f"❌ You only have {shares_owned} shares of {sym}!", show_alert=True)
        return
        
    price = db.get("market", {}).get(sym, {}).get("current_price", STOCKS[sym]["base_price"])
    gross_payout = price * amount
    fee = int(gross_payout * config.MARKET_FEE_PCT)
    net_payout = gross_payout - fee
    
    db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + net_payout
    user_stocks[sym]["shares"] -= amount
    
    if user_stocks[sym]["shares"] <= 0: del user_stocks[sym]
    save_db()
    
    success_text = (
        f"<b>「 SALE SUCCESS ✅ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"You successfully sold <b>{amount}x {sym}</b>!\n"
        f"💰 <b>Earned:</b> {net_payout} 💠 (After {fee} fee)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❮ Back", callback_data=f"sm_p_{uid}")]])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=success_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(success_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception: pass
    await cq.answer(f"✅ Sold {amount}x {sym}!", show_alert=True)