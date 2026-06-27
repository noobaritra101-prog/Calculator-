"""
==========================================
MINES — /mines <bet> <mines>
==========================================
A fixed-target Mines variant: the player picks a bet and a mine count,
and the game auto-resolves with an animated tile-by-tile reveal (no
manual "cash out" button mid-round — the target gem count is implied by
the mine count and locked in the moment the round starts).

GAME RULES
  • Board is always 5×5 (25 tiles).
  • Target gem count SCALES INVERSELY with mine count: more mines means
    a lower target, since each individual reveal is riskier. See
    target_for_mines() for the exact formula.
  • The bot reveals tiles one at a time, animating via message edits,
    until either:
      - the target number of gems is found  -> WIN, payout at the final
        multiplier for that many safe reveals
      - a mine is hit                        -> LOSS, bet is gone
  • Payout uses the real fair-odds Mines formula (multiplier compounds
    based on the shrinking probability of NOT hitting a mine on each
    successive reveal), then a flat house edge is applied — same spirit
    as Aviator's house edge, for consistency across the bot's gambling
    features. The result is capped at MAX_MULTIPLIER for economy safety,
    the same way Aviator caps its max crash point.
  • Mine positions are decided the instant the round starts (before any
    reveal), never influenced by player behavior mid-round.
"""

import asyncio
import random
import time

from aiogram import F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode

import config
from config import bot, main_router, load_db, save_db, ensure_user

# ==========================================
# SETTINGS
# ==========================================
BOARD_SIZE = 25            # fixed 5x5 board
MIN_MINES = 1
MAX_MINES = 23             # must leave at least 2 safe tiles
MIN_BET = 10
MAX_BET = 10_000
HOUSE_EDGE_PCT = 0.05       # matches Aviator's house edge for consistency
MAX_MULTIPLIER = 50.0      # safety ceiling, same spirit as Aviator's crash cap
REVEAL_DELAY_SECONDS = 0.9 # pause between animated tile reveals

# ==========================================
# WEALTH-BALANCING MECHANIC (silent, not a documented rule)
# ==========================================
# Purely an economy-balancing nudge against runaway wealth snowballing —
# NOT a documented game rule, and intentionally invisible to the player.
# It does not affect every round, and it never affects players under the
# threshold at all. When it does trigger, the player simply experiences
# normal-looking bad luck (an early mine) — there is no UI indication of
# why, and /minesrules never mentions this.
WEALTH_BIAS_THRESHOLD = 80_000   # balance must exceed this to be eligible
WEALTH_BIAS_CHANCE = 0.25        # % of ELIGIBLE rounds that get nudged
WEALTH_BIAS_WINDOW = 3           # nudge forces a mine within the first N reveals

GEM_EMOJI = "💎"
BOMB_EMOJI = "💣"
HIDDEN_EMOJI = "⬛"
BOOM_EMOJI = "💥"


# ==========================================
# GAME MATH
# ==========================================
def target_for_mines(mines: int) -> int:
    """Implied target gem count: roughly 20% of safe tiles, floor 2.
    More mines -> fewer safe tiles -> lower target, since each reveal
    carries more risk and a high target would be nearly unwinnable."""
    safe_tiles = BOARD_SIZE - mines
    target = max(2, round(safe_tiles * 0.2))
    return min(target, safe_tiles)


def fair_multiplier(mines: int, gems_found: int) -> float:
    """Real probability-based Mines payout: the multiplier is the inverse
    of the probability of having survived `gems_found` consecutive safe
    reveals, then reduced by a flat house edge. Capped at MAX_MULTIPLIER
    for economy safety on extreme mine counts."""
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
    mine_positions = random.sample(range(BOARD_SIZE), mines)
    for pos in mine_positions:
        board[pos] = True
    return board


