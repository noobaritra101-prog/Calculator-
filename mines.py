import asyncio
import random
import time
from datetime import date
from typing import Dict, Any

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import main_router, load_db, save_db, ensure_user, ADMIN_IDS

# ==========================================
# SETTINGS
# ==========================================
BOARD_SIZE = 25            # fixed 5x5 board
MIN_MINES = 3              # Min bomb count
MAX_MINES = 23             # Max bomb count
MIN_BET = 10
MAX_BET = 30000            # Max bet 30,000 Shards
HOUSE_EDGE_PCT = 0.15      # Disclosed flat house edge
MAX_MULTIPLIER = 20.0      # Multiplier ceiling
MIN_CASHOUT_GEMS = 3       # Gems needed to unlock cash out
GAME_TIMEOUT = 600         # 10 minutes limit in seconds

DEFAULT_WEBAPP_URL = "https://famous-centaur-493f76.netlify.app"

GEM_EMOJI = "💎"
BOMB_EMOJI = "💣"
BOOM_EMOJI = "💥"
HIDDEN_TILE = "•"

# ------------------------------------------
# RUBBER-BAND DDA CONFIGURATION
# ------------------------------------------
TARGET_NET = 0
RECOVERY_SCALE = 5000

# In-memory active round state, keyed by str(user_id).
active_games: dict = {}

# FastAPI Router for Web Mini App
mines_router = APIRouter(prefix="/api/mines", tags=["Mines Web App"])


# ==========================================
# PYDANTIC SCHEMAS (FOR REST API)
# ==========================================
class StartGameReq(BaseModel):
    user_id: str
    bet: int
    mines: int

class RevealTileReq(BaseModel):
    user_id: str
    tile_index: int

class CashoutReq(BaseModel):
    user_id: str


# ==========================================
# GAME MATH & HELPER FUNCTIONS
# ==========================================
def fair_multiplier(mines: int, gems_found: int) -> float:
    """Calculates fair-odds multiplier minus house edge, capped at MAX_MULTIPLIER."""
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
    """Returns a 25-length list, True = mine, False = safe gem tile."""
    board = [False] * BOARD_SIZE
    for pos in random.sample(range(BOARD_SIZE), mines):
        board[pos] = True
    return board


def apply_dda_balancing(uid: str, idx: int, game: dict) -> None:
    """
    Applies Dynamic Difficulty Balancing (DDA) on tile reveals for BOTH Bot and Web.
      1. High bet scaling
      2. Rich player correction (> 80,000 Shards balance)
      3. Personal net profit surplus rubber-band correction
    """
    bet, board = game["bet"], game["board"]
    db = load_db()
    user_data = db["users"].get(uid, {})
    shards = user_data.get("nexus_shards", 0)
    mines_bet = user_data.get("mines_bet", 0)
    mines_won = user_data.get("mines_won", 0)
    net_profit = mines_won - mines_bet

    # Only balance from the 4th tap onwards (gems_found >= 3)
    if not board[idx] and game["gems_found"] >= 3:
        # 1. Bet Scaling (adds up to 60% probability at 30k bet)
        bet_contribution = (bet / MAX_BET) * 0.60
        
        # 2. Rich player correction (> 80k balance)
        balance_contribution = 0.50 if shards > 80000 else 0.0
        
        # 3. Personal profit surplus rubber-band recovery
        profit_contribution = max(0.0, net_profit / RECOVERY_SCALE) if net_profit > TARGET_NET else 0.0

        force_prob = bet_contribution + balance_contribution + profit_contribution

        if bet_contribution > 0.05 or balance_contribution > 0 or profit_contribution > 0:
            force_prob = min(0.90, force_prob)  # Cap forced loss chance at 90%
            if random.random() < force_prob:
                unrevealed_mines = [i for i in range(BOARD_SIZE) if board[i] and i not in game["revealed"]]
                if unrevealed_mines:
                    swap_idx = random.choice(unrevealed_mines)
                    board[idx] = True
                    board[swap_idx] = False


