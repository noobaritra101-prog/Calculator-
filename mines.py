"""
====================================================================
MINES — Telegram Bot & Web Mini App Unified Engine
====================================================================
• Netlify App URL: https://famous-centaur-493f76.netlify.app
• Railway Backend: https://calculator-production-75bf.up.railway.app
====================================================================
"""

import asyncio
import random
import time
from datetime import date
from typing import Optional

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import main_router, load_db, save_db, ensure_user, ADMIN_IDS

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
NETLIFY_WEBAPP_URL = "https://famous-centaur-493f76.netlify.app"
RAILWAY_BACKEND_URL = "https://calculator-production-75bf.up.railway.app"

BOARD_SIZE = 25            # 5x5 Grid
MIN_MINES = 3              # Min bombs
MAX_MINES = 23             # Max bombs
MIN_BET = 10
MAX_BET = 30000            # Max bet
HOUSE_EDGE_PCT = 0.15      # 15% house edge
MAX_MULTIPLIER = 20.0      # Safety cap
MIN_CASHOUT_GEMS = 3       # Safe unlocks
GAME_TIMEOUT = 600         # 10 minutes limit

GEM_EMOJI = "💎"
BOMB_EMOJI = "💣"
BOOM_EMOJI = "💥"
HIDDEN_TILE = "•"

# Dynamic Difficulty Adjustment (DDA)
TARGET_NET = 0
RECOVERY_SCALE = 5000

# Unified In-Memory Active State (Keyed by str(user_id))
active_games: dict = {}

# FastAPI Router Export for main.py / card_aio.py
web_mines_router = APIRouter(prefix="/api/mines", tags=["Mines Web App"])


# ==========================================
# GAME MATH & CORE FUNCTIONS
# ==========================================
def fair_multiplier(mines: int, gems_found: int) -> float:
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
    board = [False] * BOARD_SIZE
    for pos in random.sample(range(BOARD_SIZE), mines):
        board[pos] = True
    return board


def apply_dda_check(uid: str, bet: int, board: list, idx: int, gems_found: int):
    """Executes identical Rubber-Band DDA balancing across Bot & Web App."""
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
# TELEGRAM BOT UI BUILDERS
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

    # Always provide Web App launcher button
    rows.append([InlineKeyboardButton(text="📱 Open Web App", web_app=WebAppInfo(url=NETLIFY_WEBAPP_URL))])
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
        "<i>Tap a tile to reveal or launch Web App below.</i>"
    )