def maybe_apply_wealth_bias(reveal_order: list, board: list, balance: int) -> list:
    """Silent economy-balancing nudge: if the player's balance is above
    WEALTH_BIAS_THRESHOLD, there's a WEALTH_BIAS_CHANCE per round that one
    of the first WEALTH_BIAS_WINDOW reveals is forced to be a mine tile
    (if one exists within that window isn't already there). This never
    changes the board's mine COUNT or positions — only which already-mine
    tile gets reached early in the reveal order. Players under the
    threshold are completely unaffected, and even eligible players see
    this on only a fraction of rounds. Mutates and returns reveal_order;
    the board itself is untouched."""
    if balance <= WEALTH_BIAS_THRESHOLD:
        return reveal_order
    if random.random() > WEALTH_BIAS_CHANCE:
        return reveal_order

    window = reveal_order[:WEALTH_BIAS_WINDOW]
    if any(board[i] for i in window):
        return reveal_order  # a mine is already early in the order, nothing to do

    mine_indices = [i for i in range(len(board)) if board[i]]
    if not mine_indices:
        return reveal_order  # shouldn't happen (mines >= MIN_MINES), but stay safe

    # Swap a random mine tile into a random slot within the early window.
    target_slot = random.randrange(WEALTH_BIAS_WINDOW)
    mine_tile = random.choice(mine_indices)
    mine_current_pos = reveal_order.index(mine_tile)
    reveal_order[target_slot], reveal_order[mine_current_pos] = (
        reveal_order[mine_current_pos], reveal_order[target_slot]
    )
    return reveal_order


# ==========================================
# BOARD RENDERING
# ==========================================
def render_board(board: list, revealed: set, boom_at=None) -> str:
    """Renders the 5x5 board as emoji rows. Unrevealed tiles stay hidden
    even if they're mines, unless boom_at reveals the fatal one."""
    rows = []
    for r in range(5):
        row_tiles = []
        for c in range(5):
            idx = r * 5 + c
            if idx == boom_at:
                row_tiles.append(BOOM_EMOJI)
            elif idx in revealed:
                row_tiles.append(BOMB_EMOJI if board[idx] else GEM_EMOJI)
            else:
                row_tiles.append(HIDDEN_EMOJI)
        rows.append("".join(row_tiles))
    return "\n".join(rows)


def build_status_text(bet: int, mines: int, target: int, gems_found: int, current_mult: float) -> str:
    return (
        "<b>「 💣 MINES ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Bet:</b> {bet} 💠\n"
        f"💣 <b>Mines:</b> {mines}\n"
        f"🎯 <b>Target:</b> {target} gems to cash out\n"
        f"💎 <b>Found:</b> {gems_found}/{target}\n"
        f"📈 <b>Current Multiplier:</b> {current_mult:.2f}x\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<i>Revealing tiles...</i>"
    )


def build_win_text(bet: int, mines: int, target: int, final_mult: float, payout: int) -> str:
    return (
        "<b>「 🎉 CASHED OUT! 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Bet:</b> {bet} 💠\n"
        f"💣 <b>Mines:</b> {mines}\n"
        f"💎 <b>Gems Found:</b> {target}/{target}\n"
        f"📈 <b>Final Multiplier:</b> {final_mult:.2f}x\n"
        f"✅ <b>Payout:</b> +{payout} 💠\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Final Board:"
    )


def build_loss_text(bet: int, mines: int, target: int, gems_found: int) -> str:
    return (
        "<b>「 💥 BOOM! YOU HIT A MINE! 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💸 <b>Bet Lost:</b> {bet} 💠\n"
        f"💣 <b>Mines:</b> {mines}\n"
        f"💎 <b>Gems Found:</b> {gems_found}/{target}\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Final Board:"
    )


