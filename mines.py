"""
==========================================
MINES — /mines <bet> <mines>
==========================================
Manual tap-to-reveal Mines with Web Mini App support.
The player picks a bet and a mine count, gets a 5x5 grid of hidden inline-button
tiles, and taps any tile to reveal it.

GAME RULES
  • Board is always 5x5 (25 tiles).
  • Player taps any hidden tile to reveal it.
  • Cash Out unlocks only after MIN_CASHOUT_GEMS (3) safe reveals.
  • Hit a mine -> round over, bet is lost, full board is revealed with 💥.
  • Cash out anytime -> paid at fair-odds multiplier reduced by house edge.
  • Dynamic Difficulty Adjustment (DDA) balancing after 3 safe reveals.
  • Mine positions are decided at start and synchronized across Telegram & Web.
"""

import asyncio
import random
import time
from datetime import date
from aiohttp import web

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode

from config import main_router, load_db, save_db, ensure_user, ADMIN_IDS

# ==========================================
# SETTINGS
# ==========================================
NETLIFY_WEBAPP_URL = "https://famous-centaur-493f76.netlify.app"

BOARD_SIZE = 25            # fixed 5x5 board
MIN_MINES = 3              # Min bomb count
MAX_MINES = 23             # Max bomb count
MIN_BET = 10
MAX_BET = 30000            # Max bet
HOUSE_EDGE_PCT = 0.15      # flat house edge (15%)
MAX_MULTIPLIER = 20.0      # safety ceiling multiplier
MIN_CASHOUT_GEMS = 3       # Cash Out unlocks after 3 safe reveals
GAME_TIMEOUT = 600         # 10 minutes limit in seconds

GEM_EMOJI = "💎"
BOMB_EMOJI = "💣"
BOOM_EMOJI = "💥"
HIDDEN_TILE = "•"

# RUBBER-BAND DDA CONFIGURATION
TARGET_NET = 0
RECOVERY_SCALE = 5000

# In-memory active round state, keyed by str(user_id)
active_games: dict = {}


# ==========================================
# GAME MATH & CORE FUNCTIONS
# ==========================================
def fair_multiplier(mines: int, gems_found: int) -> float:
    """Calculates fair probability-based multiplier capped at MAX_MULTIPLIER."""
    if gems_found <= 0:
        return 1.0
    safe_tiles = BOARD_SIZE - mines
    prob_survive = 1.0
    for i in range(gems_found):
        prob_survive *= (safe_tiles - i) / (BOARD_SIZE - i)
    fair = 1 / prob_survive
    final = fair * (1 - HOUSE_EDGE_PCT)
    return min(final, MAX_MULTIPLIER)


def generate_board(mines: int) -> list:
    """Returns a 25-length list: True = mine, False = safe gem tile."""
    board = [False] * BOARD_SIZE
    for pos in random.sample(range(BOARD_SIZE), mines):
        board[pos] = True
    return board


def apply_dda_check(uid: str, bet: int, board: list, idx: int, gems_found: int):
    """Executes Rubber-Band DDA balancing starting after the 3rd safe reveal."""
    db = load_db()
    user_data = db["users"].get(uid, {})
    shards = user_data.get("nexus_shards", 0)
    mines_bet = user_data.get("mines_bet", 0)
    mines_won = user_data.get("mines_won", 0)
    net_profit = mines_won - mines_bet

    if not board[idx] and gems_found >= 3:
        bet_contribution = (bet / MAX_BET) * 0.60
        balance_contribution = 0.50 if shards > 80000 else 0.0
        profit_contribution = max(0.0, net_profit / RECOVERY_SCALE) if net_profit > TARGET_NET else 0.0

        force_prob = bet_contribution + balance_contribution + profit_contribution

        if bet_contribution > 0.05 or balance_contribution > 0 or profit_contribution > 0:
            force_prob = min(0.90, force_prob)
            if random.random() < force_prob:
                unrevealed_mines = [i for i in range(BOARD_SIZE) if board[i] and i not in active_games[uid]["revealed"]]
                if unrevealed_mines:
                    swap_idx = random.choice(unrevealed_mines)
                    board[idx] = True
                    board[swap_idx] = False


