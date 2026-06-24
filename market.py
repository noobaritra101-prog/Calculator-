import time
import random
import asyncio
import io
from PIL import Image, ImageDraw, ImageFont
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, InputMediaPhoto
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

import config
from config import bot, main_router, load_db, save_db, ensure_user, STOCKS

# ==========================================
# MARKET CRASH SETTINGS (🔴 Extreme Risk only)
# ==========================================
# Any stock with volatility above this is eligible to crash. This matches
# the existing "🔴 Extreme Risk (Gamble)" tier threshold used in the UI.
CRASH_VOLATILITY_THRESHOLD = 0.25
# How long trading (buying AND selling) is frozen on a crashed stock.
CRASH_FREEZE_SECONDS = 2 * 60 * 60
# Crashed price = this fraction of the stock's base_price.
CRASH_PRICE_FLOOR_PCT = 0.05
# Only these symbols can ever crash — random or forced via /fcrash.
CRASHABLE_SYMBOLS = {"NEX", "TRK"}


def is_extreme_risk(sym: str) -> bool:
    return STOCKS.get(sym, {}).get("volatility", 0) > CRASH_VOLATILITY_THRESHOLD


def is_frozen(market_entry: dict) -> bool:
    """True if this stock is currently inside its post-crash trading freeze."""
    frozen_until = market_entry.get("frozen_until", 0)
    return time.time() < frozen_until


def freeze_time_left_str(market_entry: dict) -> str:
    seconds_left = max(0, int(market_entry.get("frozen_until", 0) - time.time()))
    h, rem = divmod(seconds_left, 3600)
    m, _ = divmod(rem, 60)
    if h: return f"{h}h {m}m"
    return f"{m}m"


def apply_crash(db: dict, sym: str) -> int:
    """Crashes `sym`: it has reached a price of 0, so trading is frozen for
    2 hours and every holder's position in it is wiped to nothing (their
    shares are now worth 0, same as the price). Returns holders_wiped.
    Caller is responsible for save_db()."""
    market = db.setdefault("market", {})
    stock_info = STOCKS[sym]
    entry = market.setdefault(sym, {"current_price": stock_info["base_price"], "history": [stock_info["base_price"]], "flow": 0})

    crash_price = max(1, int(stock_info["base_price"] * CRASH_PRICE_FLOOR_PCT))
    entry["current_price"] = crash_price
    entry["history"].append(crash_price)
    if len(entry["history"]) > 24:
        entry["history"].pop(0)
    entry["frozen_until"] = time.time() + CRASH_FREEZE_SECONDS
    entry["flow"] = 0

    # Wipe every holder's position in this stock — it hit 0, so there's
    # nothing left to wipe FROM; their shares are simply gone.
    wiped_holders = 0
    for u_id, u_data in db.get("users", {}).items():
        u_stocks = u_data.get("stocks", {})
        if sym in u_stocks and u_stocks[sym].get("shares", 0) > 0:
            del u_stocks[sym]
            wiped_holders += 1
    entry["last_crash_wiped_holders"] = wiped_holders
    return wiped_holders


