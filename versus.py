import time
import random
import asyncio
import hashlib
from aiogram import F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.enums import ParseMode

from config import (
    bot, main_router, ADMIN_IDS,
    format_rarity, load_db, save_db,
    ensure_user, get_mention,
    is_ghost_banned, is_shadow_banned
)

# ==========================================
# CONSTANTS
# ==========================================
VERSUS_COOLDOWN   = 900   # 15 minutes between /versus uses
VERSUS_DAILY_CAP  = 10    # max duels per day
ACCEPT_TIMEOUT    = 30    # seconds to accept challenge
DRAFT_TIMEOUT     = 30    # seconds per draft turn

ROLES = [
    "Jinchuuriki",
    "Domain User",
    "Bankai",
    "Specialist",
    "Cursed Vessel",
    "Sage",
    "Arrancar",
    "Grim Pact",
]

ROLE_BONUSES = {
    "Jinchuuriki":   {"atk": 25, "def":  0, "spd": 10},
    "Domain User":   {"atk": 10, "def": 10, "spd": 10},
    "Bankai":        {"atk":  0, "def":  0, "spd":  0},
    "Specialist":    {"atk": 15, "def":  5, "spd": 15},
    "Cursed Vessel": {"atk":  0, "def": 30, "spd":  0},
    "Sage":          {"atk":  5, "def":  5, "spd":  5},
    "Arrancar":      {"atk": 20, "def": 20, "spd": 20},
    "Grim Pact":     {"atk":  0, "def":  0, "spd":  0},
}

RARITY_BASE = {
    "Basic 🃏":  {"atk": 40, "def": 40, "spd": 40},
    "Elite ⚓":  {"atk": 65, "def": 65, "spd": 65},
    "Divine ❄️": {"atk": 90, "def": 90, "spd": 90},
}

SHARD_WIN    = 200
SHARD_LOSE   = 30
SHARD_UPSET  = 350   # win with all Basic cards
SHARD_ARRREV = 50    # Arrancar switched and that side won

# In-memory state
active_versus: dict = {}
_versus_cooldowns: dict[int, float] = {}
_versus_daily: dict[str, int] = {}   # "uid_YYYY-MM-DD" → count


# ==========================================
# HELPERS
# ==========================================
def _today_key(uid: int) -> str:
    from datetime import datetime, timezone
    d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{uid}_{d}"


def get_variance(card_id: str) -> tuple[int, int, int]:
    h = int(hashlib.md5(card_id.encode()).hexdigest(), 16)
    atk_v = (h % 21) - 10
    def_v = ((h >> 8) % 21) - 10
    spd_v = ((h >> 16) % 21) - 10
    return atk_v, def_v, spd_v


def get_card_stats(card_id: str, rarity: str, role: str,
                   atk_bonus: int = 0, def_bonus: int = 0, spd_bonus: int = 0) -> dict:
    base  = RARITY_BASE.get(rarity, RARITY_BASE["Basic 🃏"])
    var   = get_variance(card_id)
    rb    = ROLE_BONUSES[role]
    return {
        "atk": base["atk"] + var[0] + rb["atk"] + atk_bonus,
        "def": base["def"] + var[1] + rb["def"] + def_bonus,
        "spd": base["spd"] + var[2] + rb["spd"] + spd_bonus,
    }


def _state_key(uid_a: int, uid_b: int) -> frozenset:
    return frozenset({uid_a, uid_b})


def _get_hand(uid: int, db: dict) -> list[str]:
    """Return all card_ids the user owns (amount >= 1), capped at 20 for button display."""
    user_cards = db["users"].get(str(uid), {}).get("cards", {})
    owned = [cid for cid, cdata in user_cards.items() if cdata.get("amount", 0) >= 1]
    random.shuffle(owned)
    return owned[:20]


