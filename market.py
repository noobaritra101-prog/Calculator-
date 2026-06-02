import time
import random
import asyncio
import io
from PIL import Image, ImageDraw, ImageFont
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
# MARKET ENGINE (Runs periodically)
# ==========================================
async def market_engine_loop():
    while True:
        await asyncio.sleep(config.MARKET_UPDATE_INTERVAL)
        db = load_db()
        market = db.setdefault("market", {})

        for sym, stock_info in STOCKS.items():
            if sym not in market:
                market[sym] = {"current_price": stock_info["base_price"], "history": [stock_info["base_price"]] * 24}
            
            old_price = market[sym]["current_price"]
            base_price = stock_info["base_price"]
            volatility = stock_info["volatility"]
            
            # Cap player influence to prevent runaway prices (max +5% nudge)
            total_shares_held = sum(u.get("stocks", {}).get(sym, {}).get("shares", 0) for u in db["users"].values())
            raw_influence = total_shares_held * 0.0001
            player_influence = min(raw_influence, old_price * 0.05)

            # Soft mean-reversion: nudge price 2% back toward base each tick
            reversion = (base_price - old_price) * 0.02

            rng_shift = random.uniform(-volatility, volatility)
            new_price = int(old_price + (old_price * rng_shift) + player_influence + reversion)
            
            # Clamp: floor at 10% of base, ceiling at 20x base
            price_floor = max(5, int(base_price * 0.10))
            price_ceil  = int(base_price * 20)
            if new_price < price_floor: new_price = price_floor
            if new_price > price_ceil:  new_price = price_ceil
            
            market[sym]["current_price"] = new_price
            market[sym]["history"].append(new_price)
            if len(market[sym]["history"]) > 24: 
                market[sym]["history"].pop(0)

        save_db()

        # ── DB-Group log: market tick summary ──────────────────────────────
        try:
            lines = [f"<b>「 📈 MARKET ENGINE UPDATE 」</b>",
                     f"━━━━━━━━━━━━━━━━━━━━━━",
                     f"🕐 <b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"]
            for sym in STOCKS:
                price = market.get(sym, {}).get("current_price", "?")
                hist  = market.get(sym, {}).get("history", [])
                prev  = hist[-2] if len(hist) >= 2 else price
                arrow = "🟢" if price >= prev else "🔴"
                lines.append(f"  {arrow} <b>{sym}</b>  ➜  <b>{price} 💠</b>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            await config.bot.send_message(
                chat_id=config.DATABASE_BACKUP_ID,
                text="\n".join(lines),
                parse_mode="HTML"
            )
        except Exception as log_err:
            print(f"[MARKET LOG] Failed: {log_err}")

        print(f"[MARKET] Engine updated stock prices at {time.strftime('%X')}")