# ==========================================
# BOT TELEGRAM KEYBOARDS & MESSAGES
# ==========================================
def build_keyboard(uid: str, board: list, revealed: set, boom_at=None, game_over=False, can_cash_out=False) -> InlineKeyboardMarkup:
    rows = []
    for r in range(5):
        row = []
        for c in range(5):
            idx = r * 5 + c
            if idx == boom_at:
                row.append(InlineKeyboardButton(text=BOOM_EMOJI, callback_data="mnoop"))
            elif idx in revealed or game_over:
                row.append(InlineKeyboardButton(text=BOMB_EMOJI if board[idx] else GEM_EMOJI, callback_data="mnoop"))
            else:
                row.append(InlineKeyboardButton(text=HIDDEN_TILE, callback_data=f"mtile_{uid}_{idx}"))
        rows.append(row)

    if not game_over and can_cash_out:
        rows.append([InlineKeyboardButton(text="💰 Cash Out", callback_data=f"mcash_{uid}")])

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
    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception:
        pass


# ==========================================
# REST API ENDPOINTS (FOR WEB MINI APP)
# ==========================================
@mines_router.get("/state/{user_id}")
async def api_get_state(user_id: str):
    db = load_db()
    ensure_user(user_id, "User", None)
    user_data = db["users"].get(user_id, {})
    balance = user_data.get("nexus_shards", 0)

    game = active_games.get(str(user_id))
    if not game:
        return {"balance": balance, "active": False}

    if time.time() - game["start_time"] > GAME_TIMEOUT:
        active_games.pop(str(user_id), None)
        global_stats = db.setdefault("mines_global", {})
        global_stats["total_taken"] = global_stats.get("total_taken", 0) + game["bet"]
        save_db()
        return {"balance": balance, "active": False}

    current_mult = fair_multiplier(game["mines"], game["gems_found"])
    cashout_val = int(game["bet"] * current_mult)
    can_cash = game["gems_found"] >= MIN_CASHOUT_GEMS

    revealed_map = {idx: ("mine" if game["board"][idx] else "gem") for idx in game["revealed"]}

    return {
        "balance": balance,
        "active": True,
        "current_mult": current_mult,
        "cashout_value": cashout_val,
        "can_cash_out": can_cash,
        "gems_found": game["gems_found"],
        "revealed": revealed_map
    }


@mines_router.post("/start")
async def api_start_game(req: StartGameReq):
    uid = str(req.user_id)
    db = load_db()
    ensure_user(uid, "User", None)

    if uid in active_games:
        game = active_games[uid]
        if time.time() - game["start_time"] > GAME_TIMEOUT:
            active_games.pop(uid, None)
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_taken"] = global_stats.get("total_taken", 0) + game["bet"]
            save_db()
        else:
            raise HTTPException(status_code=400, detail="Active round already in progress!")

    if req.bet < MIN_BET or req.bet > MAX_BET:
        raise HTTPException(status_code=400, detail=f"Bet must be between {MIN_BET} and {MAX_BET:,} 💠")
    if req.mines < MIN_MINES or req.mines > MAX_MINES:
        raise HTTPException(status_code=400, detail=f"Mines must be between {MIN_MINES} and {MAX_MINES}")

    user_data = db["users"][uid]
    if user_data.get("nexus_shards", 0) < req.bet:
        raise HTTPException(status_code=400, detail="Insufficient Shards for this bet.")

    user_data["nexus_shards"] -= req.bet
    user_data["mines_bet"] = user_data.get("mines_bet", 0) + req.bet

    global_stats = db.setdefault("mines_global", {})
    global_stats["total_bet"] = global_stats.get("total_bet", 0) + req.bet
    global_stats["total_games"] = global_stats.get("total_games", 0) + 1

    today_str = date.today().isoformat()
    daily_games = global_stats.setdefault("daily_games", {})
    daily_games[today_str] = daily_games.get(today_str, 0) + 1
    save_db()

    board = generate_board(req.mines)
    active_games[uid] = {
        "bet": req.bet,
        "mines": req.mines,
        "board": board,
        "revealed": set(),
        "gems_found": 0,
        "safe_tiles": BOARD_SIZE - req.mines,
        "lock": asyncio.Lock(),
        "start_time": time.time(),
    }

    return {"balance": user_data["nexus_shards"]}