async def announce_crash_in_main_group(db: dict, sym: str, wiped: int):
    """Posts the crash announcement to the main group and pins it. Used by
    both engine-driven crashes (price hit 0) and /fcrash so every crash —
    automatic or manual — gets the same public notification."""
    entry = db.get("market", {}).get(sym, {})
    text = (
        f"<b>「 💥 CRASH 💥 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>{STOCKS[sym]['name']} ({sym})</b> has crashed — price hit <b>0 💠</b>.\n\n"
        f"💵 <b>Reopening price:</b> {entry.get('current_price', '?')} 💠\n"
        f"🚫 <b>Trading frozen for:</b> {freeze_time_left_str(entry)}\n"
        f"☠️ <b>{wiped} holder(s)</b> had their position in {sym} become worth <b>0 💠</b>."
    )
    try:
        msg = await config.bot.send_message(
            chat_id=config.MAIN_GROUP_USERNAME,
            text=text,
            parse_mode=ParseMode.HTML
        )
        await config.bot.pin_chat_message(
            chat_id=config.MAIN_GROUP_USERNAME,
            message_id=msg.message_id,
            disable_notification=False
        )
    except Exception as announce_err:
        print(f"[CRASH ANNOUNCE] Failed to post/pin in main group: {announce_err}")



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
                market[sym] = {"current_price": stock_info["base_price"], "history": [stock_info["base_price"]] * 24, "flow": 0}

            # A /fcrash ramp-down owns this symbol's price exclusively while
            # it's in progress — skip the normal RNG tick for it so the two
            # don't fight over the same value mid-ramp.
            if market[sym].get("force_crashing"):
                continue

            old_price = market[sym]["current_price"]
            base_price = stock_info["base_price"]
            volatility = stock_info["volatility"]

            # Player influence now reflects NET recent buy/sell activity since the
            # last tick (flow), not total outstanding shares. Total holdings can
            # only ever be >= 0, so using it as the influence meant merely HOLDING
            # shares (with no new trades) kept nudging the price up forever, with
            # no symmetric downward pull — that's what let NEX/TRK climb endlessly
            # as long as anyone held shares, guaranteeing profit. Net flow can be
            # positive (net buying) or negative (net selling), so heavy selling
            # now actually pushes the price down again.
            flow = market[sym].get("flow", 0)
            raw_influence = flow * 0.0001
            player_influence = max(-old_price * 0.05, min(raw_influence, old_price * 0.05))

            # Soft mean-reversion: nudge price 2% back toward base each tick
            reversion = (base_price - old_price) * 0.02

            rng_shift = random.uniform(-volatility, volatility)
            new_price = int(old_price + (old_price * rng_shift) + player_influence + reversion)

            # Clamp: floor at 10% of base, ceiling at 20x base. NEX/TRK are
            # exempt from the floor — they're allowed to drift all the way
            # down to 0, which is what now triggers their crash (see below).
            # They still can't go negative.
            price_ceil = int(base_price * 20)
            if sym in CRASHABLE_SYMBOLS:
                price_floor = 0
            else:
                price_floor = max(5, int(base_price * 0.10))
            if new_price < price_floor: new_price = price_floor
            if new_price > price_ceil:  new_price = price_ceil

            market[sym]["current_price"] = new_price
            market[sym]["history"].append(new_price)
            if len(market[sym]["history"]) > 24: 
                market[sym]["history"].pop(0)

            # Flow only represents activity SINCE THE LAST TICK — reset it now
            # that it's been applied, so idle holding has zero ongoing effect.
            market[sym]["flow"] = 0

        # ── PRICE-DRIVEN CRASHES (NEX/TRK only) ──────────────────────────
        # No more random roll. A crashable stock crashes the moment its
        # price actually reaches 0 — which only CRASHABLE_SYMBOLS can ever
        # do, since the floor clamp above skips them. Everything else is
        # floored at 10% of base price and can never trigger this.
        crashed_syms = []
        for sym in CRASHABLE_SYMBOLS:
            if sym not in STOCKS: continue
            entry = market[sym]
            if is_frozen(entry): continue
            if entry["current_price"] <= 0:
                crashed_syms.append(sym)

        for sym in crashed_syms:
            wiped = apply_crash(db, sym)
            await announce_crash_in_main_group(db, sym, wiped)

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
# /fcrash <SYMBOL> — owner-only forced crash
# ==========================================
FCRASH_RAMP_SECONDS = 60
FCRASH_TICK_SECONDS = 2  # price steps down once every 2s -> ~30 steps over 60s


