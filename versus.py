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
VERSUS_COOLDOWN  = 900   # 15 min between challenges
VERSUS_DAILY_CAP = 10    # max duels per day
ACCEPT_TIMEOUT   = 30    # seconds to accept challenge
DRAFT_TIMEOUT    = 30    # seconds per draft turn

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
SHARD_UPSET  = 350
SHARD_ARRREV = 50

# In-memory state
active_versus: dict        = {}
_versus_cooldowns: dict    = {}
_versus_daily: dict        = {}


# ==========================================
# HELPERS
# ==========================================
def _today_key(uid: int) -> str:
    from datetime import datetime, timezone
    return f"{uid}_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def _state_key(uid_a: int, uid_b: int) -> frozenset:
    return frozenset({uid_a, uid_b})


def get_variance(card_id: str) -> tuple:
    h = int(hashlib.md5(card_id.encode()).hexdigest(), 16)
    return (h % 21) - 10, ((h >> 8) % 21) - 10, ((h >> 16) % 21) - 10


def get_card_stats(card_id: str, rarity: str, role: str,
                   atk_bonus: int = 0, def_bonus: int = 0, spd_bonus: int = 0) -> dict:
    base = RARITY_BASE.get(rarity, RARITY_BASE["Basic 🃏"])
    var  = get_variance(card_id)
    rb   = ROLE_BONUSES[role]
    return {
        "atk": base["atk"] + var[0] + rb["atk"] + atk_bonus,
        "def": base["def"] + var[1] + rb["def"] + def_bonus,
        "spd": base["spd"] + var[2] + rb["spd"] + spd_bonus,
    }


def _get_owned_cards(uid: int, db: dict) -> list:
    """All card_ids user owns with amount >= 1."""
    user_cards = db["users"].get(str(uid), {}).get("cards", {})
    return [cid for cid, cd in user_cards.items() if cd.get("amount", 0) >= 1]


def _pull_random_card(uid: int, used: set, db: dict) -> str | None:
    """Pull a random card from user's deck excluding already-used ones."""
    owned = _get_owned_cards(uid, db)
    available = [c for c in owned if c not in used]
    return random.choice(available) if available else None


# ==========================================
# BOARD BUILDER
# ==========================================
def _build_board(state: dict, db: dict,
                 stage_hint: str = "",
                 role_buttons: bool = False,
                 pulled_card_id: str = None) -> tuple[str, InlineKeyboardMarkup | None]:
    """
    Returns (board_text, keyboard_or_None).
    stage_hint: 'pull' shows the Pull button, 'role' shows role buttons,
                '' shows no buttons (spectator / waiting)
    """
    uid_a    = state["challenger"]
    uid_b    = state["opponent"]
    name_a   = state["name_a"]
    name_b   = state["name_b"]
    roster_a = state["roster_a"]
    roster_b = state["roster_b"]
    turn_uid = state["draft_turn"]

    def role_line(role: str, roster: dict, hide_arrancar: bool) -> str:
        cid = roster.get(role)
        if not cid:
            return f"  {role:<16} ➜  . . ."
        if role == "Arrancar" and hide_arrancar:
            return f"  {role:<16} ➜  ░░░░░░░░░"
        cdata = db["global_cards"].get(cid, {})
        return f"  {role:<16} ➜  {cdata.get('name','?')}  《 {format_rarity(cdata.get('rarity',''))} 》"

    lines_a = "\n".join(role_line(r, roster_a, False) for r in ROLES)
    lines_b = "\n".join(role_line(r, roster_b, True)  for r in ROLES)

    slots_done = len(roster_a) + len(roster_b)
    turn_name  = name_a if turn_uid == uid_a else name_b

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

    kb = None

    if stage_hint == "pull":
        # Show Pull + Cancel on the board itself
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🎲 Pull Card",
                callback_data=f"vs_pull_{turn_uid}"
            ),
            InlineKeyboardButton(
                text="❌ Cancel",
                callback_data=f"vs_cancel_{uid_a}_{uid_b}"
            ),
        ]])

    elif stage_hint == "role" and pulled_card_id:
        # Show role buttons on the board itself
        roster = roster_a if turn_uid == uid_a else roster_b
        empty_roles = [r for r in ROLES if r not in roster]
        rows = []
        row  = []
        for role in empty_roles:
            row.append(InlineKeyboardButton(
                text=role,
                callback_data=f"vs_role_{turn_uid}_{pulled_card_id}_{role}"
            ))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(
            text="❌ Cancel",
            callback_data=f"vs_cancel_{uid_a}_{uid_b}"
        )])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)

    return text, kb