@mines_router.post("/reveal")
async def api_reveal_tile(req: RevealTileReq):
    uid = str(req.user_id)
    game = active_games.get(uid)
    if not game:
        raise HTTPException(status_code=400, detail="No active round found.")

    idx = req.tile_index
    if idx < 0 or idx >= BOARD_SIZE:
        raise HTTPException(status_code=400, detail="Invalid tile index.")

    async with game["lock"]:
        if idx in game["revealed"]:
            raise HTTPException(status_code=400, detail="Tile already revealed.")

        apply_dda_balancing(uid, idx, game)
        db = load_db()

        # HIT MINE
        if game["board"][idx]:
            game["revealed"].add(idx)
            active_games.pop(uid, None)

            global_stats = db.setdefault("mines_global", {})
            global_stats["total_taken"] = global_stats.get("total_taken", 0) + game["bet"]
            save_db()

            full_board = ["mine" if b else "gem" for b in game["board"]]
            return {
                "result": "mine",
                "full_board": full_board,
                "boom_at": idx,
                "balance": db["users"][uid].get("nexus_shards", 0)
            }

        # SAFE GEM FOUND
        game["revealed"].add(idx)
        game["gems_found"] += 1
        current_mult = fair_multiplier(game["mines"], game["gems_found"])

        # BOARD CLEARED AUTO-WIN
        if game["gems_found"] >= game["safe_tiles"]:
            payout = int(game["bet"] * current_mult)
            net_profit_round = payout - game["bet"]

            db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + payout
            db["users"][uid]["mines_won"] = db["users"][uid].get("mines_won", 0) + payout

            global_stats = db.setdefault("mines_global", {})
            global_stats["total_won"] = global_stats.get("total_won", 0) + net_profit_round
            save_db()

            full_board = ["mine" if b else "gem" for b in game["board"]]
            active_games.pop(uid, None)

            return {
                "result": "cleared",
                "payout": payout,
                "full_board": full_board,
                "balance": db["users"][uid].get("nexus_shards", 0)
            }

        cashout_val = int(game["bet"] * current_mult)
        can_cash = game["gems_found"] >= MIN_CASHOUT_GEMS

        return {
            "result": "gem",
            "current_mult": current_mult,
            "cashout_value": cashout_val,
            "can_cash_out": can_cash,
            "gems_found": game["gems_found"]
        }


@mines_router.post("/cashout")
async def api_cashout(req: CashoutReq):
    uid = str(req.user_id)
    game = active_games.get(uid)
    if not game:
        raise HTTPException(status_code=400, detail="No active round found.")

    async with game["lock"]:
        if game["gems_found"] < MIN_CASHOUT_GEMS:
            raise HTTPException(status_code=400, detail="Must reveal at least 3 gems before cashing out.")

        current_mult = fair_multiplier(game["mines"], game["gems_found"])
        payout = int(game["bet"] * current_mult)
        net_profit_round = payout - game["bet"]

        db = load_db()
        db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + payout
        db["users"][uid]["mines_won"] = db["users"][uid].get("mines_won", 0) + payout

        global_stats = db.setdefault("mines_global", {})
        global_stats["total_won"] = global_stats.get("total_won", 0) + net_profit_round
        save_db()

        full_board = ["mine" if b else "gem" for b in game["board"]]
        active_games.pop(uid, None)

        return {
            "payout": payout,
            "full_board": full_board,
            "balance": db["users"][uid].get("nexus_shards", 0)
        }


# ==========================================
# /webmine COMMAND (OPENS MINI APP)
# ==========================================
@main_router.message(Command("webmine"))
async def webmine_cmd(message: Message):
    # Check if command is used outside of direct messages (group/supergroup/channel)
    if message.chat.type != "private":
        bot_info = await message.bot.get_me()
        bot_username = bot_info.username

        dm_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💬 Open in DM",
                        url=f"https://t.me/{bot_username}?start=webmine"
                    )
                ]
            ]
        )

        await message.reply(
            "<b>「 💣 MINES WEB MINI APP 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>Webmine is available in DM only!</b>\n\n"
            "Click the button below to switch to DM and launch the Mini App.",
            reply_markup=dm_keyboard,
            parse_mode=ParseMode.HTML
        )
        return

    uid = str(message.from_user.id)
    db = load_db()
    ensure_user(uid, message.from_user.first_name, message.from_user.username)

    web_url = db.get("settings", {}).get("mines_webapp_url", DEFAULT_WEBAPP_URL)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💣 Open Mines Mini App",
                    web_app=WebAppInfo(url=web_url)
                )
            ]
        ]
    )

    await message.reply(
        "<b>「 💣 MINES WEB MINI APP 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Click the button below to launch the Mini App interface and play Mines seamlessly!",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )


# ==========================================
# /mines COMMAND (INLINE BUTTON GAME)
# ==========================================
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
            await message.reply("⚠️ You already have an active round in progress.", parse_mode=ParseMode.HTML)
            return

    args = (command.args or "").split()
    if len(args) != 2:
        await message.reply(
            "<b>「 💣 MINES — HOW TO PLAY 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"<b>Usage:</b> <code>/mines &lt;bet&gt; &lt;mines&gt;</code>\n"
            f"<b>Example:</b> <code>/mines 50 3</code>\n\n"
            f"💡 Or play the Mini App: <code>/webmine</code>\n\n"
            f"💰 Bet: {MIN_BET} – {MAX_BET:,} 💠\n"
            f"💣 Mines: {MIN_MINES} – {MAX_MINES} (on a 25-tile board)",
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
        "chat_id": message.chat.id,
        "start_time": time.time(),
    }

    mines_image = db["settings"].get("mines_image")
    status_text = build_status_text(bet, mines, 0, 1.0)
    reply_markup = build_keyboard(uid, board, set(), can_cash_out=False)

    if mines_image:
        try:
            await message.reply_photo(photo=mines_image, caption=status_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except Exception:
            await message.reply(text=status_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await message.reply(text=status_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


# ==========================================
# BOT TILE TAP CALLBACK
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
        if idx in game["revealed"]:
            await cq.answer()
            return

        bet, mines, board = game["bet"], game["mines"], game["board"]
        apply_dda_balancing(owner_id, idx, game)

        # HIT MINE
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

        # SAFE GEM
        game["revealed"].add(idx)
        game["gems_found"] += 1
        current_mult = fair_multiplier(mines, game["gems_found"])

        # BOARD CLEARED
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


# ==========================================
# BOT CASH OUT CALLBACK
# ==========================================
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
        if game["gems_found"] < MIN_CASHOUT_GEMS:
            remaining = MIN_CASHOUT_GEMS - game["gems_found"]
            await cq.answer(f"Reveal {remaining} more tile{'s' if remaining != 1 else ''} before cashing out!", show_alert=True)
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


# ==========================================
# ADMIN COMMANDS
# ==========================================
@main_router.message(Command("gmstats"))
async def gmstats_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    db = load_db()
    global_stats = db.get("mines_global", {})
    total_won = global_stats.get("total_won", 0)
    total_taken = global_stats.get("total_taken", 0)
    total_games = global_stats.get("total_games", 0)

    today_str = date.today().isoformat()
    games_today = global_stats.get("daily_games", {}).get(today_str, 0)

    text = (
        "<b>「 📊 MINES GLOBAL STATS 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💠 <b>Total Shards Generated -</b> {total_won:,}\n"
        f"💸 <b>Total Shards Taken -</b> {total_taken:,}\n"
        f"🎮 <b>Total Games Played -</b> {total_games:,}\n"
        f"📅 <b>Games Played Today -</b> {games_today:,}\n"
        "━━━━━━━━━━━━━━━━━"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(Command("setweb"))
async def setweb_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return

    if not command.args:
        await message.reply("⚠️ Usage: <code>/setweb https://your-netlify-url.netlify.app</code>", parse_mode=ParseMode.HTML)
        return

    url = command.args.strip()
    db = load_db()
    db.setdefault("settings", {})["mines_webapp_url"] = url
    save_db()

    await message.reply(f"✅ Mines Web App URL updated to:\n<code>{url}</code>", parse_mode=ParseMode.HTML)


@main_router.message(Command("imm"))
async def imm_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply("⚠️ Reply to an image with <code>/imm</code> to set the Mines background photo.", parse_mode=ParseMode.HTML)
        return

    file_id = message.reply_to_message.photo[-1].file_id
    db = load_db()
    db["settings"]["mines_image"] = file_id
    save_db()

    await message.reply("✅ Mines background image saved.", parse_mode=ParseMode.HTML)