# ==========================================
# HIGH-FIDELITY PIL GRAPH GENERATOR
# ==========================================
def generate_stock_graph(symbol: str, history: list) -> io.BytesIO:
    width, height = 600, 300
    
    # Initialize high-quality base image with transparent alpha support
    img = Image.new("RGBA", (width, height), (11, 15, 25, 255))
    draw = ImageDraw.Draw(img)
    
    color_up = (46, 204, 113, 255)     # Glowing Emerald
    color_down = (231, 76, 60, 255)    # Glowing Crimson
    grid_color = (24, 30, 43, 255)     # Subtle Grid Slate
    
    is_up = history[-1] >= history[0] if len(history) > 1 else True
    line_color = color_up if is_up else color_down

    # Draw neon technical grid structure
    for i in range(0, width, 50): 
        draw.line([(i, 0), (i, height)], fill=grid_color, width=1)
    for i in range(0, height, 50): 
        draw.line([(0, i), (width, i)], fill=grid_color, width=1)

    # Plot coordinates & calculate gradients
    if len(history) > 1:
        max_price = max(history) + 5
        min_price = min(history) - 5 if min(history) > 5 else 0
        price_range = max_price - min_price if max_price != min_price else 1
        
        x_step = width / (len(history) - 1)
        points = []
        
        for i, price in enumerate(history):
            x = i * x_step
            y = height - ((price - min_price) / price_range * height)
            points.append((x, y))
            
        # Composite semi-transparent glow fill under the plotted curve
        fill_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        fill_draw = ImageDraw.Draw(fill_layer)
        poly_points = [(points[0][0], height)] + points + [(points[-1][0], height)]
        fill_color = (46, 204, 113, 30) if is_up else (231, 76, 60, 30)
        fill_draw.polygon(poly_points, fill=fill_color)
        
        img = Image.alpha_composite(img, fill_layer)
        draw = ImageDraw.Draw(img) # Re-bind draw handle to active layer
        
        # Render clean trend curve with custom antialiased thickness
        draw.line(points, fill=line_color, width=4)
        
        # Plot precise node point rings
        for pt in points:
            draw.ellipse((pt[0]-4, pt[1]-4, pt[0]+4, pt[1]+4), fill=(255, 255, 255, 255), outline=line_color, width=1)

    # Render technical information labels directly on canvas
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    text_color = (130, 145, 175, 255)
    draw.text((15, 15), f"ASSET: {symbol}", fill=(255, 255, 255, 255), font=font)
    draw.text((15, 30), f"PRICE: {history[-1]} Shards", fill=line_color, font=font)
    draw.text((15, height - 25), "NEXUS EXCHANGE SYSTEMS", fill=text_color, font=font)

    # Convert back to standard RGB to safeguard file transfers
    final_img = img.convert("RGB")
    bio = io.BytesIO()
    final_img.save(bio, format="PNG")
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
        "━━━━━━━━━━━━━━━━━\n\n"
        "<blockquote><i>Welcome to the financial heart of the Anime Nexus. Acquire, trade, and exchange fractional shares dynamically.</i></blockquote>\n\n"
        "<b>💡 Quick Market Rules:</b>\n"
        "• 🏢 <b>Stocks:</b> Buy shares of elite anime factions. Buy low, sell high.\n"
        "• 📊 <b>Volatility:</b> Highly volatile factions yield rapid gains but carry steep risk.\n"
        "• ⏱️ <b>Updates:</b> Prices shift every <b>5 minutes</b> dynamically based on RNG.\n"
        "• 🏦 <b>Brokerage Fee:</b> A standard <b>1.5% fee</b> applies to both buy and sell trades.\n"
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
        "━━━━━━━━━━━━━━━━━\n\n"
        "<blockquote><i>Welcome to the financial heart of the Anime Nexus. Acquire, trade, and exchange fractional shares dynamically.</i></blockquote>\n\n"
        "<b>💡 Quick Market Rules:</b>\n"
        "• 🏢 <b>Stocks:</b> Buy shares of elite anime factions. Buy low, sell high.\n"
        "• 📊 <b>Volatility:</b> Highly volatile factions yield rapid gains but carry steep risk.\n"
        "• ⏱️ <b>Updates:</b> Prices shift every <b>5 minutes</b> dynamically based on RNG.\n"
        "• 🏦 <b>Brokerage Fee:</b> A standard <b>1.5% fee</b> applies to both buy and sell trades.\n"
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
    
    text = "<b>「 🛒 MARKET LISTINGS 」</b>\n━━━━━━━━━━━━━━━━━\nSelect an index to inspect financial parameters:\n\n"
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
        
        text += f"<b>{idx})</b> {emoji} <b>{data['name']}</b> ({sym}) - <b>{price} 💠</b><i>{pct_str}</i>\n"
        row.append(InlineKeyboardButton(text=str(idx), callback_data=f"sm_v_{uid}_{sym}_1"))
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
    uid = parts[2]
    sym = parts[3]
    # Check if a specific amount value is passed in the query path, else default to 1
    amount = int(parts[4]) if len(parts) > 4 else 1
    
    if not await verify_user(cq, uid): return

    db = load_db()
    market = db.get("market", {}).get(sym, {})
    
    stock = STOCKS[sym]
    price = market.get("current_price", stock["base_price"])
    history = market.get("history", [price])
    
    trend = "📈 BULLISH" if len(history) > 1 and history[-1] >= history[0] else "📉 BEARISH"
    
    vol = stock["volatility"]
    if vol <= 0.08: risk_level = "🟢 Low Risk (Stable)"
    elif vol <= 0.15: risk_level = "🟡 Medium Risk (Moderate)"
    elif vol <= 0.25: risk_level = "🟠 High Risk (Volatile)"
    else: risk_level = "🔴 Extreme Risk (Gamble)"
    
    base_cost = price * amount
    fee = int(base_cost * config.MARKET_FEE_PCT)
    total_cost = base_cost + fee
    
    caption = (
        f"<b>「 {stock['name']} ({sym}) 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote><i>High-fidelity analytical chart displaying the last 24 transaction nodes.</i></blockquote>\n\n"
        f"💵 <b>Current Valuation:</b> <b>{price} 💠</b>\n"
        f"📊 <b>24h Direct Trend:</b> <i>{trend}</i>\n"
        f"⚠️ <b>Risk Tier:</b> <b>{risk_level}</b>\n\n"
        f"🛒 <b>Purchase Volume:</b> <b>{amount} shares</b>\n"
        f"💰 <b>Estimated Cost:</b> <b>{total_cost} 💠</b> <i>(incl. 1.5% fee)</i>\n\n"
        f"<i>Brokerage Fee (1.5%) applies automatically on buy executions.</i>"
    )
    
    # Calculate amount adjustments safely (ensure minimum of 1 share)
    minus_10 = max(1, amount - 10)
    minus_1 = max(1, amount - 1)
    plus_1 = amount + 1
    plus_10 = amount + 10
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➖10", callback_data=f"sm_v_{uid}_{sym}_{minus_10}"),
            InlineKeyboardButton(text="➖1", callback_data=f"sm_v_{uid}_{sym}_{minus_1}"),
            InlineKeyboardButton(text=f"📦 {amount}", callback_data="noop"),
            InlineKeyboardButton(text="➕1", callback_data=f"sm_v_{uid}_{sym}_{plus_1}"),
            InlineKeyboardButton(text="➕10", callback_data=f"sm_v_{uid}_{sym}_{plus_10}")
        ],
        [InlineKeyboardButton(text=f"🛒 Buy {amount} Share(s)", callback_data=f"sm_cb_{uid}_{sym}_{amount}")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data=f"sm_v_{uid}_{sym}_{amount}"),
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
        await cq.answer()
    except TelegramBadRequest:
        await cq.answer()
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
        f"<b>「 CONFIRM ACQUISITION 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote><i>Verification required to proceed with asset transfer.</i></blockquote>\n\n"
        f"🏢 <b>Faction:</b> {STOCKS[sym]['name']} ({sym})\n"
        f"📦 <b>Volume:</b> <b>{amount} shares</b>\n"
        f"💵 <b>Base Valuation:</b> {base_cost} 💠\n"
        f"🏦 <b>Brokerage Fee (1.5%):</b> {fee} 💠\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Total Deducted:</b> <b>{total_cost} 💠</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Buy", callback_data=f"sm_xb_{uid}_{sym}_{amount}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"sm_v_{uid}_{sym}_{amount}")]
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
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote><i>Fiduciary transfer complete. Securities moved to your portfolio.</i></blockquote>\n\n"
        f"Acquired <b>{amount}x {sym}</b> shares successfully.\n"
        f"💰 <b>Cost:</b> <b>{total_cost} 💠</b> (including brokerage commission)."
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
        text += f"📦 Shares: <b>{shares}</b> | Avg Buy: <i>{int(avg_price)} 💠</i>\n"
        text += f"💰 Value: <b>{val} 💠</b> | P/L: <b>{prof} {status}</b>\n\n"
        
    status_total = "🟢" if total_prof >= 0 else "🔴"
    text += "━━━━━━━━━━━━━━━━━\n"
    text += f"🏦 <b>Total Value:</b> <b>{total_val} 💠</b>\n"
    text += f"📈 <b>Net Profit/Loss:</b> <b>{total_prof} {status_total}</b>"

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
    text = "<b>「 💵 LIQUIDATE STOCKS 」</b>\n━━━━━━━━━━━━━━━━━\nSelect an asset from your active portfolio to liquidate:\n\n"
    
    idx = 1
    for sym, data in my_stocks.items():
        if data["shares"] > 0:
            current_price = market.get(sym, {}).get("current_price", STOCKS.get(sym, {}).get("base_price", 0))
            text += f"<b>{idx})</b> 💵 <b>{STOCKS.get(sym, {}).get('name', sym)}</b>\n   └ 📦 Volume: <b>{data['shares']}</b> | Price: <b>{current_price} 💠</b>\n"
            
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

    if sym not in STOCKS:
        await cq.answer("❌ Unknown stock symbol.", show_alert=True)
        return
        
    current_price = db.get("market", {}).get(sym, {}).get("current_price", STOCKS[sym]["base_price"])
    
    text = (
        f"<b>「 SELL {sym} 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>You own:</b> <b>{shares_owned} shares</b>\n"
        f"💵 <b>Market Price:</b> <b>{current_price} 💠</b>\n\n"
        f"<i>A 1.5% brokerage commission is deducted from the payout automatically.</i>"
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
        f"<b>「 CONFIRM SALE 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote><i>Verification required to proceed with asset liquidation.</i></blockquote>\n\n"
        f"🏢 <b>Faction:</b> {STOCKS[sym]['name']} ({sym})\n"
        f"📦 <b>Volume:</b> <b>{amount} shares</b>\n"
        f"💵 <b>Market Value:</b> {gross_payout} 💠\n"
        f"🏦 <b>Brokerage Fee (1.5%):</b> -{fee} 💠\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Net Payout:</b> <b>{net_payout} 💠</b>"
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
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote><i>Liquidation sequence resolved successfully.</i></blockquote>\n\n"
        f"Sold <b>{amount}x {sym}</b> shares.\n"
        f"💰 <b>Net Deposited:</b> <b>{net_payout} 💠</b> (commission processed)."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❮ Back", callback_data=f"sm_p_{uid}")]])
    
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=success_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(success_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception: pass
    await cq.answer(f"✅ Sold {amount}x {sym}!", show_alert=True)