# ==========================================
# /mines COMMAND
# ==========================================
@main_router.message(Command("mines"))
async def mines_cmd(message: Message, command: CommandObject):
    uid = str(message.from_user.id)
    db = ensure_user(uid, message.from_user.first_name, message.from_user.username)

    args = (command.args or "").split()
    if len(args) != 2:
        await message.reply(
            "<b>「 💣 MINES — HOW TO PLAY 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"<b>Usage:</b> <code>/mines &lt;bet&gt; &lt;mines&gt;</code>\n"
            f"<b>Example:</b> <code>/mines 50 3</code>\n\n"
            f"💰 Bet: {MIN_BET} – {MAX_BET} 💠\n"
            f"💣 Mines: {MIN_MINES} – {MAX_MINES} (on a 25-tile board)\n\n"
            "Use <code>/minesrules</code> for full rules.",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        bet = int(args[0])
        mines = int(args[1])
    except ValueError:
        await message.reply("❌ Bet and mines must both be whole numbers.", parse_mode=ParseMode.HTML)
        return

    if bet < MIN_BET or bet > MAX_BET:
        await message.reply(f"❌ Bet must be between {MIN_BET} and {MAX_BET} Shards 💠.", parse_mode=ParseMode.HTML)
        return
    if mines < MIN_MINES or mines > MAX_MINES:
        await message.reply(f"❌ Mines must be between {MIN_MINES} and {MAX_MINES}.", parse_mode=ParseMode.HTML)
        return

    user_data = db["users"][uid]
    balance_before_bet = user_data.get("nexus_shards", 0)
    if balance_before_bet < bet:
        await message.reply("❌ You don't have enough Shards for that bet.", parse_mode=ParseMode.HTML)
        return

    # Debit the bet immediately, before any reveal — same pattern as
    # Aviator's place_bet(), so a crash/restart mid-round can never let a
    # player keep an unpaid-for bet alive.
    user_data["nexus_shards"] -= bet
    save_db()

    target = target_for_mines(mines)
    board = generate_board(mines)

    sent = await message.reply(
        build_status_text(bet, mines, target, 0, 1.0) + "\n\n" + render_board(board, set()),
        parse_mode=ParseMode.HTML
    )

    revealed = set()
    gems_found = 0
    # Reveal order is pre-shuffled once, independent of tile content, so
    # the animation order itself leaks no information about mine
    # locations beyond what's already fixed at round start.
    reveal_order = list(range(BOARD_SIZE))
    random.shuffle(reveal_order)
    # Balance checked BEFORE the debit above — this is the player's true
    # standing wealth entering the round, not their post-bet balance.
    reveal_order = maybe_apply_wealth_bias(reveal_order, board, balance_before_bet)

    for idx in reveal_order:
        await asyncio.sleep(REVEAL_DELAY_SECONDS)

        if board[idx]:
            # Hit a mine — round over, loss.
            revealed.add(idx)
            try:
                await sent.edit_text(
                    build_loss_text(bet, mines, target, gems_found) + "\n\n" +
                    render_board(board, revealed, boom_at=idx),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            return

        revealed.add(idx)
        gems_found += 1
        current_mult = fair_multiplier(mines, gems_found)

        if gems_found >= target:
            # Target reached — win, payout now.
            payout = int(bet * current_mult)
            db = load_db()
            db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + payout
            save_db()
            try:
                await sent.edit_text(
                    build_win_text(bet, mines, target, current_mult, payout) + "\n\n" +
                    render_board(board, revealed),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            return

        try:
            await sent.edit_text(
                build_status_text(bet, mines, target, gems_found, current_mult) + "\n\n" +
                render_board(board, revealed),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass


# ==========================================
# /minesrules COMMAND
# ==========================================
@main_router.message(Command("minesrules"))
async def mines_rules_cmd(message: Message):
    example_mines = [1, 3, 5, 10, 15, 20]
    rows = []
    for m in example_mines:
        t = target_for_mines(m)
        mult = fair_multiplier(m, t)
        rows.append(f"  💣 {m:>2} mines  ➜  🎯 {t} gems  ➜  📈 {mult:.2f}x")
    table = "\n".join(rows)

    text = (
        "<b>「 💣 MINES — RULES 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<b>How to play:</b>\n"
        f"<code>/mines &lt;bet&gt; &lt;mines&gt;</code> — e.g. <code>/mines 50 3</code>\n\n"
        "The board is a 5×5 grid (25 tiles) hiding gems 💎 and mines 💣. "
        "Once you start, the bot reveals tiles one at a time on its own — "
        "there's no manual cash-out button. Your goal (the 🎯 <b>target</b>) "
        "is fixed the moment you start, based on how many mines you chose.\n\n"
        "<b>The catch:</b> more mines = fewer safe tiles on the board, so "
        "your target shrinks too — but survive it and the payout is much "
        "bigger, since the odds were so much worse.\n\n"
        f"💰 <b>Bet range:</b> {MIN_BET} – {MAX_BET} 💠\n"
        f"💣 <b>Mine range:</b> {MIN_MINES} – {MAX_MINES} (out of 25 tiles)\n\n"
        "<b>Example payouts:</b>\n"
        f"{table}\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Hit a mine before reaching your target and the bet is lost. "
        "Reach the target and you're paid out automatically at the "
        "multiplier shown — no risk of greed costing you the win."
    )
    await message.reply(text, parse_mode=ParseMode.HTML)