def _build_board(state: dict, db: dict, viewer_uid: int) -> str:
    """Build the live draft board text."""
    uid_a    = state["challenger"]
    uid_b    = state["opponent"]
    name_a   = state["name_a"]
    name_b   = state["name_b"]
    roster_a = state["roster_a"]
    roster_b = state["roster_b"]

    def role_line(role: str, roster: dict, is_opponent_view: bool) -> str:
        cid = roster.get(role)
        if not cid:
            return f"  {role:<16} ➜  . . ."
        if role == "Arrancar" and is_opponent_view:
            return f"  {role:<16} ➜  ░░░░░░░░░"
        cdata = db["global_cards"].get(cid, {})
        return f"  {role:<16} ➜  {cdata.get('name', '?')}  《 {format_rarity(cdata.get('rarity',''))} 》"

    is_a_opponent = (viewer_uid == uid_b)
    is_b_opponent = (viewer_uid == uid_a)

    lines_a = "\n".join(role_line(r, roster_a, is_a_opponent) for r in ROLES)
    lines_b = "\n".join(role_line(r, roster_b, is_b_opponent) for r in ROLES)

    turn_uid  = state["draft_turn"]
    turn_name = name_a if turn_uid == uid_a else name_b
    slots_done = len(roster_a) + len(roster_b)

    text = (
        f"<b>「 ⚡ NEXUS AWAKENING — Draft ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🔵 {name_a}\n"
        f"{lines_a}\n\n"
        f"🔴 {name_b}\n"
        f"{lines_b}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🎮 Turn  ➜  {turn_name}\n"
        f"📋 Progress  ➜  {slots_done}/16 roles filled\n"
        f"⏳ {DRAFT_TIMEOUT}s to pick"
    )
    return text


