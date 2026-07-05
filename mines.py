import asyncio
import random

from aiogram.types import (
    Message, 
    CallbackQuery, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    # Importing the custom/rich message types
    InputRichMessage,
    RichBlock,
    RichBlockSectionHeading,
    RichBlockParagraph,
    RichText,
    RichTextBold,
    RichTextTextMention
)
from aiogram.filters import Command, CommandObject
from aiogram import Bot

from config import main_router, load_db, save_db, ensure_user

# ==========================================
# SETTINGS
# ==========================================
BOARD_SIZE = 25            # fixed 5x5 board
MIN_MINES = 1
MAX_MINES = 23             # must leave at least 2 safe tiles
MIN_BET = 10
MAX_BET = 10_000
HOUSE_EDGE_PCT = 0.15       # flat house edge
MAX_MULTIPLIER = 20.0      # safety ceiling
MIN_CASHOUT_GEMS = 3       # Cash Out unlocks after this many safe reveals

GEM_EMOJI    = "💎"
BOMB_EMOJI   = "💣"
HIDDEN_EMOJI = "⬛"
BOOM_EMOJI   = "💥"

# In-memory active round state
active_games: dict = {}


# ==========================================
# GAME MATH
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


# ==========================================
# RENDERING (RICH MESSAGES)
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
                row.append(InlineKeyboardButton(text=HIDDEN_EMOJI, callback_data=f"mtile_{uid}_{idx}"))
        rows.append(row)

    if not game_over and can_cash_out:
        rows.append([InlineKeyboardButton(text="💰 Cash Out", callback_data=f"mcash_{uid}")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_status_rich(bet: int, mines: int, gems_found: int, current_mult: float) -> InputRichMessage:
    if gems_found < MIN_CASHOUT_GEMS:
        remaining = MIN_CASHOUT_GEMS - gems_found
        unlock_note = f"🔒 Cash Out unlocks in: {remaining} more reveal{'s' if remaining != 1 else ''}"
    else:
        unlock_note = "🔓 Cash Out unlocked!"

    return InputRichMessage(
        blocks=[
            RichBlock(
                elements=[
                    RichBlockSectionHeading(text="「 💣 MINES ぁ 」", size="large"),
                    RichBlockParagraph(
                        text=RichText(
                            elements=[
                                RichTextBold(text="Bet: "), f"{bet} 💠\n",
                                RichTextBold(text="Mines: "), f"{mines}\n",
                                RichTextBold(text="Gems Found: "), f"{gems_found}\n",
                                RichTextBold(text="Current Multiplier: "), f"{current_mult:.2f}x\n",
                                RichTextBold(text="Cash Out Value: "), f"{int(bet * current_mult)} 💠\n\n",
                                RichTextBold(text=unlock_note)
                            ]
                        )
                    ),
                    RichBlockParagraph(
                        text=RichText(elements=["Tap a tile to reveal it."])
                    )
                ]
            )
        ]
    )


def build_win_rich(bet: int, mines: int, gems_found: int, final_mult: float, payout: int, username: str, user_id: int) -> InputRichMessage:
    return InputRichMessage(
        blocks=[
            RichBlock(
                elements=[
                    RichBlockSectionHeading(text="「 🎉 CASHED OUT! 」", size="large"),
                    RichBlockParagraph(
                        text=RichText(
                            elements=[
                                RichTextBold(text="Player: "), RichTextTextMention(user_id=user_id, text=username), "\n",
                                RichTextBold(text="Bet: "), f"{bet} 💠\n",
                                RichTextBold(text="Mines: "), f"{mines}\n",
                                RichTextBold(text="Gems Found: "), f"{gems_found}\n",
                                RichTextBold(text="Final Multiplier: "), f"{final_mult:.2f}x\n",
                                RichTextBold(text="Payout: "), f"+{payout} 💠"
                            ]
                        )
                    )
                ]
            )
        ]
    )


def build_loss_rich(bet: int, mines: int, gems_found: int, username: str, user_id: int) -> InputRichMessage:
    return InputRichMessage(
        blocks=[
            RichBlock(
                elements=[
                    RichBlockSectionHeading(text="「 💥 BOOM! YOU HIT A MINE! 」", size="large"),
                    RichBlockParagraph(
                        text=RichText(
                            elements=[
                                RichTextBold(text="Player: "), RichTextTextMention(user_id=user_id, text=username), "\n",
                                RichTextBold(text="Bet Lost: "), f"{bet} 💠\n",
                                RichTextBold(text="Mines: "), f"{mines}\n",
                                RichTextBold(text="Gems Found: "), f"{gems_found}"
                            ]
                        )
                    )
                ]
            )
        ]
    )


# ==========================================
# /mines COMMAND
# ==========================================
@main_router.message(Command("mines"))
async def mines_cmd(message: Message, command: CommandObject, bot: Bot):
    uid = str(message.from_user.id)
    db = ensure_user(uid, message.from_user.first_name, message.from_user.username)

    if uid in active_games:
        await message.reply("⚠️ You already have a Mines round in progress — finish or cash out that one first.")
        return

    args = (command.args or "").split()
    if len(args) != 2:
        # Instruction block using the custom formatting structure
        help_msg = InputRichMessage(
            blocks=[
                RichBlock(
                    elements=[
                        RichBlockSectionHeading(text="「 💣 MINES — HOW TO PLAY 」", size="large"),
                        RichBlockParagraph(
                            text=RichText(
                                elements=[
                                    RichTextBold(text="Usage: "), "/mines <bet> <mines>\n",
                                    RichTextBold(text="Example: "), "/mines 50 3\n\n",
                                    RichTextBold(text="Bet limit: "), f"{MIN_BET} – {MAX_BET} 💠\n",
                                    RichTextBold(text="Mines count: "), f"{MIN_MINES} – {MAX_MINES} (on a 25-tile board)"
                                ]
                            )
                        )
                    ]
                )
            ]
        )
        await bot.send_rich_message(chat_id=message.chat.id, rich_message=help_msg)
        return

    try:
        bet = int(args[0])
        mines = int(args[1])
    except ValueError:
        await message.reply("Bet and mines must both be whole numbers.")
        return

    if bet < MIN_BET or bet > MAX_BET:
        await message.reply(f"Bet must be between {MIN_BET} and {MAX_BET} Shards 💠.")
        return
    if mines < MIN_MINES or mines > MAX_MINES:
        await message.reply(f"Mines must be between {MIN_MINES} and {MAX_MINES}.")
        return

    user_data = db["users"][uid]
    if user_data.get("nexus_shards", 0) < bet:
        await message.reply("You don't have enough Shards for that bet.")
        return

    # Debit balance
    user_data["nexus_shards"] -= bet
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
    }

    rich_status = build_status_rich(bet, mines, 0, 1.0)
    await bot.send_rich_message(
        chat_id=message.chat.id,
        rich_message=rich_status,
        reply_markup=build_keyboard(uid, board, set(), can_cash_out=False)
    )


# ==========================================
# TILE TAP CALLBACK
# ==========================================
@main_router.callback_query(lambda cq: cq.data and cq.data.startswith("mtile_"))
async def mines_tile_cb(cq: CallbackQuery, bot: Bot):
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

        if board[idx]:
            # Hit a mine
            game["revealed"].add(idx)
            active_games.pop(owner_id, None)
            await cq.answer("💥 Boom!", show_alert=False)
            
            # Rebuilding screen with loss text and updated keyboard
            rich_loss = build_loss_rich(bet, mines, game["gems_found"], cq.from_user.first_name, cq.from_user.id)
            try:
                await bot.edit_message_rich_text(
                    chat_id=cq.message.chat.id,
                    message_id=cq.message.message_id,
                    rich_message=rich_loss,
                    reply_markup=build_keyboard(owner_id, board, game["revealed"], boom_at=idx, game_over=True)
                )
            except Exception:
                pass
            return

        game["revealed"].add(idx)
        game["gems_found"] += 1
        current_mult = fair_multiplier(mines, game["gems_found"])

        # Cleared board win
        if game["gems_found"] >= game["safe_tiles"]:
            payout = int(bet * current_mult)
            db = load_db()
            db["users"][owner_id]["nexus_shards"] = db["users"][owner_id].get("nexus_shards", 0) + payout
            save_db()
            active_games.pop(owner_id, None)
            await cq.answer("🎉 Board cleared!", show_alert=False)
            
            rich_win = build_win_rich(bet, mines, game["gems_found"], current_mult, payout, cq.from_user.first_name, cq.from_user.id)
            try:
                await bot.edit_message_rich_text(
                    chat_id=cq.message.chat.id,
                    message_id=cq.message.message_id,
                    rich_message=rich_win,
                    reply_markup=build_keyboard(owner_id, board, game["revealed"])
                )
            except Exception:
                pass
            return

        await cq.answer()
        # Normal update
        rich_status = build_status_rich(bet, mines, game["gems_found"], current_mult)
        try:
            await bot.edit_message_rich_text(
                chat_id=cq.message.chat.id,
                message_id=cq.message.message_id,
                rich_message=rich_status,
                reply_markup=build_keyboard(owner_id, board, game["revealed"], can_cash_out=game["gems_found"] >= MIN_CASHOUT_GEMS)
            )
        except Exception:
            pass


# ==========================================
# CASH OUT CALLBACK
# ==========================================
@main_router.callback_query(lambda cq: cq.data and cq.data.startswith("mcash_"))
async def mines_cashout_cb(cq: CallbackQuery, bot: Bot):
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
            await cq.answer(f"Reveal {remaining} more tile{'s' if remaining != 1 else ''} before you can cash out!", show_alert=True)
            return

        bet, mines, board = game["bet"], game["mines"], game["board"]
        final_mult = fair_multiplier(mines, game["gems_found"])
        payout = int(bet * final_mult)

        db = load_db()
        db["users"][owner_id]["nexus_shards"] = db["users"][owner_id].get("nexus_shards", 0) + payout
        save_db()
        active_games.pop(owner_id, None)

        await cq.answer(f"✅ Cashed out: +{payout} 💠")
        
        rich_win = build_win_rich(bet, mines, game["gems_found"], final_mult, payout, cq.from_user.first_name, cq.from_user.id)
        try:
            await bot.edit_message_rich_text(
                chat_id=cq.message.chat.id,
                message_id=cq.message.message_id,
                rich_message=rich_win,
                reply_markup=build_keyboard(owner_id, board, game["revealed"])
            )
        except Exception:
            pass


@main_router.callback_query(lambda cq: cq.data == "mnoop")
async def mines_noop_cb(cq: CallbackQuery):
    await cq.answer()