# ==========================================
# TELEGRAM BOT COMMANDS & CALLBACKS
# ==========================================
@main_router.message(Command("play"))
@main_router.message(Command("app"))
async def play_webapp_cmd(message: Message):
    """Directly sends the Web Mini App launcher button."""
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💣 Play Mines Web App", web_app=WebAppInfo(url=NETLIFY_WEBAPP_URL))]
    ])
    await message.reply(
        "<b>「 💣 MINES WEB MINI APP 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Tap the button below to launch the Mines Web Mini App!",
        reply_markup=markup,
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
                "⚠️ You already have an active Mines round! Finish or cash out first.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📱 Continue in Web App", web_app=WebAppInfo(url=NETLIFY_WEBAPP_URL))]
                ]),
                parse_mode=ParseMode.HTML
            )
            return

    args = (command.args or "").split()
    if len(args) != 2:
        await message.reply(
            "<b>「 💣 MINES — HOW TO PLAY 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "<b>Usage:</b> <code>/mines &lt;bet&gt; &lt;mines&gt;</code>\n"
            "<b>Example:</b> <code>/mines 50 3</code>\n\n"
            f"💰 Bet: {MIN_BET} – {MAX_BET:,} 💠\n"
            f"💣 Mines: {MIN_MINES} – {MAX_MINES}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Or Launch Web App", web_app=WebAppInfo(url=NETLIFY_WEBAPP_URL))]
            ]),
            parse_mode=ParseMode.HTML
        )
        return

    try:
        bet, mines = int(args[0]), int(args[1])
    except ValueError:
        await message.reply("Bet and mines must be whole numbers.", parse_mode=ParseMode.HTML)
        return

    if bet < MIN_BET or bet > MAX_BET or mines < MIN_MINES or mines > MAX_MINES:
        await message.reply(f"Invalid inputs! Bet: {MIN_BET}-{MAX_BET:,}, Mines: {MIN_MINES}-{MAX_MINES}.", parse_mode=ParseMode.HTML)
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

    status_text = build_status_text(bet, mines, 0, 1.0)
    reply_markup = build_keyboard(uid, board, set(), can_cash_out=False)
    await message.reply(status_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


@main_router.callback_query(lambda cq: cq.data and cq.data.startswith("mtile_"))
async def mines_tile_cb(cq: CallbackQuery):
    _, owner_id, idx_str = cq.data.split("_")
    if str(cq.from_user.id) != owner_id:
        await cq.answer("⚠️ This isn't your round!", show_alert=True)
        return

    game = active_games.get(owner_id)
    if not game:
        await cq.answer("This round has ended.", show_alert=True)
        return

    idx = int(idx_str)

    async with game["lock"]:
        if active_games.get(owner_id) is not game:
            await cq.answer("This round has ended.", show_alert=True)
            return

        if idx in game["revealed"]:
            await cq.answer()
            return

        bet, mines, board = game["bet"], game["mines"], game["board"]
        apply_dda_check(owner_id, bet, board, idx, game["gems_found"])

        if board[idx]:
            game["revealed"].add(idx)
            active_games.pop(owner_id, None)
            
            db = load_db()
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_taken"] = global_stats.get("total_taken", 0) + bet
            save_db()

            await cq.answer("💥 Boom!", show_alert=False)
            await cq.message.edit_text(
                f"<b>「 💥 BOOM! YOU HIT A MINE! 」</b>\n━━━━━━━━━━━━━━━━━\n💸 <b>Bet Lost:</b> {bet} 💠\n💣 <b>Mines:</b> {mines}\n💎 <b>Gems Found:</b> {game['gems_found']}\n━━━━━━━━━━━━━━━━━",
                reply_markup=build_keyboard(owner_id, board, game["revealed"], boom_at=idx, game_over=True),
                parse_mode=ParseMode.HTML
            )
            return

        game["revealed"].add(idx)
        game["gems_found"] += 1
        current_mult = fair_multiplier(mines, game["gems_found"])

        if game["gems_found"] >= game["safe_tiles"]:
            payout = int(bet * current_mult)
            db = load_db()
            db["users"][owner_id]["nexus_shards"] = db["users"][owner_id].get("nexus_shards", 0) + payout
            db["users"][owner_id]["mines_won"] = db["users"][owner_id].get("mines_won", 0) + payout
            
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_won"] = global_stats.get("total_won", 0) + (payout - bet)
            save_db()

            active_games.pop(owner_id, None)
            await cq.answer("🎉 Board cleared!", show_alert=False)
            await cq.message.edit_text(
                f"<b>「 🎉 CASHED OUT! 」</b>\n━━━━━━━━━━━━━━━━━\n💰 <b>Bet:</b> {bet} 💠\n📈 <b>Multiplier:</b> {current_mult:.2f}x\n✅ <b>Payout:</b> +{payout} 💠\n━━━━━━━━━━━━━━━━━",
                reply_markup=build_keyboard(owner_id, board, game["revealed"]),
                parse_mode=ParseMode.HTML
            )
            return

        await cq.answer()
        await cq.message.edit_text(
            build_status_text(bet, mines, game["gems_found"], current_mult),
            reply_markup=build_keyboard(owner_id, board, game["revealed"], can_cash_out=game["gems_found"] >= MIN_CASHOUT_GEMS),
            parse_mode=ParseMode.HTML
        )


@main_router.callback_query(lambda cq: cq.data and cq.data.startswith("mcash_"))
async def mines_cashout_cb(cq: CallbackQuery):
    owner_id = cq.data.split("_")[1]
    if str(cq.from_user.id) != owner_id:
        await cq.answer("⚠️ This isn't your round!", show_alert=True)
        return

    game = active_games.get(owner_id)
    if not game:
        await cq.answer("This round has ended.", show_alert=True)
        return

    async with game["lock"]:
        if active_games.get(owner_id) is not game:
            await cq.answer("This round has ended.", show_alert=True)
            return

        if game["gems_found"] < MIN_CASHOUT_GEMS:
            await cq.answer(f"Reveal {MIN_CASHOUT_GEMS - game['gems_found']} more tiles before cashout!", show_alert=True)
            return

        bet, mines, board = game["bet"], game["mines"], game["board"]
        final_mult = fair_multiplier(mines, game["gems_found"])
        payout = int(bet * final_mult)

        db = load_db()
        db["users"][owner_id]["nexus_shards"] = db["users"][owner_id].get("nexus_shards", 0) + payout
        db["users"][owner_id]["mines_won"] = db["users"][owner_id].get("mines_won", 0) + payout
        
        global_stats = db.setdefault("mines_global", {})
        global_stats["total_won"] = global_stats.get("total_won", 0) + (payout - bet)
        save_db()

        active_games.pop(owner_id, None)
        await cq.answer(f"✅ Cashed out: +{payout} 💠")
        await cq.message.edit_text(
            f"<b>「 🎉 CASHED OUT! 」</b>\n━━━━━━━━━━━━━━━━━\n💰 <b>Bet:</b> {bet} 💠\n📈 <b>Multiplier:</b> {final_mult:.2f}x\n✅ <b>Payout:</b> +{payout} 💠\n━━━━━━━━━━━━━━━━━",
            reply_markup=build_keyboard(owner_id, board, game["revealed"]),
            parse_mode=ParseMode.HTML
        )


@main_router.callback_query(lambda cq: cq.data == "mnoop")
async def mines_noop_cb(cq: CallbackQuery):
    await cq.answer()


@main_router.message(Command("gmstats"))
async def gmstats_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    db = load_db()
    stats = db.get("mines_global", {})
    await message.reply(
        f"<b>「 📊 MINES GLOBAL STATS 」</b>\n━━━━━━━━━━━━━━━━━\n"
        f"💠 <b>Generated:</b> {stats.get('total_won', 0):,}\n"
        f"💸 <b>Taken:</b> {stats.get('total_taken', 0):,}\n"
        f"🎮 <b>Total Games:</b> {stats.get('total_games', 0):,}\n━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML
    )


# ==========================================
# FASTAPI WEB APP API ENDPOINTS
# ==========================================
class StartReq(BaseModel):
    user_id: str
    first_name: Optional[str] = "Player"
    username: Optional[str] = "Player"
    bet: int
    mines: int

class RevealReq(BaseModel):
    user_id: str
    tile_index: int

class CashoutReq(BaseModel):
    user_id: str


@web_mines_router.get("/state/{user_id}")
async def get_state(user_id: str):
    uid = str(user_id)
    db = load_db()
    user_data = db.get("users", {}).get(uid, {})
    balance = user_data.get("nexus_shards", 0)

    game = active_games.get(uid)
    if not game:
        return {"active": False, "balance": balance}

    if time.time() - game["start_time"] > GAME_TIMEOUT:
        active_games.pop(uid, None)
        return {"active": False, "balance": balance}

    current_mult = fair_multiplier(game["mines"], game["gems_found"])
    revealed_tiles = {str(idx): ("mine" if game["board"][idx] else "gem") for idx in game["revealed"]}

    return {
        "active": True,
        "balance": balance,
        "bet": game["bet"],
        "mines": game["mines"],
        "gems_found": game["gems_found"],
        "current_mult": round(current_mult, 2),
        "cashout_value": int(game["bet"] * current_mult),
        "can_cash_out": game["gems_found"] >= MIN_CASHOUT_GEMS,
        "revealed": revealed_tiles
    }


@web_mines_router.post("/start")
async def start_game_web(req: StartReq):
    uid = str(req.user_id)
    db = load_db()
    ensure_user(uid, req.first_name, req.username)

    if uid in active_games:
        game = active_games[uid]
        if time.time() - game["start_time"] <= GAME_TIMEOUT:
            raise HTTPException(status_code=400, detail="You already have an active round!")
        active_games.pop(uid, None)

    if req.bet < MIN_BET or req.bet > MAX_BET or req.mines < MIN_MINES or req.mines > MAX_MINES:
        raise HTTPException(status_code=400, detail="Invalid bet or mine parameters.")

    user_data = db["users"][uid]
    if user_data.get("nexus_shards", 0) < req.bet:
        raise HTTPException(status_code=400, detail="Insufficient Shards balance!")

    user_data["nexus_shards"] -= req.bet
    user_data["mines_bet"] = user_data.get("mines_bet", 0) + req.bet

    global_stats = db.setdefault("mines_global", {})
    global_stats["total_bet"] = global_stats.get("total_bet", 0) + req.bet
    global_stats["total_games"] = global_stats.get("total_games", 0) + 1
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

    return {
        "status": "started",
        "balance": user_data["nexus_shards"],
        "bet": req.bet,
        "mines": req.mines,
        "gems_found": 0,
        "current_mult": 1.0,
        "can_cash_out": False
    }


@web_mines_router.post("/reveal")
async def reveal_tile_web(req: RevealReq):
    uid = str(req.user_id)
    game = active_games.get(uid)
    if not game:
        raise HTTPException(status_code=400, detail="No active game.")

    async with game["lock"]:
        if active_games.get(uid) is not game:
            raise HTTPException(status_code=400, detail="Game ended.")

        idx = req.tile_index
        if idx in game["revealed"]:
            raise HTTPException(status_code=400, detail="Already revealed.")

        bet, mines, board = game["bet"], game["mines"], game["board"]
        apply_dda_check(uid, bet, board, idx, game["gems_found"])

        # Mine hit
        if board[idx]:
            game["revealed"].add(idx)
            active_games.pop(uid, None)
            
            db = load_db()
            global_stats = db.setdefault("mines_global", {})
            global_stats["total_taken"] = global_stats.get("total_taken", 0) + bet
            save_db()

            full_board = {str(i): ("mine" if board[i] else "gem") for i in range(BOARD_SIZE)}
            return {
                "result": "mine",
                "boom_at": idx,
                "full_board": full_board,
                "balance": db["users"][uid].get("nexus_shards", 0)
            }

        game["revealed"].add(idx)
        game["gems_found"] += 1
        current_mult = fair_multiplier(mines, game["gems_found"])

        # Cleared board
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
            return {
                "result": "cleared",
                "payout": payout,
                "multiplier": round(current_mult, 2),
                "full_board": full_board,
                "balance": db["users"][uid]["nexus_shards"]
            }

        return {
            "result": "gem",
            "tile_index": idx,
            "gems_found": game["gems_found"],
            "current_mult": round(current_mult, 2),
            "cashout_value": int(bet * current_mult),
            "can_cash_out": game["gems_found"] >= MIN_CASHOUT_GEMS
        }


@web_mines_router.post("/cashout")
async def cashout_web(req: CashoutReq):
    uid = str(req.user_id)
    game = active_games.get(uid)
    if not game:
        raise HTTPException(status_code=400, detail="No active game.")

    async with game["lock"]:
        if active_games.get(uid) is not game:
            raise HTTPException(status_code=400, detail="Game ended.")

        if game["gems_found"] < MIN_CASHOUT_GEMS:
            raise HTTPException(status_code=400, detail=f"Reveal {MIN_CASHOUT_GEMS - game['gems_found']} more gems to cash out!")

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

        return {
            "status": "success",
            "payout": payout,
            "multiplier": round(final_mult, 2),
            "full_board": full_board,
            "balance": db["users"][uid]["nexus_shards"]
        }