# ==========================================
# BATTLE CALCULATION
# ==========================================
def resolve_battle(state: dict, db: dict) -> dict:
    uid_a    = state["challenger"]
    uid_b    = state["opponent"]
    roster_a = state["roster_a"]
    roster_b = state["roster_b"]

    def team_buffs(roster: dict) -> tuple:
        atk_b = def_b = spd_b = 0
        if "Domain User" in roster: atk_b += 10; def_b += 10; spd_b += 10
        if "Sage"        in roster: atk_b +=  8; def_b +=  8; spd_b +=  8
        return atk_b, def_b, spd_b

    buf_a = team_buffs(roster_a)
    buf_b = team_buffs(roster_b)

    def get_stats(roster: dict, role: str, buf: tuple, grim_losing: bool = False) -> dict:
        cid   = roster[role]
        cdata = db["global_cards"].get(cid, {})
        rar   = format_rarity(cdata.get("rarity", "Basic 🃏"))
        stats = get_card_stats(cid, rar, role, buf[0], buf[1], buf[2])
        if role == "Bankai":
            stats["atk"] = int(stats["atk"] * 2.5)
            stats["def"] = int(stats["def"] * 2.5)
            stats["spd"] = int(stats["spd"] * 2.5)
        if role == "Grim Pact" and grim_losing:
            stats["atk"] = int(stats["atk"] * 2)
        return stats

    def clash_roles(role: str, buf_x: tuple, buf_y: tuple,
                    grim_x: bool = False, grim_y: bool = False) -> dict:
        sx = get_stats(roster_a, role, buf_x, grim_x)
        sy = get_stats(roster_b, role, buf_y, grim_y)
        dmg_x = max(5, sx["atk"] - sy["def"])
        dmg_y = max(5, sy["atk"] - sx["def"])
        if role == "Cursed Vessel":
            dmg_x = max(1, dmg_x - int(dmg_x * 0.5))
        net = dmg_x - dmg_y
        if   net > 0:             winner = "a"
        elif net < 0:             winner = "b"
        elif sx["spd"] > sy["spd"]: winner = "a"
        elif sy["spd"] > sx["spd"]: winner = "b"
        else:                       winner = "draw"
        return {"winner": winner, "dmg_a": dmg_x, "dmg_b": dmg_y,
                "stats_a": sx, "stats_b": sy}

    score_a = score_b = 0
    clash_results = {}
    grim_a = grim_b = False

    non_arrancar = [r for r in ROLES if r != "Arrancar"]
    for i, role in enumerate(non_arrancar):
        res = clash_roles(role, buf_a, buf_b, grim_a, grim_b)
        clash_results[role] = res
        if   res["winner"] == "a":    score_a += 1
        elif res["winner"] == "b":    score_b += 1
        else:                         score_a += 0.5; score_b += 0.5
        if i == 3:
            grim_a = score_a < score_b
            grim_b = score_b < score_a

    # Arrancar joins winning side
    arrancar_side = "a" if score_a >= score_b else "b"
    sa = get_stats(roster_a, "Arrancar", buf_a)
    sb = get_stats(roster_b, "Arrancar", buf_b)
    dmg_a = max(5, sa["atk"] - sb["def"])
    dmg_b = max(5, sb["atk"] - sa["def"])
    if arrancar_side == "a":
        winner = "a" if dmg_a >= dmg_b else "b"
    else:
        winner = "b" if dmg_b >= dmg_a else "a"

    clash_results["Arrancar"] = {
        "winner": winner, "dmg_a": dmg_a, "dmg_b": dmg_b,
        "stats_a": sa, "stats_b": sb, "arrancar_side": arrancar_side
    }
    if   winner == "a":  score_a += 1
    elif winner == "b":  score_b += 1
    else:                score_a += 0.5; score_b += 0.5

    overall_winner = uid_a if score_a > score_b else (uid_b if score_b > score_a else None)

    def all_basic(roster: dict) -> bool:
        return all(
            format_rarity(db["global_cards"].get(cid, {}).get("rarity", "")) == "Basic 🃏"
            for cid in roster.values()
        )

    upset_bonus = (overall_winner == uid_a and all_basic(roster_a)) or \
                  (overall_winner == uid_b and all_basic(roster_b))
    arr_bonus   = (arrancar_side == "a" and overall_winner == uid_a) or \
                  (arrancar_side == "b" and overall_winner == uid_b)

    return {
        "clash_results": clash_results,
        "score_a": score_a, "score_b": score_b,
        "winner": overall_winner,
        "upset_bonus": upset_bonus,
        "arr_bonus": arr_bonus,
        "arrancar_side": arrancar_side,
    }