# ==========================================
# RENDERING & UI BUILDERS
# ==========================================
def build_keyboard(uid: str, board: list, revealed: set, boom_at=None, game_over=False, can_cash_out=False) -> InlineKeyboardMarkup:
    rows = []
    for r in range(5):
        row = []
        for c in range(5):
            idx = r * 5 + c
            if idx == boom_at:
                row.append(InlineKeyboardButton(text=BOOM_EMOJI, callback_data="mnoop"))
            elif idx in revealed:
                row.append(InlineKeyboardButton(text=BOMB_EMOJI if board[idx] else GEM_EMOJI, callback_data="mnoop"))
            elif game_over:
                row.append(InlineKeyboardButton(text=BOMB_EMOJI if board[idx] else GEM_EMOJI, callback_data="mnoop"))
            else:
                row.append(InlineKeyboardButton(text=HIDDEN_TILE, callback_data=f"mtile_{uid}_{idx}"))
        rows.append(row)

    if not game_over and can_cash_out:
        rows.append([InlineKeyboardButton(text="💰 Cash Out", callback_data=f"mcash_{uid}")])

    # Always include Web App launch option
    rows.append([InlineKeyboardButton(text="📱 Open Web Mini App", web_app=WebAppInfo(url=NETLIFY_WEBAPP_URL))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_status_text(bet: int, mines: int, gems_found: int, current_mult: float) -> str:
    if gems_found < MIN_CASHOUT_GEMS:
        remaining = MIN_CASHOUT_GEMS - gems_found
        unlock_note = f"\n🔒 <b>Cash Out unlocks in:</b> {remaining} more reveal{'s' if remaining != 1 else ''}"
    else:
        unlock_note = "\n🔓 <b>Cash Out unlocked!</b>"

    return (
        "<b>「 💣 MINES 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Bet:</b> {bet} 💠\n"
        f"💣 <b>Mines:</b> {mines}\n"
        f"💎 <b>Gems Found:</b> {gems_found}\n"
        f"📈 <b>Current Multiplier:</b> {current_mult:.2f}x\n"
        f"✅ <b>Cash Out Value:</b> {int(bet * current_mult)} 💠"
        f"{unlock_note}\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<i>Tap a tile to reveal it.</i>"
    )


def build_win_text(bet: int, mines: int, gems_found: int, final_mult: float, payout: int) -> str:
    return (
        "<b>「 🎉 CASHED OUT! 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Bet:</b> {bet} 💠\n"
        f"💣 <b>Mines:</b> {mines}\n"
        f"💎 <b>Gems Found:</b> {gems_found}\n"
        f"📈 <b>Final Multiplier:</b> {final_mult:.2f}x\n"
        f"✅ <b>Payout:</b> +{payout} 💠\n"
        "━━━━━━━━━━━━━━━━━"
    )


def build_loss_text(bet: int, mines: int, gems_found: int) -> str:
    return (
        "<b>「 💥 BOOM! YOU HIT A MINE! 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💸 <b>Bet Lost:</b> {bet} 💠\n"
        f"💣 <b>Mines:</b> {mines}\n"
        f"💎 <b>Gems Found:</b> {gems_found}\n"
        "━━━━━━━━━━━━━━━━━"
    )


async def edit_game_message(cq: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup):
    """Safely updates either text message or photo caption dynamically."""
    try:
        if cq.message.photo:
            await cq.message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            await cq.message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    except Exception:
        pass


# ==========================================
# TELEGRAM BOT COMMAND HANDLERS
# ==========================================
@main_router.message(Command("play", "app", "webmines"))
async def play_webapp_cmd(message: Message):
    """Directly sends the Web Mini App button. Triggered by /play, /app, or /webmines."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💣 Play Mines Web App", web_app=WebAppInfo(url=NETLIFY_WEBAPP_URL))]
    ])
    await message.reply(
        "<b>「 💣 MINES WEB MINI APP 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Tap the button below to launch the Mines Web Mini App!",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@main_router.message(Command("mines"))
async def mines_cmd(message: Message, command: CommandObject):
    uid = str(message.from_user.id)
    db = load_db()
    ensure_user(uid, message.from_user.first_name, message.from_user.username)

    if uid in active_games:
        game = active_games[uid]
        if time.time() - game["start_time"] > GAME_TIMEOUT:
            active_games.pop(uid, None)
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_taken"] = global_stats.get("total_taken", 0) + game["bet"]
            save_db()
        else:
            await message.reply(
                "⚠️ You already have a Mines round in progress — finish or cash out that one first.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📱 Open Web Mini App", web_app=WebAppInfo(url=NETLIFY_WEBAPP_URL))]
                ]),
                parse_mode=ParseMode.HTML
            )
            return

    args = (command.args or "").split()
    if len(args) != 2:
        await message.reply(
            "<b>「 💣 MINES — HOW TO PLAY 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"<b>Usage:</b> <code>/mines &lt;bet&gt; &lt;mines&gt;</code>\n"
            f"<b>Example:</b> <code>/mines 50 3</code>\n\n"
            f"💰 Bet: {MIN_BET} – {MAX_BET:,} 💠\n"
            f"💣 Mines: {MIN_MINES} – {MAX_MINES} (on a 25-tile board)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Launch Web App", web_app=WebAppInfo(url=NETLIFY_WEBAPP_URL))]
            ]),
            parse_mode=ParseMode.HTML
        )
        return

    try:
        bet = int(args[0])
        mines = int(args[1])
    except ValueError:
        await message.reply("Bet and mines must both be whole numbers.", parse_mode=ParseMode.HTML)
        return

    if bet < MIN_BET or bet > MAX_BET:
        await message.reply(f"Bet must be between {MIN_BET} and {MAX_BET:,} Shards 💠.", parse_mode=ParseMode.HTML)
        return
    if mines < MIN_MINES or mines > MAX_MINES:
        await message.reply(f"Mines must be between {MIN_MINES} and {MAX_MINES}.", parse_mode=ParseMode.HTML)
        return

    user_data = db["users"][uid]
    if user_data.get("nexus_shards", 0) < bet:
        await message.reply("You don't have enough Shards for that bet.", parse_mode=ParseMode.HTML)
        return

    # Debit bet
    user_data["nexus_shards"] -= bet
    user_data["mines_bet"] = user_data.get("mines_bet", 0) + bet
    
    # Global stats tracking
    global_stats = db.setdefault("mines_global", {})
    global_stats["total_bet"] = global_stats.get("total_bet", 0) + bet
    global_stats["total_games"] = global_stats.get("total_games", 0) + 1
    
    today_str = date.today().isoformat()
    daily_games = global_stats.setdefault("daily_games", {})
    daily_games[today_str] = daily_games.get(today_str, 0) + 1

    save_db()

    board = generate_board(mines)
    safe_tiles = BOARD_SIZE - mines

    active_games[uid] = {
        "bet": bet,
        "mines": mines,
        "board": board,
        "revealed": set(),
        "gems_found": 0,
        "safe_tiles": safe_tiles,
        "lock": asyncio.Lock(),
        "chat_id": message.chat.id,
        "start_time": time.time(),
    }

    # Fetch dynamic game image if configured
    mines_image = db["settings"].get("mines_image")
    status_text = build_status_text(bet, mines, 0, 1.0)
    reply_markup = build_keyboard(uid, board, set(), can_cash_out=False)

    if mines_image:
        try:
            await message.reply_photo(
                photo=mines_image,
                caption=status_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            await message.reply(
                text=status_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    else:
        await message.reply(
            text=status_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )


# ==========================================
# TELEGRAM BOT CALLBACK HANDLERS
# ==========================================
@main_router.callback_query(lambda cq: cq.data and cq.data.startswith("mtile_"))
async def mines_tile_cb(cq: CallbackQuery):
    _, owner_id, idx_str = cq.data.split("_")
    if str(cq.from_user.id) != owner_id:
        await cq.answer("⚠️ This isn't your round!", show_alert=True)
        return

    game = active_games.get(owner_id)
    if not game:
        await cq.answer("This round has already ended.", show_alert=True)
        return

    idx = int(idx_str)

    async with game["lock"]:
        if active_games.get(owner_id) is not game:
            await cq.answer("This round has already ended.", show_alert=True)
            return

        # Check expiration (10 minutes)
        if time.time() - game["start_time"] > GAME_TIMEOUT:
            active_games.pop(owner_id, None)
            db = load_db()
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_taken"] = global_stats.get("total_taken", 0) + game["bet"]
            save_db()
            
            await cq.answer("⚠️ This round has expired (10-minute limit exceeded).", show_alert=True)
            try:
                await edit_game_message(
                    cq,
                    "<b>「 ⏳ ROUND EXPIRED 」</b>\n"
                    "━━━━━━━━━━━━━━━━━\n"
                    f"💸 <b>Bet Lost:</b> {game['bet']} 💠\n"
                    "<i>This round exceeded the 10-minute active limit.</i>\n"
                    "━━━━━━━━━━━━━━━━━",
                    None
                )
            except Exception:
                pass
            return

        if idx in game["revealed"]:
            await cq.answer()
            return

        bet, mines, board = game["bet"], game["mines"], game["board"]

        # Apply Rubber-Band DDA
        apply_dda_check(owner_id, bet, board, idx, game["gems_found"])

        # Hit a Mine
        if board[idx]:
            game["revealed"].add(idx)
            active_games.pop(owner_id, None)
            
            db = load_db()
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_taken"] = global_stats.get("total_taken", 0) + bet
            save_db()

            await cq.answer("💥 Boom!", show_alert=False)
            await edit_game_message(
                cq,
                build_loss_text(bet, mines, game["gems_found"]),
                build_keyboard(owner_id, board, game["revealed"], boom_at=idx, game_over=True)
            )
            return

        # Reveal Safe Gem
        game["revealed"].add(idx)
        game["gems_found"] += 1
        current_mult = fair_multiplier(mines, game["gems_found"])

        # Board Clear Win
        if game["gems_found"] >= game["safe_tiles"]:
            payout = int(bet * current_mult)
            net_profit_round = payout - bet

            db = load_db()
            db["users"][owner_id]["nexus_shards"] = db["users"][owner_id].get("nexus_shards", 0) + payout
            db["users"][owner_id]["mines_won"] = db["users"][owner_id].get("mines_won", 0) + payout
            
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_won"] = global_stats.get("total_won", 0) + net_profit_round
            
            save_db()
            active_games.pop(owner_id, None)
            await cq.answer("🎉 Board cleared!", show_alert=False)
            await edit_game_message(
                cq,
                build_win_text(bet, mines, game["gems_found"], current_mult, payout),
                build_keyboard(owner_id, board, game["revealed"])
            )
            return

        await cq.answer()
        await edit_game_message(
            cq,
            build_status_text(bet, mines, game["gems_found"], current_mult),
            build_keyboard(owner_id, board, game["revealed"], can_cash_out=game["gems_found"] >= MIN_CASHOUT_GEMS)
        )


@main_router.callback_query(lambda cq: cq.data and cq.data.startswith("mcash_"))
async def mines_cashout_cb(cq: CallbackQuery):
    owner_id = cq.data.split("_")[1]
    if str(cq.from_user.id) != owner_id:
        await cq.answer("⚠️ This isn't your round!", show_alert=True)
        return

    game = active_games.get(owner_id)
    if not game:
        await cq.answer("This round has already ended.", show_alert=True)
        return

    async with game["lock"]:
        if active_games.get(owner_id) is not game:
            await cq.answer("This round has already ended.", show_alert=True)
            return

        if time.time() - game["start_time"] > GAME_TIMEOUT:
            active_games.pop(owner_id, None)
            db = load_db()
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_taken"] = global_stats.get("total_taken", 0) + game["bet"]
            save_db()
            
            await cq.answer("⚠️ This round has expired.", show_alert=True)
            return

        if game["gems_found"] < MIN_CASHOUT_GEMS:
            remaining = MIN_CASHOUT_GEMS - game["gems_found"]
            await cq.answer(f"Reveal {remaining} more tile{'s' if remaining != 1 else ''} before you can cash out!", show_alert=True)
            return

        bet, mines, board = game["bet"], game["mines"], game["board"]
        final_mult = fair_multiplier(mines, game["gems_found"])
        payout = int(bet * final_mult)
        net_profit_round = payout - bet

        db = load_db()
        db["users"][owner_id]["nexus_shards"] = db["users"][owner_id].get("nexus_shards", 0) + payout
        db["users"][owner_id]["mines_won"] = db["users"][owner_id].get("mines_won", 0) + payout
        
        global_stats = db.setdefault("mines_global", {})
        global_stats["total_won"] = global_stats.get("total_won", 0) + net_profit_round
        
        save_db()
        active_games.pop(owner_id, None)

        await cq.answer(f"✅ Cashed out: +{payout} 💠")
        await edit_game_message(
            cq,
            build_win_text(bet, mines, game["gems_found"], final_mult, payout),
            build_keyboard(owner_id, board, game["revealed"])
        )


@main_router.callback_query(lambda cq: cq.data == "mnoop")
async def mines_noop_cb(cq: CallbackQuery):
    await cq.answer()


@main_router.message(Command("gmstats"))
async def gmstats_cmd(message: Message):
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        return

    db = load_db()
    global_stats = db.get("mines_global", {})
    
    total_won = global_stats.get("total_won", 0)
    total_taken = global_stats.get("total_taken", 0)
    total_games = global_stats.get("total_games", 0)
    
    today_str = date.today().isoformat()
    daily_games = global_stats.get("daily_games", {})
    games_today = daily_games.get(today_str, 0)
    
    text = (
        "<b>「 📊 MINES GLOBAL STATS 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💠 <b>Total Shards Generated Via Mines -</b> {total_won:,}\n"
        f"💸 <b>Total Shards taken by Mines -</b> {total_taken:,}\n"
        f"🎮 <b>Total mines Games played globally -</b> {total_games:,}\n"
        f"📅 <b>Total mines games played Today -</b> {games_today:,}\n"
        "━━━━━━━━━━━━━━━━━"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(Command("imm"))
async def imm_cmd(message: Message):
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        return

    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply("⚠️ Please reply to an image with <code>/imm</code> to set the Mines background photo.", parse_mode=ParseMode.HTML)
        return

    file_id = message.reply_to_message.photo[-1].file_id
    db = load_db()
    db["settings"]["mines_image"] = file_id
    save_db()

    await message.reply("✅ Mines game background image successfully set and saved to the database.", parse_mode=ParseMode.HTML)


# ==========================================
# AIOHTTP WEB API ROUTE HANDLERS
# ==========================================
def json_response(data, status=200):
    return web.json_response(data, status=status, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization"
    })


async def api_options_handler(request):
    return json_response({"status": "ok"})


async def api_get_state(request):
    uid = str(request.match_info.get("user_id"))
    db = load_db()
    user_data = db.get("users", {}).get(uid, {})
    balance = user_data.get("nexus_shards", 0)

    game = active_games.get(uid)
    if not game:
        return json_response({"active": False, "balance": balance})

    if time.time() - game["start_time"] > GAME_TIMEOUT:
        active_games.pop(uid, None)
        return json_response({"active": False, "balance": balance})

    current_mult = fair_multiplier(game["mines"], game["gems_found"])
    revealed_tiles = {str(idx): ("mine" if game["board"][idx] else "gem") for idx in game["revealed"]}

    return json_response({
        "active": True,
        "balance": balance,
        "bet": game["bet"],
        "mines": game["mines"],
        "gems_found": game["gems_found"],
        "current_mult": round(current_mult, 2),
        "cashout_value": int(game["bet"] * current_mult),
        "can_cash_out": game["gems_found"] >= MIN_CASHOUT_GEMS,
        "revealed": revealed_tiles
    })


async def api_start_game(request):
    try:
        data = await request.json()
    except Exception:
        return json_response({"detail": "Invalid JSON"}, status=400)

    uid = str(data.get("user_id"))
    bet = int(data.get("bet", 0))
    mines = int(data.get("mines", 0))

    db = load_db()
    ensure_user(uid, "Player", "Player")

    if uid in active_games:
        game = active_games[uid]
        if time.time() - game["start_time"] <= GAME_TIMEOUT:
            return json_response({"detail": "You already have an active round!"}, status=400)
        active_games.pop(uid, None)

    if bet < MIN_BET or bet > MAX_BET or mines < MIN_MINES or mines > MAX_MINES:
        return json_response({"detail": "Invalid bet or mine count."}, status=400)

    user_data = db["users"][uid]
    if user_data.get("nexus_shards", 0) < bet:
        return json_response({"detail": "Insufficient Shards balance!"}, status=400)

    user_data["nexus_shards"] -= bet
    user_data["mines_bet"] = user_data.get("mines_bet", 0) + bet

    global_stats = db.setdefault("mines_global", {})
    global_stats["total_bet"] = global_stats.get("total_bet", 0) + bet
    global_stats["total_games"] = global_stats.get("total_games", 0) + 1
    
    today_str = date.today().isoformat()
    daily_games = global_stats.setdefault("daily_games", {})
    daily_games[today_str] = daily_games.get(today_str, 0) + 1
    save_db()

    board = generate_board(mines)
    active_games[uid] = {
        "bet": bet,
        "mines": mines,
        "board": board,
        "revealed": set(),
        "gems_found": 0,
        "safe_tiles": BOARD_SIZE - mines,
        "lock": asyncio.Lock(),
        "start_time": time.time(),
    }

    return json_response({
        "status": "started",
        "balance": user_data["nexus_shards"],
        "bet": bet,
        "mines": mines,
        "gems_found": 0,
        "current_mult": 1.0,
        "can_cash_out": False
    })


async def api_reveal_tile(request):
    try:
        data = await request.json()
    except Exception:
        return json_response({"detail": "Invalid JSON"}, status=400)

    uid = str(data.get("user_id"))
    idx = int(data.get("tile_index", -1))

    game = active_games.get(uid)
    if not game:
        return json_response({"detail": "No active game."}, status=400)

    async with game["lock"]:
        if active_games.get(uid) is not game:
            return json_response({"detail": "Game ended."}, status=400)

        if idx < 0 or idx >= BOARD_SIZE or idx in game["revealed"]:
            return json_response({"detail": "Invalid or revealed tile."}, status=400)

        bet, mines, board = game["bet"], game["mines"], game["board"]
        apply_dda_check(uid, bet, board, idx, game["gems_found"])

        # MINE HIT
        if board[idx]:
            game["revealed"].add(idx)
            active_games.pop(uid, None)

            db = load_db()
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_taken"] = global_stats.get("total_taken", 0) + bet
            save_db()

            full_board = {str(i): ("mine" if board[i] else "gem") for i in range(BOARD_SIZE)}
            return json_response({
                "result": "mine",
                "boom_at": idx,
                "full_board": full_board,
                "balance": db["users"][uid].get("nexus_shards", 0)
            })

        # GEM SAFE
        game["revealed"].add(idx)
        game["gems_found"] += 1
        current_mult = fair_multiplier(mines, game["gems_found"])

        # BOARD CLEARED
        if game["gems_found"] >= game["safe_tiles"]:
            payout = int(bet * current_mult)
            db = load_db()
            db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + payout
            db["users"][uid]["mines_won"] = db["users"][uid].get("mines_won", 0) + payout

            global_stats = db.setdefault("mines_global", {})
            global_stats["total_won"] = global_stats.get("total_won", 0) + (payout - bet)
            save_db()

            active_games.pop(uid, None)
            full_board = {str(i): ("mine" if board[i] else "gem") for i in range(BOARD_SIZE)}
            return json_response({
                "result": "cleared",
                "payout": payout,
                "multiplier": round(current_mult, 2),
                "full_board": full_board,
                "balance": db["users"][uid]["nexus_shards"]
            })

        return json_response({
            "result": "gem",
            "tile_index": idx,
            "gems_found": game["gems_found"],
            "current_mult": round(current_mult, 2),
            "cashout_value": int(bet * current_mult),
            "can_cash_out": game["gems_found"] >= MIN_CASHOUT_GEMS
        })


async def api_cashout(request):
    try:
        data = await request.json()
    except Exception:
        return json_response({"detail": "Invalid JSON"}, status=400)

    uid = str(data.get("user_id"))
    game = active_games.get(uid)
    if not game:
        return json_response({"detail": "No active game."}, status=400)

    async with game["lock"]:
        if active_games.get(uid) is not game:
            return json_response({"detail": "Game ended."}, status=400)

        if game["gems_found"] < MIN_CASHOUT_GEMS:
            return json_response({"detail": f"Reveal {MIN_CASHOUT_GEMS - game['gems_found']} more gems to cash out!"}, status=400)

        bet, mines, board = game["bet"], game["mines"], game["board"]
        final_mult = fair_multiplier(mines, game["gems_found"])
        payout = int(bet * final_mult)

        db = load_db()
        db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + payout
        db["users"][uid]["mines_won"] = db["users"][uid].get("mines_won", 0) + payout

        global_stats = db.setdefault("mines_global", {})
        global_stats["total_won"] = global_stats.get("total_won", 0) + (payout - bet)
        save_db()

        active_games.pop(uid, None)
        full_board = {str(i): ("mine" if board[i] else "gem") for i in range(BOARD_SIZE)}

        return json_response({
            "status": "success",
            "payout": payout,
            "multiplier": round(final_mult, 2),
            "full_board": full_board,
            "balance": db["users"][uid]["nexus_shards"]
        })


def setup_mines_routes(app: web.Application):
    """Registers /api/mines routes on the main aiohttp server."""
    app.router.add_options("/api/mines/{tail:.*}", api_options_handler)
    app.router.add_get("/api/mines/state/{user_id}", api_get_state)
    app.router.add_post("/api/mines/start", api_start_game)
    app.router.add_post("/api/mines/reveal", api_reveal_tile)
    app.router.add_post("/api/mines/cashout", api_cashout)