def _build_card_buttons(hand: list[str], db: dict, key_prefix: str) -> InlineKeyboardMarkup:
    """Build inline buttons for card selection."""
    buttons = []
    row = []
    for cid in hand:
        cdata = db["global_cards"].get(cid, {})
        name  = cdata.get("name", "?")
        rar   = format_rarity(cdata.get("rarity", ""))
        label = f"{name} {rar}"
        row.append(InlineKeyboardButton(text=label, callback_data=f"{key_prefix}_{cid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Cancel Versus", callback_data=f"{key_prefix}_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_role_buttons(roster: dict, key_prefix: str) -> InlineKeyboardMarkup:
    """Build inline buttons for role selection (only empty roles shown)."""
    empty_roles = [r for r in ROLES if r not in roster]
    buttons = []
    row = []
    for role in empty_roles:
        row.append(InlineKeyboardButton(text=role, callback_data=f"{key_prefix}_{role}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Cancel Versus", callback_data=f"{key_prefix}_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==========================================
# BATTLE CALCULATION
# ==========================================
def resolve_battle(state: dict, db: dict) -> dict:
    """
    Resolve all 8 role clashes and return a results dict.
    Order:
      1. Apply Domain User & Sage team-wide buffs
      2. Clash roles 1–7 (skip Arrancar for now)
      3. Check Grim Pact after clash 4
      4. Resolve Arrancar (joins winning side for clash 8)
      5. Final score + winner
    """
    uid_a    = state["challenger"]
    uid_b    = state["opponent"]
    roster_a = state["roster_a"]
    roster_b = state["roster_b"]

    # ── Team-wide buff pass ──────────────────────────────
    def team_buffs(roster: dict) -> tuple[int, int, int]:
        atk_b = def_b = spd_b = 0
        # Domain User: +10 to all 7 OTHER cards
        if "Domain User" in roster:
            atk_b += 10; def_b += 10; spd_b += 10
        # Sage: +8 to all other cards
        if "Sage" in roster:
            atk_b += 8; def_b += 8; spd_b += 8
        return atk_b, def_b, spd_b

    buf_a = team_buffs(roster_a)
    buf_b = team_buffs(roster_b)

    def get_stats(roster: dict, role: str, buf: tuple,
                  grim_losing: bool = False, bankai_full: bool = True) -> dict:
        cid   = roster[role]
        cdata = db["global_cards"].get(cid, {})
        rar   = format_rarity(cdata.get("rarity", "Basic 🃏"))
        stats = get_card_stats(cid, rar, role, buf[0], buf[1], buf[2])

        # Bankai — always full power (no round restriction in single-calc mode)
        if role == "Bankai":
            stats["atk"] = int(stats["atk"] * 2.5)
            stats["def"] = int(stats["def"] * 2.5)
            stats["spd"] = int(stats["spd"] * 2.5)

        # Grim Pact — ATK ×2 if team was losing after clash 4
        if role == "Grim Pact" and grim_losing:
            stats["atk"] = int(stats["atk"] * 2)

        return stats

    def clash(role: str, roster_x: dict, roster_y: dict,
              buf_x: tuple, buf_y: tuple,
              grim_x: bool = False, grim_y: bool = False) -> dict:
        """Returns: winner 'a'/'b'/'draw', damage_x, damage_y, stats_x, stats_y"""
        sx = get_stats(roster_x, role, buf_x, grim_x)
        sy = get_stats(roster_y, role, buf_y, grim_y)

        # Cursed Vessel reflect — defender reflects 50% damage back
        dmg_x = max(5, sx["atk"] - sy["def"])
        dmg_y = max(5, sy["atk"] - sx["def"])

        if role == "Cursed Vessel":
            # The Cursed Vessel holder reflects 50% of incoming damage
            # x attacks y (Cursed Vessel): y reflects 50% back to x
            reflected = int(dmg_x * 0.5)
            dmg_x = max(1, dmg_x - reflected)
        # For symmetry: if x is Cursed Vessel we already handled it above
        # If y is Cursed Vessel, it reflects damage from x
        # (In versus both have same roles so both or neither have CV)

        net_x = dmg_x - dmg_y
        if net_x > 0:
            winner = "a"
        elif net_x < 0:
            winner = "b"
        else:
            # SPD tiebreak
            if sx["spd"] > sy["spd"]:   winner = "a"
            elif sy["spd"] > sx["spd"]: winner = "b"
            else:                        winner = "draw"

        return {"winner": winner, "dmg_a": dmg_x, "dmg_b": dmg_y,
                "stats_a": sx, "stats_b": sy}

    score_a = score_b = 0
    clash_results = {}

    # ── Clashes 1–7 (skip Arrancar) ────────────────────
    non_arrancar = [r for r in ROLES if r != "Arrancar"]
    for i, role in enumerate(non_arrancar):
        result = clash(role, roster_a, roster_b, buf_a, buf_b)
        clash_results[role] = result
        if result["winner"] == "a":
            score_a += 1
        elif result["winner"] == "b":
            score_b += 1
        else:
            score_a += 0.5
            score_b += 0.5

        # Grim Pact check after clash 4 (index 3)
        if i == 3:
            grim_a_active = score_a < score_b
            grim_b_active = score_b < score_a

    # ── Arrancar — joins winning side at clash 8 ────────
    arrancar_switched = False
    arrancar_side = None
    if score_a >= score_b:
        # Arrancar joins A's team (fights as A)
        arrancar_switched = (state["challenger"] != uid_a)  # always False here, side note
        arrancar_side = "a"
        # Arrancar uses A's buffs and fights for A
        sa = get_stats(roster_a, "Arrancar", buf_a)
        sb = get_stats(roster_b, "Arrancar", buf_b)
        # Arrancar joins winning side: its ATK+DEF+SPD bonus already baked in
        # The opponent is B's Arrancar card used normally
        dmg_a = max(5, sa["atk"] - sb["def"])
        dmg_b = max(5, sb["atk"] - sa["def"])
        if dmg_a > dmg_b:
            winner = "a"
        elif dmg_b > dmg_a:
            winner = "b"
        else:
            winner = "a" if sa["spd"] >= sb["spd"] else "b"
    else:
        arrancar_side = "b"
        sa = get_stats(roster_a, "Arrancar", buf_a)
        sb = get_stats(roster_b, "Arrancar", buf_b)
        dmg_a = max(5, sa["atk"] - sb["def"])
        dmg_b = max(5, sb["atk"] - sa["def"])
        if dmg_b > dmg_a:
            winner = "b"
        elif dmg_a > dmg_b:
            winner = "a"
        else:
            winner = "b" if sb["spd"] >= sa["spd"] else "a"

    clash_results["Arrancar"] = {
        "winner": winner, "dmg_a": dmg_a, "dmg_b": dmg_b,
        "stats_a": sa, "stats_b": sb,
        "arrancar_side": arrancar_side
    }
    if winner == "a":   score_a += 1
    elif winner == "b": score_b += 1
    else:               score_a += 0.5; score_b += 0.5

    # ── Final winner ────────────────────────────────────
    if score_a > score_b:   overall_winner = uid_a
    elif score_b > score_a: overall_winner = uid_b
    else:                   overall_winner = None  # draw

    # Upset check: winner used only Basic cards
    def all_basic(roster: dict) -> bool:
        for cid in roster.values():
            cdata = db["global_cards"].get(cid, {})
            if format_rarity(cdata.get("rarity", "")) != "Basic 🃏":
                return False
        return True

    upset_bonus = False
    if overall_winner == uid_a and all_basic(roster_a):  upset_bonus = True
    if overall_winner == uid_b and all_basic(roster_b):  upset_bonus = True

    # Arrancar bonus
    arr_bonus = False
    if (arrancar_side == "a" and overall_winner == uid_a) or \
       (arrancar_side == "b" and overall_winner == uid_b):
        arr_bonus = True

    return {
        "clash_results": clash_results,
        "score_a": score_a,
        "score_b": score_b,
        "winner": overall_winner,
        "upset_bonus": upset_bonus,
        "arr_bonus": arr_bonus,
        "arrancar_side": arrancar_side,
    }


# ==========================================
# RESULT MESSAGE BUILDER
# ==========================================
def build_result_text(state: dict, battle: dict, db: dict) -> str:
    uid_a    = state["challenger"]
    uid_b    = state["opponent"]
    name_a   = state["name_a"]
    name_b   = state["name_b"]
    roster_a = state["roster_a"]
    roster_b = state["roster_b"]
    cr       = battle["clash_results"]

    ROLE_ICONS = {
        "Jinchuuriki":   "🦊",
        "Domain User":   "🔮",
        "Bankai":        "⚔️",
        "Specialist":    "🎯",
        "Cursed Vessel": "🩸",
        "Sage":          "🌿",
        "Arrancar":      "👁️",
        "Grim Pact":     "💀",
    }

    lines = [
        "<b>「 ⚡ NEXUS AWAKENING — RESULT ぁ 」</b>",
        "━━━━━━━━━━━━━━━━━",
    ]

    for role in ROLES:
        res   = cr[role]
        icon  = ROLE_ICONS[role]
        cid_a = roster_a[role]
        cid_b = roster_b[role]
        cd_a  = db["global_cards"].get(cid_a, {})
        cd_b  = db["global_cards"].get(cid_b, {})
        n_a   = cd_a.get("name", "?")
        n_b   = cd_b.get("name", "?")
        w     = res["winner"]

        w_sym = "🔵" if w == "a" else ("🔴" if w == "b" else "⚖️")

        arrancar_note = ""
        if role == "Arrancar":
            side_name = name_a if res["arrancar_side"] == "a" else name_b
            arrancar_note = f" <i>(switched → {side_name})</i>"

        lines.append(
            f"{icon} <b>{role}</b>{arrancar_note}\n"
            f"  🔵 {n_a}  ⚔️{res['stats_a']['atk']} 🛡️{res['stats_a']['def']} ⚡{res['stats_a']['spd']}\n"
            f"  🔴 {n_b}  ⚔️{res['stats_b']['atk']} 🛡️{res['stats_b']['def']} ⚡{res['stats_b']['spd']}\n"
            f"  {w_sym} DMG  ➜  🔵 {res['dmg_a']}  vs  🔴 {res['dmg_b']}"
        )

    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append(
        f"📊 Score  ➜  🔵 {name_a} <b>{battle['score_a']}</b>  —  "
        f"<b>{battle['score_b']}</b> 🔴 {name_b}"
    )
    lines.append("━━━━━━━━━━━━━━━━━")

    winner_uid = battle["winner"]
    if winner_uid is None:
        lines.append("⚖️ <b>DRAW!</b> Both warriors equally matched.")
        shards_a = shards_b = SHARD_LOSE
    elif winner_uid == uid_a:
        shards_a = SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN
        shards_b = SHARD_LOSE
        if battle["arr_bonus"]: shards_a += SHARD_ARRREV
        lines.append(f"🏆 <b>Winner  ➜  {name_a}</b>")
        if battle["upset_bonus"]: lines.append("✨ <i>Upset Bonus! All Basic hand defeated stronger cards!</i>")
        if battle["arr_bonus"]:   lines.append("👁️ <i>Arrancar Bonus! Switched sides and won!</i>")
    else:
        shards_b = SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN
        shards_a = SHARD_LOSE
        if battle["arr_bonus"]: shards_b += SHARD_ARRREV
        lines.append(f"🏆 <b>Winner  ➜  {name_b}</b>")
        if battle["upset_bonus"]: lines.append("✨ <i>Upset Bonus! All Basic hand defeated stronger cards!</i>")
        if battle["arr_bonus"]:   lines.append("👁️ <i>Arrancar Bonus! Switched sides and won!</i>")

    lines.append(
        f"\n💠 Shards  ➜  🔵 +{shards_a}   🔴 +{shards_b}"
    )

    return "\n".join(lines)


# ==========================================
# /versus COMMAND
# ==========================================
@main_router.message(Command("versus"))
async def versus_cmd(message: Message):
    uid = message.from_user.id
    if is_ghost_banned(uid) or is_shadow_banned(uid): return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply(
            "⚠️ <b>Usage:</b> Reply to a user's message to challenge them.\n"
            "<code>/versus</code>  (as a reply)",
            parse_mode=ParseMode.HTML
        )
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        await message.reply("❌ You cannot challenge a bot.", parse_mode=ParseMode.HTML)
        return
    if target.id == uid:
        await message.reply("❌ You cannot challenge yourself.", parse_mode=ParseMode.HTML)
        return

    # Cooldown check
    now = time.time()
    if uid not in ADMIN_IDS:
        last = _versus_cooldowns.get(uid, 0)
        if now - last < VERSUS_COOLDOWN:
            rem  = int(VERSUS_COOLDOWN - (now - last))
            m, s = divmod(rem, 60)
            await message.reply(
                f"⏳ <b>Versus cooldown active!</b>\nYou can challenge again in <b>{m}m {s}s</b>.",
                parse_mode=ParseMode.HTML
            )
            return

    # Daily cap
    if uid not in ADMIN_IDS:
        dkey = _today_key(uid)
        if _versus_daily.get(dkey, 0) >= VERSUS_DAILY_CAP:
            await message.reply(
                f"❌ <b>Daily limit reached!</b>\nYou've played <b>{VERSUS_DAILY_CAP}</b> duels today.",
                parse_mode=ParseMode.HTML
            )
            return

    # Check no active versus for either user
    for key, st in active_versus.items():
        if uid in key or target.id in key:
            await message.reply(
                "⚠️ One of you already has an active Versus. Finish or cancel it first.",
                parse_mode=ParseMode.HTML
            )
            return

    db = load_db()
    ensure_user(uid, message.from_user.full_name, message.from_user.username, db)
    ensure_user(target.id, target.full_name, target.username, db)

    # Check both have at least 8 cards
    hand_a = _get_hand(uid, db)
    hand_b = _get_hand(target.id, db)
    if len(hand_a) < 8:
        await message.reply("❌ You need at least 8 cards in your deck to versus.", parse_mode=ParseMode.HTML)
        return
    if len(hand_b) < 8:
        await message.reply(
            f"❌ {target.full_name} needs at least 8 cards to participate.",
            parse_mode=ParseMode.HTML
        )
        return

    name_a = await get_mention(message.from_user)
    name_b = await get_mention(target)

    key = _state_key(uid, target.id)
    active_versus[key] = {
        "challenger":    uid,
        "opponent":      target.id,
        "name_a":        message.from_user.full_name,
        "name_b":        target.full_name,
        "chat_id":       message.chat.id,
        "msg_id":        None,
        "stage":         "pending",
        "hand_a":        hand_a,
        "hand_b":        hand_b,
        "roster_a":      {},
        "roster_b":      {},
        "pending_card":  None,
        "draft_turn":    uid,
        "score_a":       0,
        "score_b":       0,
        "expires":       now + ACCEPT_TIMEOUT,
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Accept",  callback_data=f"vs_accept_{uid}_{target.id}"),
        InlineKeyboardButton(text="❌ Decline", callback_data=f"vs_decline_{uid}_{target.id}"),
    ]])

    msg = await message.reply(
        f"<b>「 ⚔️ NEXUS DUEL REQUEST ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📤 Challenger  ➜  {name_a}\n"
        f"📥 Opponent    ➜  {name_b}\n\n"
        f"⏳ Waiting for response… ({ACCEPT_TIMEOUT}s)\n"
        f"━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )
    active_versus[key]["msg_id"] = msg.message_id
    asyncio.create_task(_accept_timeout(key, msg.message_id, message.chat.id))


async def _accept_timeout(key: frozenset, msg_id: int, chat_id: int):
    await asyncio.sleep(ACCEPT_TIMEOUT)
    if key in active_versus and active_versus[key]["stage"] == "pending":
        del active_versus[key]
        try:
            await bot.edit_message_text(
                "⏛ <b>Versus request expired.</b>",
                chat_id=chat_id, message_id=msg_id,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass


# ==========================================
# ACCEPT / DECLINE
# ==========================================
@main_router.callback_query(F.data.startswith("vs_accept_"))
async def vs_accept_cb(cq: CallbackQuery):
    parts   = cq.data.split("_")
    uid_a   = int(parts[2])
    uid_b   = int(parts[3])
    key     = _state_key(uid_a, uid_b)

    if cq.from_user.id != uid_b:
        await cq.answer("⚠️ This challenge isn't for you.", show_alert=True)
        return

    if key not in active_versus:
        await cq.answer("⚠️ This challenge has expired.", show_alert=True)
        return

    state = active_versus[key]
    if state["stage"] != "pending":
        await cq.answer("⚠️ Already in progress.", show_alert=True)
        return

    state["stage"]   = "drafting"
    state["expires"] = time.time() + DRAFT_TIMEOUT

    db    = load_db()
    board = _build_board(state, db, uid_a)

    await cq.message.edit_text(board, parse_mode=ParseMode.HTML, reply_markup=None)
    await cq.answer("✅ Challenge accepted! Draft begins.")

    # Prompt challenger to pick their first card
    await _prompt_card_pick(state, db, cq.message.chat.id)
    asyncio.create_task(_draft_timeout_loop(key))


@main_router.callback_query(F.data.startswith("vs_decline_"))
async def vs_decline_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid_a = int(parts[2])
    uid_b = int(parts[3])
    key   = _state_key(uid_a, uid_b)

    if cq.from_user.id != uid_b:
        await cq.answer("⚠️ This challenge isn't for you.", show_alert=True)
        return

    if key in active_versus:
        del active_versus[key]

    await cq.message.edit_text(
        f"❌ <b>Challenge declined.</b>",
        parse_mode=ParseMode.HTML
    )
    await cq.answer()


# ==========================================
# DRAFT — CARD PICK
# ==========================================
async def _prompt_card_pick(state: dict, db: dict, chat_id: int):
    turn_uid  = state["draft_turn"]
    hand_key  = "hand_a" if turn_uid == state["challenger"] else "hand_b"
    roster_key = "roster_a" if turn_uid == state["challenger"] else "roster_b"
    hand      = state[hand_key]
    roster    = state[roster_key]

    # Filter already-assigned cards
    used = set(roster.values())
    available = [cid for cid in hand if cid not in used]

    if not available:
        # Fallback: any owned card not yet used
        all_owned = _get_hand(turn_uid, db)
        available = [c for c in all_owned if c not in used][:8]

    turn_name = state["name_a"] if turn_uid == state["challenger"] else state["name_b"]
    kb = _build_card_buttons(available, db, f"vs_card_{turn_uid}")

    await bot.send_message(
        chat_id,
        f"<b>🎴 {turn_name}</b> — pick a card for your roster:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


@main_router.callback_query(F.data.startswith("vs_card_"))
async def vs_card_pick_cb(cq: CallbackQuery):
    parts    = cq.data.split("_")
    turn_uid = int(parts[2])
    card_id  = "_".join(parts[3:])

    if cq.from_user.id != turn_uid:
        await cq.answer("⚠️ It's not your turn!", show_alert=True)
        return

    # Find state
    key = None
    for k, st in active_versus.items():
        if turn_uid in k and st["stage"] == "drafting":
            key = k
            break

    if not key or key not in active_versus:
        await cq.answer("⚠️ No active versus found.", show_alert=True)
        return

    state = active_versus[key]

    if card_id == "cancel":
        await _cancel_versus(key, cq.message.chat.id, "❌ Versus cancelled.")
        await cq.message.delete()
        await cq.answer()
        return

    db     = load_db()
    cdata  = db["global_cards"].get(card_id)
    if not cdata:
        await cq.answer("❌ Card not found.", show_alert=True)
        return

    # Verify still owned
    uid_str = str(turn_uid)
    if db["users"].get(uid_str, {}).get("cards", {}).get(card_id, {}).get("amount", 0) < 1:
        await cq.answer("❌ You no longer own this card!", show_alert=True)
        return

    state["pending_card"] = card_id
    state["expires"]      = time.time() + DRAFT_TIMEOUT

    # Send card image
    await bot.send_photo(
        cq.message.chat.id,
        photo=cdata["file_id"],
        caption=(
            f"<b>「 🎴 CARD SELECTED ぁ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>{cdata['name']}</b>\n"
            f"🌟 {format_rarity(cdata['rarity'])}\n"
            f"📺 {cdata.get('anime', '?')}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Now assign this card to a role!"
        ),
        parse_mode=ParseMode.HTML
    )

    # Delete card-pick message
    await cq.message.delete()

    # Prompt role selection
    roster_key = "roster_a" if turn_uid == state["challenger"] else "roster_b"
    roster     = state[roster_key]
    kb         = _build_role_buttons(roster, f"vs_role_{turn_uid}")

    await bot.send_message(
        cq.message.chat.id,
        f"<b>⚡ Assign</b> <i>{cdata['name']}</i> to a role:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )
    await cq.answer()


# ==========================================
# DRAFT — ROLE PICK
# ==========================================
@main_router.callback_query(F.data.startswith("vs_role_"))
async def vs_role_pick_cb(cq: CallbackQuery):
    parts    = cq.data.split("_")
    turn_uid = int(parts[2])
    role     = "_".join(parts[3:])

    if cq.from_user.id != turn_uid:
        await cq.answer("⚠️ It's not your turn!", show_alert=True)
        return

    key = None
    for k, st in active_versus.items():
        if turn_uid in k and st["stage"] == "drafting":
            key = k
            break

    if not key or key not in active_versus:
        await cq.answer("⚠️ No active versus found.", show_alert=True)
        return

    state = active_versus[key]

    if role == "cancel":
        await _cancel_versus(key, cq.message.chat.id, "❌ Versus cancelled.")
        await cq.message.delete()
        await cq.answer()
        return

    if role not in ROLES:
        await cq.answer("⚠️ Invalid role.", show_alert=True)
        return

    card_id    = state.get("pending_card")
    if not card_id:
        await cq.answer("⚠️ No card selected. Start over.", show_alert=True)
        return

    roster_key = "roster_a" if turn_uid == state["challenger"] else "roster_b"
    roster     = state[roster_key]

    if role in roster:
        await cq.answer("⚠️ Role already filled. Pick another.", show_alert=True)
        return

    roster[role]         = card_id
    state["pending_card"] = None
    state["expires"]     = time.time() + DRAFT_TIMEOUT

    db    = load_db()
    board = _build_board(state, db, cq.from_user.id)

    # Update the board message
    try:
        await bot.edit_message_text(
            board,
            chat_id=cq.message.chat.id,
            message_id=state["msg_id"],
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

    await cq.message.delete()
    await cq.answer(f"✅ {role} assigned!")

    # Check if draft is complete
    if len(state["roster_a"]) == 8 and len(state["roster_b"]) == 8:
        await _finalize_battle(key, cq.message.chat.id, db)
        return

    # Switch turn
    uid_a = state["challenger"]
    uid_b = state["opponent"]
    state["draft_turn"] = uid_b if turn_uid == uid_a else uid_a

    await _prompt_card_pick(state, db, cq.message.chat.id)


# ==========================================
# DRAFT TIMEOUT LOOP
# ==========================================
async def _draft_timeout_loop(key: frozenset):
    while key in active_versus:
        await asyncio.sleep(5)
        if key not in active_versus:
            break
        state = active_versus[key]
        if state["stage"] != "drafting":
            break
        if time.time() > state["expires"]:
            # Auto-fill pending card + role for the timed-out player
            db         = load_db()
            turn_uid   = state["draft_turn"]
            roster_key = "roster_a" if turn_uid == state["challenger"] else "roster_b"
            roster     = state[roster_key]
            hand_key   = "hand_a" if turn_uid == state["challenger"] else "hand_b"
            hand       = state[hand_key]

            # If pending card not yet picked, pick one
            if not state["pending_card"]:
                used      = set(roster.values())
                available = [c for c in hand if c not in used]
                if available:
                    state["pending_card"] = random.choice(available)

            # Assign to a random empty role
            if state["pending_card"]:
                empty_roles = [r for r in ROLES if r not in roster]
                if empty_roles:
                    role = random.choice(empty_roles)
                    roster[role] = state["pending_card"]
                    state["pending_card"] = None

            chat_id   = state["chat_id"]
            turn_name = state["name_a"] if turn_uid == state["challenger"] else state["name_b"]

            try:
                await bot.send_message(
                    chat_id,
                    f"⏛ <b>{turn_name}</b> took too long — card auto-assigned!",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

            # Check if done
            if len(state["roster_a"]) == 8 and len(state["roster_b"]) == 8:
                await _finalize_battle(key, chat_id, db)
                break

            # Switch turn
            uid_a = state["challenger"]
            uid_b = state["opponent"]
            state["draft_turn"] = uid_b if turn_uid == uid_a else uid_a
            state["expires"]    = time.time() + DRAFT_TIMEOUT

            board = _build_board(state, db, turn_uid)
            try:
                await bot.edit_message_text(
                    board, chat_id=chat_id,
                    message_id=state["msg_id"],
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

            await _prompt_card_pick(state, db, chat_id)


# ==========================================
# FINALIZE BATTLE
# ==========================================
async def _finalize_battle(key: frozenset, chat_id: int, db: dict):
    if key not in active_versus:
        return

    state        = active_versus[key]
    state["stage"] = "battle"

    uid_a = state["challenger"]
    uid_b = state["opponent"]

    # Update board to "calculating" state
    try:
        await bot.edit_message_text(
            "<b>「 ⚡ NEXUS AWAKENING ぁ 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "⚔️ <b>Draft complete! Calculating battle…</b>\n"
            "━━━━━━━━━━━━━━━━━",
            chat_id=chat_id,
            message_id=state["msg_id"],
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

    await asyncio.sleep(1.5)

    battle = resolve_battle(state, db)
    result_text = build_result_text(state, battle, db)

    # Award shards
    winner_uid = battle["winner"]
    shards_a = shards_b = 0
    if winner_uid is None:
        shards_a = shards_b = SHARD_LOSE
    elif winner_uid == uid_a:
        shards_a = SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN
        shards_b = SHARD_LOSE
        if battle["arr_bonus"]: shards_a += SHARD_ARRREV
    else:
        shards_b = SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN
        shards_a = SHARD_LOSE
        if battle["arr_bonus"]: shards_b += SHARD_ARRREV

    db["users"][str(uid_a)]["nexus_shards"] = db["users"][str(uid_a)].get("nexus_shards", 0) + shards_a
    db["users"][str(uid_b)]["nexus_shards"] = db["users"][str(uid_b)].get("nexus_shards", 0) + shards_b
    save_db(db)

    # Cooldown + daily tracking
    now = time.time()
    _versus_cooldowns[uid_a] = now
    _versus_cooldowns[uid_b] = now
    _versus_daily[_today_key(uid_a)] = _versus_daily.get(_today_key(uid_a), 0) + 1
    _versus_daily[_today_key(uid_b)] = _versus_daily.get(_today_key(uid_b), 0) + 1

    # Send result
    try:
        await bot.edit_message_text(
            result_text,
            chat_id=chat_id,
            message_id=state["msg_id"],
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await bot.send_message(chat_id, result_text, parse_mode=ParseMode.HTML)

    del active_versus[key]


# ==========================================
# CANCEL HELPER
# ==========================================
async def _cancel_versus(key: frozenset, chat_id: int, reason: str):
    if key in active_versus:
        msg_id = active_versus[key].get("msg_id")
        del active_versus[key]
        if msg_id:
            try:
                await bot.edit_message_text(
                    reason, chat_id=chat_id,
                    message_id=msg_id,
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