# ==========================================
# RESULT MESSAGE
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
        "Jinchuuriki":   "🦊", "Domain User":   "🔮",
        "Bankai":        "⚔️", "Specialist":    "🎯",
        "Cursed Vessel": "🩸", "Sage":          "🌿",
        "Arrancar":      "👁️", "Grim Pact":     "💀",
    }

    lines = [
        "<b>「 ⚡ NEXUS AWAKENING — RESULT ぁ 」</b>",
        "━━━━━━━━━━━━━━━━━",
    ]

    for role in ROLES:
        res  = cr[role]
        icon = ROLE_ICONS[role]
        cd_a = db["global_cards"].get(roster_a[role], {})
        cd_b = db["global_cards"].get(roster_b[role], {})
        w    = res["winner"]
        sym  = "🔵" if w == "a" else ("🔴" if w == "b" else "⚖️")

        note = ""
        if role == "Arrancar":
            side = name_a if res["arrancar_side"] == "a" else name_b
            note = f" <i>(→ {side})</i>"

        lines.append(
            f"{icon} <b>{role}</b>{note}\n"
            f"  🔵 {cd_a.get('name','?')}  ⚔️{res['stats_a']['atk']} 🛡️{res['stats_a']['def']} ⚡{res['stats_a']['spd']}\n"
            f"  🔴 {cd_b.get('name','?')}  ⚔️{res['stats_b']['atk']} 🛡️{res['stats_b']['def']} ⚡{res['stats_b']['spd']}\n"
            f"  {sym} DMG  ➜  🔵 {res['dmg_a']}  vs  🔴 {res['dmg_b']}"
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
        shards_a = (SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN) + (SHARD_ARRREV if battle["arr_bonus"] else 0)
        shards_b = SHARD_LOSE
        lines.append(f"🏆 <b>Winner  ➜  {name_a}</b>")
    else:
        shards_b = (SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN) + (SHARD_ARRREV if battle["arr_bonus"] else 0)
        shards_a = SHARD_LOSE
        lines.append(f"🏆 <b>Winner  ➜  {name_b}</b>")

    if battle["upset_bonus"]: lines.append("✨ <i>Upset Bonus! All Basic hand defeated stronger cards!</i>")
    if battle["arr_bonus"]:   lines.append("👁️ <i>Arrancar Bonus! Switched sides and won!</i>")
    lines.append(f"\n💠 Shards  ➜  🔵 +{shards_a}   🔴 +{shards_b}")

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

    now = time.time()
    if uid not in ADMIN_IDS:
        last = _versus_cooldowns.get(uid, 0)
        if now - last < VERSUS_COOLDOWN:
            rem  = int(VERSUS_COOLDOWN - (now - last))
            m, s = divmod(rem, 60)
            await message.reply(
                f"⏳ <b>Versus cooldown active!</b>\nTry again in <b>{m}m {s}s</b>.",
                parse_mode=ParseMode.HTML
            )
            return
        dkey = _today_key(uid)
        if _versus_daily.get(dkey, 0) >= VERSUS_DAILY_CAP:
            await message.reply(
                f"❌ <b>Daily limit reached!</b> You've played <b>{VERSUS_DAILY_CAP}</b> duels today.",
                parse_mode=ParseMode.HTML
            )
            return

    for k, st in active_versus.items():
        if uid in k or target.id in k:
            await message.reply(
                "⚠️ One of you already has an active Versus. Finish it first.",
                parse_mode=ParseMode.HTML
            )
            return

    db = load_db()
    ensure_user(uid, message.from_user.full_name, message.from_user.username)
    ensure_user(target.id, target.full_name, target.username)

    owned_a = _get_owned_cards(uid, db)
    owned_b = _get_owned_cards(target.id, db)
    if len(owned_a) < 8:
        await message.reply("❌ You need at least 8 cards in your deck to versus.", parse_mode=ParseMode.HTML)
        return
    if len(owned_b) < 8:
        await message.reply(f"❌ {target.full_name} needs at least 8 cards to participate.", parse_mode=ParseMode.HTML)
        return

    name_a = get_mention(uid, message.from_user.full_name)
    name_b = get_mention(target.id, target.full_name)
    key    = _state_key(uid, target.id)

    active_versus[key] = {
        "challenger":  uid,
        "opponent":    target.id,
        "name_a":      message.from_user.full_name,
        "name_b":      target.full_name,
        "chat_id":     message.chat.id,
        "msg_id":      None,
        "stage":       "pending",
        "roster_a":    {},
        "roster_b":    {},
        "draft_turn":  uid,
        "score_a":     0,
        "score_b":     0,
        "expires":     now + ACCEPT_TIMEOUT,
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
    parts = cq.data.split("_")
    uid_a = int(parts[2])
    uid_b = int(parts[3])
    key   = _state_key(uid_a, uid_b)

    if cq.from_user.id != uid_b:
        await cq.answer("⚠️ This challenge isn't for you.", show_alert=True)
        return
    if key not in active_versus:
        await cq.answer("⚠️ Challenge has expired.", show_alert=True)
        return

    state = active_versus[key]
    if state["stage"] != "pending":
        await cq.answer("⚠️ Already in progress.", show_alert=True)
        return

    state["stage"]   = "drafting"
    state["expires"] = time.time() + DRAFT_TIMEOUT

    db   = load_db()
    text, kb = _build_board(state, db, stage_hint="pull")
    await cq.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await cq.answer("✅ Challenge accepted! Draft begins.")
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

    await cq.message.edit_text("❌ <b>Challenge declined.</b>", parse_mode=ParseMode.HTML)
    await cq.answer()


# ==========================================
# PULL CARD — random pick, show photo, update board with role buttons
# ==========================================
@main_router.callback_query(F.data.startswith("vs_pull_"))
async def vs_pull_cb(cq: CallbackQuery):
    turn_uid = int(cq.data.split("_")[2])

    if cq.from_user.id != turn_uid:
        await cq.answer("⚠️ It's not your turn!", show_alert=True)
        return

    key = None
    for k, st in active_versus.items():
        if turn_uid in k and st["stage"] == "drafting":
            key = k
            break

    if not key:
        await cq.answer("⚠️ No active versus found.", show_alert=True)
        return

    state      = active_versus[key]
    uid_a      = state["challenger"]
    uid_b      = state["opponent"]
    roster_key = "roster_a" if turn_uid == uid_a else "roster_b"
    roster     = state[roster_key]
    used       = set(roster.values())

    db      = load_db()
    card_id = _pull_random_card(turn_uid, used, db)

    if not card_id:
        await cq.answer("❌ No available cards left in your deck!", show_alert=True)
        return

    cdata = db["global_cards"].get(card_id, {})

    # Send card photo to chat (separate message — shows the card visually)
    await bot.send_photo(
        cq.message.chat.id,
        photo=cdata["file_id"],
        caption=(
            f"<b>「 🎲 PULLED ぁ 」</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>{cdata.get('name','?')}</b>\n"
            f"🌟 {format_rarity(cdata.get('rarity',''))}\n"
            f"📺 {cdata.get('anime','?')}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Assign this card to a role!"
        ),
        parse_mode=ParseMode.HTML
    )

    # Edit the board to show role buttons with the pulled card embedded in callback
    state["expires"] = time.time() + DRAFT_TIMEOUT
    text, kb = _build_board(state, db, stage_hint="role", pulled_card_id=card_id)
    await cq.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await cq.answer(f"🎲 Pulled: {cdata.get('name','?')}!")


# ==========================================
# ROLE PICK — assign pulled card to role, update board
# ==========================================
@main_router.callback_query(F.data.startswith("vs_role_"))
async def vs_role_pick_cb(cq: CallbackQuery):
    # format: vs_role_{turn_uid}_{card_id}_{role}
    # card_id itself might have underscores, role names don't
    parts    = cq.data.split("_")
    turn_uid = int(parts[2])

    # Role is always the last segment, card_id is everything between index 3 and last
    role    = parts[-1]
    card_id = "_".join(parts[3:-1])

    if cq.from_user.id != turn_uid:
        await cq.answer("⚠️ It's not your turn!", show_alert=True)
        return

    if role not in ROLES:
        await cq.answer("⚠️ Invalid role.", show_alert=True)
        return

    key = None
    for k, st in active_versus.items():
        if turn_uid in k and st["stage"] == "drafting":
            key = k
            break

    if not key:
        await cq.answer("⚠️ No active versus found.", show_alert=True)
        return

    state      = active_versus[key]
    uid_a      = state["challenger"]
    uid_b      = state["opponent"]
    roster_key = "roster_a" if turn_uid == uid_a else "roster_b"
    roster     = state[roster_key]

    if role in roster:
        await cq.answer("⚠️ Role already taken. Pick another.", show_alert=True)
        return

    # Assign
    roster[role]     = card_id
    state["expires"] = time.time() + DRAFT_TIMEOUT

    db = load_db()
    await cq.answer(f"✅ {role} assigned!")

    # Check draft complete
    if len(state["roster_a"]) == 8 and len(state["roster_b"]) == 8:
        await _finalize_battle(key, cq.message.chat.id, db, cq.message.message_id)
        return

    # Switch turn
    state["draft_turn"] = uid_b if turn_uid == uid_a else uid_a
    text, kb = _build_board(state, db, stage_hint="pull")
    await cq.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ==========================================
# CANCEL BUTTON
# ==========================================
@main_router.callback_query(F.data.startswith("vs_cancel_"))
async def vs_cancel_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid_a = int(parts[2])
    uid_b = int(parts[3])
    key   = _state_key(uid_a, uid_b)

    if cq.from_user.id not in (uid_a, uid_b):
        await cq.answer("⚠️ Not your versus.", show_alert=True)
        return

    if key in active_versus:
        del active_versus[key]

    await cq.message.edit_text("❌ <b>Versus cancelled.</b>", parse_mode=ParseMode.HTML)
    await cq.answer()


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
        if time.time() <= state["expires"]:
            continue

        # Auto-fill: pull random card + random role
        db         = load_db()
        turn_uid   = state["draft_turn"]
        uid_a      = state["challenger"]
        uid_b      = state["opponent"]
        roster_key = "roster_a" if turn_uid == uid_a else "roster_b"
        roster     = state[roster_key]
        chat_id    = state["chat_id"]
        turn_name  = state["name_a"] if turn_uid == uid_a else state["name_b"]

        used      = set(roster.values())
        card_id   = _pull_random_card(turn_uid, used, db)
        if card_id:
            empty_roles = [r for r in ROLES if r not in roster]
            if empty_roles:
                roster[random.choice(empty_roles)] = card_id

        try:
            await bot.send_message(
                chat_id,
                f"⏛ <b>{turn_name}</b> took too long — card auto-assigned!",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

        if len(state["roster_a"]) == 8 and len(state["roster_b"]) == 8:
            await _finalize_battle(key, chat_id, db, state["msg_id"])
            break

        state["draft_turn"] = uid_b if turn_uid == uid_a else uid_a
        state["expires"]    = time.time() + DRAFT_TIMEOUT

        try:
            text, kb = _build_board(state, db, stage_hint="pull")
            await bot.edit_message_text(
                text, chat_id=chat_id,
                message_id=state["msg_id"],
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
        except Exception:
            pass


# ==========================================
# FINALIZE BATTLE
# ==========================================
async def _finalize_battle(key: frozenset, chat_id: int, db: dict, msg_id: int):
    if key not in active_versus:
        return

    state        = active_versus[key]
    state["stage"] = "battle"
    uid_a          = state["challenger"]
    uid_b          = state["opponent"]

    try:
        await bot.edit_message_text(
            "<b>「 ⚡ NEXUS AWAKENING ぁ 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "⚔️ <b>Draft complete! Calculating clash…</b>\n"
            "━━━━━━━━━━━━━━━━━",
            chat_id=chat_id, message_id=msg_id,
            parse_mode=ParseMode.HTML, reply_markup=None
        )
    except Exception:
        pass

    await asyncio.sleep(1.5)

    battle      = resolve_battle(state, db)
    result_text = build_result_text(state, battle, db)

    winner_uid = battle["winner"]
    if winner_uid is None:
        shards_a = shards_b = SHARD_LOSE
    elif winner_uid == uid_a:
        shards_a = (SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN) + (SHARD_ARRREV if battle["arr_bonus"] else 0)
        shards_b = SHARD_LOSE
    else:
        shards_b = (SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN) + (SHARD_ARRREV if battle["arr_bonus"] else 0)
        shards_a = SHARD_LOSE

    db["users"][str(uid_a)]["nexus_shards"] = db["users"][str(uid_a)].get("nexus_shards", 0) + shards_a
    db["users"][str(uid_b)]["nexus_shards"] = db["users"][str(uid_b)].get("nexus_shards", 0) + shards_b
    save_db(db)

    now = time.time()
    _versus_cooldowns[uid_a] = now
    _versus_cooldowns[uid_b] = now
    _versus_daily[_today_key(uid_a)] = _versus_daily.get(_today_key(uid_a), 0) + 1
    _versus_daily[_today_key(uid_b)] = _versus_daily.get(_today_key(uid_b), 0) + 1

    try:
        await bot.edit_message_text(
            result_text, chat_id=chat_id, message_id=msg_id,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await bot.send_message(chat_id, result_text, parse_mode=ParseMode.HTML)

    del active_versus[key]