async def _fcrash_ramp_down(sym: str, announce_chat_id):
    """Slowly walks `sym`'s price down to 0 over FCRASH_RAMP_SECONDS, then
    triggers the same apply_crash()/announcement path as an organic
    price-hits-0 crash. Runs as a detached background task so /fcrash
    returns immediately instead of blocking the command for 60 seconds."""
    db = load_db()
    market = db.setdefault("market", {})
    entry = market.setdefault(sym, {"current_price": STOCKS[sym]["base_price"], "history": [STOCKS[sym]["base_price"]] * 24, "flow": 0})

    entry["force_crashing"] = True
    start_price = max(1, entry["current_price"])
    save_db()

    steps = max(1, FCRASH_RAMP_SECONDS // FCRASH_TICK_SECONDS)
    for step in range(1, steps + 1):
        await asyncio.sleep(FCRASH_TICK_SECONDS)
        db = load_db()
        market = db.setdefault("market", {})
        entry = market.setdefault(sym, {"current_price": start_price, "history": [start_price], "flow": 0})

        # Linear ramp down to exactly 0 on the final step.
        remaining_fraction = max(0.0, 1 - (step / steps))
        entry["current_price"] = int(start_price * remaining_fraction)
        entry["history"].append(entry["current_price"])
        if len(entry["history"]) > 24:
            entry["history"].pop(0)
        save_db()

    # Guarantee an exact 0 regardless of any rounding above, then crash it.
    db = load_db()
    market = db.setdefault("market", {})
    entry = market[sym]
    entry["current_price"] = 0
    entry["force_crashing"] = False
    wiped = apply_crash(db, sym)
    save_db()

    await announce_crash_in_main_group(db, sym, wiped)
    try:
        await config.bot.send_message(
            chat_id=announce_chat_id,
            text=f"✅ {sym} finished ramping down and crashed at 0 💠. Announced in the main group.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


@main_router.message(Command("fcrash"))
async def force_crash_cmd(message: Message, command: CommandObject):
    if message.from_user.id != config.SUPREME_OWNER_ID:
        return  # silent — don't reveal this command exists to non-owners

    arg = (command.args or "").strip().upper()
    if arg not in CRASHABLE_SYMBOLS:
        allowed = "/".join(sorted(CRASHABLE_SYMBOLS))
        await message.reply(
            f"⚠️ <b>Usage:</b> <code>/fcrash {allowed}</code>\nOnly {allowed} can be crashed.",
            parse_mode=ParseMode.HTML
        )
        return

    if arg not in STOCKS:
        await message.reply(f"❌ {arg} isn't a configured stock.", parse_mode=ParseMode.HTML)
        return

    db = load_db()
    market = db.setdefault("market", {})
    if arg not in market:
        market[arg] = {"current_price": STOCKS[arg]["base_price"], "history": [STOCKS[arg]["base_price"]] * 24, "flow": 0}

    if market[arg].get("force_crashing"):
        await message.reply(f"⏳ {arg} is already ramping down to a crash.", parse_mode=ParseMode.HTML)
        return
    if is_frozen(market[arg]):
        await message.reply(f"🚫 {arg} is still frozen from a previous crash.", parse_mode=ParseMode.HTML)
        return

    asyncio.create_task(_fcrash_ramp_down(arg, message.chat.id))
    await message.reply(
        f"📉 <b>{arg}</b> will now slowly drop to <b>0 💠</b> over the next ~{FCRASH_RAMP_SECONDS}s, then crash.",
        parse_mode=ParseMode.HTML
    )

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
        f"• 📅 <b>Buy Limit:</b> A maximum daily allotment of <b>{config.DAILY_STOCK_BUY_LIMIT} shares</b> resets at midnight UTC.\n"
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
        f"• 📅 <b>Buy Limit:</b> A maximum daily allotment of <b>{config.DAILY_STOCK_BUY_LIMIT} shares</b> resets at midnight UTC.\n"
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
        
        frozen_tag = ""
        if is_frozen(market.get(sym, {})):
            frozen_tag = f" 🚫 <i>FROZEN ({freeze_time_left_str(market.get(sym, {}))})</i>"

        text += f"<b>{idx})</b> {emoji} <b>{data['name']}</b> ({sym}) - <b>{price} 💠</b><i>{pct_str}</i>{frozen_tag}\n"
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

    # Retrieve daily buying limit statistics
    today = config.get_shop_rotation_seed()
    daily_stock_data = db["users"].get(uid, {}).get("daily_stock_bought", {"date": "", "amount": 0})
    current_daily_amount = daily_stock_data.get("amount", 0) if daily_stock_data.get("date") == today else 0
    remaining_limit = max(0, config.DAILY_STOCK_BUY_LIMIT - current_daily_amount)
    
    caption = (
        f"<b>「 {stock['name']} ({sym}) 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote><i>High-fidelity analytical chart displaying the last 24 transaction nodes.</i></blockquote>\n\n"
        f"💵 <b>Current Valuation:</b> <b>{price} 💠</b>\n"
        f"📊 <b>24h Direct Trend:</b> <i>{trend}</i>\n"
        f"⚠️ <b>Risk Tier:</b> <b>{risk_level}</b>\n"
        f"📅 <b>Daily Buy Limit:</b> <code>{current_daily_amount}/{config.DAILY_STOCK_BUY_LIMIT}</code> shares\n"
        f"📥 <b>Remaining Today:</b> <code>{remaining_limit}</code> shares\n\n"
        f"🛒 <b>Purchase Volume:</b> <b>{amount} shares</b>\n"
        f"💰 <b>Estimated Cost:</b> <b>{total_cost} 💠</b> <i>(incl. 1.5% fee)</i>\n\n"
        f"<i>Brokerage Fee (1.5%) applies automatically on buy executions.</i>"
    )

    frozen = is_frozen(market)
    if frozen:
        caption += (
            f"\n\n🚫 <b>TRADING HALTED</b>\n"
            f"<i>This asset crashed and is frozen for {freeze_time_left_str(market)} more. "
            f"No buying or selling until trading resumes.</i>"
        )
    
    # Calculate amount adjustments safely (ensure minimum of 1 share)
    minus_10 = max(1, amount - 10)
    minus_1 = max(1, amount - 1)
    plus_1 = amount + 1
    plus_10 = amount + 10
    
    buy_row = (
        [InlineKeyboardButton(text=f"🚫 Halted ({freeze_time_left_str(market)})", callback_data="noop")]
        if frozen else
        [InlineKeyboardButton(text=f"🛒 Buy {amount} Share(s)", callback_data=f"sm_cb_{uid}_{sym}_{amount}")]
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➖10", callback_data=f"sm_v_{uid}_{sym}_{minus_10}"),
            InlineKeyboardButton(text="➖1", callback_data=f"sm_v_{uid}_{sym}_{minus_1}"),
            InlineKeyboardButton(text=f"📦 {amount}", callback_data="noop"),
            InlineKeyboardButton(text="➕1", callback_data=f"sm_v_{uid}_{sym}_{plus_1}"),
            InlineKeyboardButton(text="➕10", callback_data=f"sm_v_{uid}_{sym}_{plus_10}")
        ],
        buy_row,
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

    if is_frozen(db.get("market", {}).get(sym, {})):
        await cq.answer(
            f"🚫 Trading on {sym} is halted for {freeze_time_left_str(db['market'][sym])} following a market crash.",
            show_alert=True
        )
        return

    # Midnight reset verify check for stock buy allocation
    today = config.get_shop_rotation_seed()
    daily_stock_data = db["users"].setdefault(uid, {}).setdefault("daily_stock_bought", {"date": "", "amount": 0})
    if daily_stock_data.get("date") != today:
        daily_stock_data["date"] = today
        daily_stock_data["amount"] = 0
        save_db()

    current_daily_amount = daily_stock_data.get("amount", 0)
    if current_daily_amount + amount > config.DAILY_STOCK_BUY_LIMIT:
        await cq.answer(
            f"❌ Limit Exceeded!\n"
            f"Daily allotment allows buying {config.DAILY_STOCK_BUY_LIMIT} shares.\n"
            f"You have already bought {current_daily_amount} shares today.",
            show_alert=True
        )
        return

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
        f"📅 <b>Quota Forecast:</b> <code>{current_daily_amount + amount}/{config.DAILY_STOCK_BUY_LIMIT}</code> shares\n"
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

    if is_frozen(db.get("market", {}).get(sym, {})):
        await cq.answer(
            f"🚫 Trading on {sym} is halted for {freeze_time_left_str(db['market'][sym])} following a market crash.",
            show_alert=True
        )
        return

    # Midnight reset check verification for active transaction
    today = config.get_shop_rotation_seed()
    daily_stock_data = db["users"][uid].setdefault("daily_stock_bought", {"date": "", "amount": 0})
    if daily_stock_data.get("date") != today:
        daily_stock_data["date"] = today
        daily_stock_data["amount"] = 0

    current_daily_amount = daily_stock_data.get("amount", 0)
    if current_daily_amount + amount > config.DAILY_STOCK_BUY_LIMIT:
        await cq.answer(
            f"❌ Quota Exceeded!\n"
            f"This purchase exceeds your remaining daily allowance of {config.DAILY_STOCK_BUY_LIMIT - current_daily_amount} shares.",
            show_alert=True
        )
        return

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

    # Report this purchase as positive trade flow for the market engine's
    # next tick (net buying nudges price up; reset to 0 after each tick).
    market_entry = db.setdefault("market", {}).setdefault(sym, {"current_price": STOCKS[sym]["base_price"], "history": [STOCKS[sym]["base_price"]], "flow": 0})
    market_entry["flow"] = market_entry.get("flow", 0) + amount

    # Update daily tracking limits
    db["users"][uid]["daily_stock_bought"]["amount"] = current_daily_amount + amount
    save_db()
    
    success_text = (
        f"<b>「 PURCHASE SUCCESS ✅ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote><i>Fiduciary transfer complete. Securities moved to your portfolio.</i></blockquote>\n\n"
        f"Acquired <b>{amount}x {sym}</b> shares successfully.\n"
        f"📅 <b>Daily Quota Status:</b> <code>{current_daily_amount + amount}/{config.DAILY_STOCK_BUY_LIMIT}</code> shares\n"
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
            frozen_tag = " 🚫 <i>FROZEN</i>" if is_frozen(market.get(sym, {})) else ""
            text += f"<b>{idx})</b> 💵 <b>{STOCKS.get(sym, {}).get('name', sym)}</b>{frozen_tag}\n   └ 📦 Volume: <b>{data['shares']}</b> | Price: <b>{current_price} 💠</b>\n"
            
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

    if is_frozen(db.get("market", {}).get(sym, {})):
        await cq.answer(
            f"🚫 Trading on {sym} is halted for {freeze_time_left_str(db['market'][sym])} following a market crash.",
            show_alert=True
        )
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
        [InlineKeyboardButton(text="Sell x1", callback_data=f"sm_cs_{uid}_{sym}_1")],
        [InlineKeyboardButton(text="Sell x5", callback_data=f"sm_cs_{uid}_{sym}_5")],
        [InlineKeyboardButton(text="Sell x10", callback_data=f"sm_cs_{uid}_{sym}_10")],
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

    if is_frozen(db.get("market", {}).get(sym, {})):
        await cq.answer(
            f"🚫 Trading on {sym} is halted for {freeze_time_left_str(db['market'][sym])} following a market crash.",
            show_alert=True
        )
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

    if is_frozen(db.get("market", {}).get(sym, {})):
        await cq.answer(
            f"🚫 Trading on {sym} is halted for {freeze_time_left_str(db['market'][sym])} following a market crash.",
            show_alert=True
        )
        return
        
    price = db.get("market", {}).get(sym, {}).get("current_price", STOCKS[sym]["base_price"])
    gross_payout = price * amount
    fee = int(gross_payout * config.MARKET_FEE_PCT)
    net_payout = gross_payout - fee
    
    db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + net_payout
    user_stocks[sym]["shares"] -= amount
    
    if user_stocks[sym]["shares"] <= 0: del user_stocks[sym]

    # Report this sale as negative trade flow for the market engine's next
    # tick (net selling nudges price down; reset to 0 after each tick).
    market_entry = db.setdefault("market", {}).setdefault(sym, {"current_price": STOCKS[sym]["base_price"], "history": [STOCKS[sym]["base_price"]], "flow": 0})
    market_entry["flow"] = market_entry.get("flow", 0) - amount

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
