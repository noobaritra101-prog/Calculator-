import time
import random
import asyncio
from aiogram import F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
)
from aiogram.filters import Command
from aiogram.enums import ParseMode

from config import (
    bot, main_router, ADMIN_IDS,
    format_rarity, load_db, save_db,
    ensure_user, get_mention,
    is_ghost_banned, is_shadow_banned
)
from char_stats import get_char_stats, STAT_FIELDS

# ==========================================
# CONSTANTS
# ==========================================
VERSUS_COOLDOWN  = 900   # 15 min between challenges
VERSUS_DAILY_CAP = 10    # max duels per day
ACCEPT_TIMEOUT   = 30    # seconds to accept challenge
DRAFT_TIMEOUT    = 300   # seconds per draft turn (5 min)

ROLES = [
    "Strength",
    "Mana",
    "Defence",
    "Agility",
    "Vitality",
    "Intelligence",
    "Luck",
]

# ──────────────────────────────────────────────
# VERSUS MODE — which character tier players draft from
# ──────────────────────────────────────────────
MODES        = ["Divine", "Elite", "Basic", "Mix"]
MODE_ICONS   = {"Divine": "❄️", "Elite": "⚓", "Basic": "🃏", "Mix": "🌀"}
DEFAULT_MODE = "Mix"

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


def get_user_mode(uid: int, db: dict) -> str:
    return db["users"].get(str(uid), {}).get("versus_mode", DEFAULT_MODE)


def set_user_mode(uid: int, db: dict, mode: str) -> None:
    db["users"].setdefault(str(uid), {})["versus_mode"] = mode


def _get_owned_cards(uid: int, db: dict) -> list:
    """All card_ids user owns with amount >= 1."""
    user_cards = db["users"].get(str(uid), {}).get("cards", {})
    return [cid for cid, cd in user_cards.items() if cd.get("amount", 0) >= 1]


def _eligible_cards(uid: int, db: dict) -> list:
    """
    Owned cards that are usable in Versus:
    - must have stats written in char_stats.py (otherwise hidden entirely)
    - must match the player's selected mode (tier), unless mode is Mix
    """
    mode  = get_user_mode(uid, db)
    owned = _get_owned_cards(uid, db)
    eligible = []
    for cid in owned:
        cdata = db["global_cards"].get(cid, {})
        cs    = get_char_stats(cdata.get("name", ""))
        if not cs:
            continue  # no stats written yet — never shown in Versus
        if mode != "Mix" and cs.get("tier") != mode:
            continue
        eligible.append(cid)
    return eligible


def _pull_random_card(uid: int, used: set, db: dict) -> str | None:
    """Pull a random eligible card from user's deck excluding already-used ones."""
    eligible  = _eligible_cards(uid, db)
    available = [c for c in eligible if c not in used]
    return random.choice(available) if available else None


# ==========================================
# BOARD BUILDER
# ==========================================
def _link(uid: int, name: str) -> str:
    """Telegram profile link for a user."""
    return f'<a href="tg://user?id={uid}">{name}</a>'


def _build_board(state: dict, db: dict,
                 stage_hint: str = "",
                 pulled_card_id: str = None) -> tuple[str, InlineKeyboardMarkup | None]:
    """
    Returns (board_text, keyboard_or_None).
    stage_hint: 'pull'  → show Pull Card button
                'role'  → show role assignment buttons (after a pull)
                ''      → no buttons (waiting / spectator)
    pulled_card_id: set when stage_hint == 'role'
    """
    uid_a    = state["challenger"]
    uid_b    = state["opponent"]
    name_a   = state["name_a"]
    name_b   = state["name_b"]
    roster_a = state["roster_a"]
    roster_b = state["roster_b"]
    turn_uid = state["draft_turn"]

    link_a = _link(uid_a, name_a)
    link_b = _link(uid_b, name_b)

    def role_line(role: str, roster: dict, hide_luck: bool) -> str:
        cid = roster.get(role)
        if not cid:
            return f"  {role:<16} ➜  . . ."
        if role == "Luck" and hide_luck:
            return f"  {role:<16} ➜  ░░░░░░░░░"
        cdata = db["global_cards"].get(cid, {})
        return f"  {role:<16} ➜  {cdata.get('name','?')}  《 {format_rarity(cdata.get('rarity',''))} 》"

    lines_a = "\n".join(role_line(r, roster_a, False) for r in ROLES)
    lines_b = "\n".join(role_line(r, roster_b, True)  for r in ROLES)

    turn_link = link_a if turn_uid == uid_a else link_b

    # Pulled card line
    pulled_line = ""
    if pulled_card_id:
        cdata = db["global_cards"].get(pulled_card_id, {})
        pulled_line = (
            f"\nPulled   ➜  {cdata.get('name','?')}"
            f"  《 {format_rarity(cdata.get('rarity',''))} 》"
        )

    text = (
        f"<b>「 ⚡ NEXUS AWAKENING — Draft ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⬤ {link_a}\n"
        f"{lines_a}\n\n"
        f"⬤ {link_b}\n"
        f"{lines_b}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎮 Turn   ➜  {turn_link}"
        f"{pulled_line}"
    )

    kb = None

    if stage_hint == "pull":
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🎲 Pull Card",
                callback_data=f"vs_pull_{turn_uid}"
            ),
        ]])

    elif stage_hint == "role" and pulled_card_id:
        roster     = roster_a if turn_uid == uid_a else roster_b
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

    def card_name(cid: str) -> str:
        return db["global_cards"].get(cid, {}).get("name", "")

    def stat_val(cid: str, field: str) -> int:
        cs = get_char_stats(card_name(cid))
        return cs.get(field, 0) if cs else 0

    def char_tier(cid: str) -> str:
        cs = get_char_stats(card_name(cid))
        return cs.get("tier", "") if cs else ""

    def luck_power(cid: str) -> float:
        cs = get_char_stats(card_name(cid))
        if not cs:
            return 0
        return sum(cs.get(f, 0) for f in STAT_FIELDS) / len(STAT_FIELDS)

    score_a = score_b = 0
    clash_results = {}

    # Each of the six named roles is a direct stat-vs-stat comparison —
    # whoever's card has the higher number for that field wins the role.
    non_luck = [r for r in ROLES if r != "Luck"]
    for role in non_luck:
        cid_a = roster_a[role]
        cid_b = roster_b[role]
        va = stat_val(cid_a, role)
        vb = stat_val(cid_b, role)
        if   va > vb: winner = "a"
        elif vb > va: winner = "b"
        else:         winner = "draw"
        clash_results[role] = {"winner": winner, "value_a": va, "value_b": vb}
        if   winner == "a": score_a += 1
        elif winner == "b": score_b += 1
        else:                score_a += 0.5; score_b += 0.5

    # Luck has no dedicated stat field — it's decided by each card's overall
    # average power, but always joins whichever side is currently ahead.
    arrancar_side = "a" if score_a >= score_b else "b"
    la = luck_power(roster_a["Luck"])
    lb = luck_power(roster_b["Luck"])
    if arrancar_side == "a":
        winner = "a" if la >= lb else "b"
    else:
        winner = "b" if lb >= la else "a"

    clash_results["Luck"] = {
        "winner": winner, "value_a": la, "value_b": lb,
        "arrancar_side": arrancar_side
    }
    if   winner == "a": score_a += 1
    elif winner == "b": score_b += 1
    else:                score_a += 0.5; score_b += 0.5

    overall_winner = uid_a if score_a > score_b else (uid_b if score_b > score_a else None)

    def all_basic(roster: dict) -> bool:
        return all(char_tier(cid) == "Basic" for cid in roster.values())

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

    link_a = _link(uid_a, name_a)
    link_b = _link(uid_b, name_b)

    ROLE_ICONS = {
        "Strength":     "💪",
        "Mana":         "🔮",
        "Defence":      "💢",
        "Agility":      "🚤",
        "Vitality":     "🐋",
        "Intelligence": "🎓",
        "Luck":         "☘️",
    }

    lines = [
        "<b>「 ⚡ NEXUS AWAKENING — RESULT ぁ 」</b>",
        "━━━━━━━━━━━━━━━━",
    ]

    for role in ROLES:
        res  = cr[role]
        icon = ROLE_ICONS[role]
        cd_a = db["global_cards"].get(roster_a[role], {})
        cd_b = db["global_cards"].get(roster_b[role], {})
        w    = res["winner"]

        note = ""
        if role == "Luck":
            side = name_a if res["arrancar_side"] == "a" else name_b
            note = f" <i>(→ {side})</i>"

        if w == "a":
            winner_link = link_a
            body = f" {cd_a.get('name','?')} defeated {cd_b.get('name','?')}. (+1 points)"
        elif w == "b":
            winner_link = link_b
            body = f" {cd_b.get('name','?')} defeated {cd_a.get('name','?')}. (+1 points)"
        else:
            winner_link = "Draw"
            body = f" {cd_a.get('name','?')} and {cd_b.get('name','?')} fought to a draw."

        lines.append(f"{icon} <b>{role}</b> - {winner_link}{note}")
        lines.append(body)

    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("📊 <b>Score</b>")

    winner_uid = battle["winner"]
    if winner_uid is None:
        shards_a = shards_b = 0
        lines.append(f" {link_a} — {battle['score_a']}")
        lines.append(f" {link_b} — {battle['score_b']}")
        lines.append("\n⚖️ <b>DRAW!</b> No reward to either side.")
    else:
        win_shards = (SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN) + (SHARD_ARRREV if battle["arr_bonus"] else 0)
        if winner_uid == uid_a:
            shards_a, shards_b = win_shards, 0
            lines.append(f" {link_a} — {battle['score_a']}  [ Winner ] + {shards_a} shards")
            lines.append(f" {link_b} — {battle['score_b']}")
        else:
            shards_a, shards_b = 0, win_shards
            lines.append(f" {link_a} — {battle['score_a']}")
            lines.append(f" {link_b} — {battle['score_b']}  [ Winner ] + {shards_b} shards")
        lines.append("\nNo reward to looser")

    if battle["upset_bonus"]: lines.append("✨ <i>Upset Bonus! All Basic hand defeated stronger cards!</i>")
    if battle["arr_bonus"]:   lines.append("☘️ <i>Luck Bonus! Switched sides and won!</i>")

    return "\n".join(lines)


# ==========================================
# VERSUS MODE SETTINGS
# ==========================================
def _mode_kb(current: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for m in MODES:
        mark = "✅ " if m == current else ""
        row.append(InlineKeyboardButton(
            text=f"{mark}{MODE_ICONS[m]} {m}",
            callback_data=f"vs_setmode_{m}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _mode_text(current: str) -> str:
    return (
        "<b>⚙️ Versus Mode</b>\n"
        "Choose which character tier you'll draft from in battles. "
        "This is saved and used every time you play.\n\n"
        f"Current  ➜  {MODE_ICONS[current]} <b>{current}</b>"
    )


@main_router.message(Command(commands=["versusmode", "vsmode"]))
async def versus_mode_cmd(message: Message):
    uid = message.from_user.id
    if is_ghost_banned(uid) or is_shadow_banned(uid): return

    db = load_db()
    ensure_user(uid, message.from_user.full_name, message.from_user.username)
    current = get_user_mode(uid, db)

    await message.reply(
        _mode_text(current),
        parse_mode=ParseMode.HTML,
        reply_markup=_mode_kb(current)
    )


@main_router.callback_query(F.data.startswith("vs_setmode_"))
async def vs_setmode_cb(cq: CallbackQuery):
    mode = cq.data.split("_", 2)[2]
    if mode not in MODES:
        await cq.answer("⚠️ Unknown mode.", show_alert=True)
        return

    uid = cq.from_user.id
    db  = load_db()
    ensure_user(uid, cq.from_user.full_name, cq.from_user.username)
    set_user_mode(uid, db, mode)
    save_db(db)

    await cq.answer(f"✅ Versus mode set to {MODE_ICONS[mode]} {mode}!")
    try:
        await bot.edit_message_text(
            _mode_text(mode),
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=_mode_kb(mode)
        )
    except Exception:
        pass


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

    owned_a = _eligible_cards(uid, db)
    owned_b = _eligible_cards(target.id, db)
    if len(owned_a) < 8:
        mode_a = get_user_mode(uid, db)
        await message.reply(
            f"❌ You need at least 8 eligible cards (mode: {MODE_ICONS[mode_a]} {mode_a}, "
            f"stats must be written) to versus.\nChange mode with /versusmode.",
            parse_mode=ParseMode.HTML
        )
        return
    if len(owned_b) < 8:
        mode_b = get_user_mode(target.id, db)
        await message.reply(
            f"❌ {target.full_name} needs at least 8 eligible cards "
            f"(mode: {MODE_ICONS[mode_b]} {mode_b}) to participate.",
            parse_mode=ParseMode.HTML
        )
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
        f"{name_a} has challenged {name_b} to a Card Battle!\n\n"
        f"{name_b}, will you accept the challenge?",
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
    state["expires"] = time.time() + DRAFT_TIMEOUT

    # Build board text with pulled card shown, and role buttons
    text, kb = _build_board(state, db, stage_hint="role", pulled_card_id=card_id)

    # Edit board message to show the card photo + board text as caption with role buttons
    try:
        await bot.edit_message_media(
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            media=InputMediaPhoto(
                media=cdata["file_id"],
                caption=text,
                parse_mode=ParseMode.HTML,
                has_spoiler=True,
                show_caption_above_media=True
            ),
            reply_markup=kb
        )
    except Exception:
        # Board was a text message (first pull) — delete and resend as photo
        try:
            await cq.message.delete()
        except Exception:
            pass
        sent = await bot.send_photo(
            cq.message.chat.id,
            photo=cdata["file_id"],
            caption=text,
            parse_mode=ParseMode.HTML,
            has_spoiler=True,
            show_caption_above_media=True,
            reply_markup=kb
        )
        state["msg_id"] = sent.message_id

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
    if len(state["roster_a"]) == len(ROLES) and len(state["roster_b"]) == len(ROLES):
        await _finalize_battle(key, cq.message.chat.id, db, cq.message.message_id)
        return

    # Switch turn — board goes back to "Pull" stage
    state["draft_turn"] = uid_b if turn_uid == uid_a else uid_a
    text, kb = _build_board(state, db, stage_hint="pull")

    # Current message is a photo (the last pulled card) — just update its
    # caption + keyboard in place rather than deleting and resending
    try:
        await bot.edit_message_caption(
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            caption=text,
            parse_mode=ParseMode.HTML,
            show_caption_above_media=True,
            reply_markup=kb
        )
        state["msg_id"] = cq.message.message_id
    except Exception:
        # Fallback only if the message is somehow gone already
        sent = await bot.send_message(
            cq.message.chat.id, text,
            parse_mode=ParseMode.HTML, reply_markup=kb
        )
        state["msg_id"] = sent.message_id


# ==========================================
# DRAFT TIMEOUT LOOP
# ==========================================
async def _draft_timeout_loop(key: frozenset):
    while key in active_versus:
        await asyncio.sleep(10)
        if key not in active_versus:
            break
        state = active_versus[key]
        if state["stage"] != "drafting":
            break
        if time.time() <= state["expires"]:
            continue

        # Timeout — no auto-fill, just end the versus
        chat_id = state["chat_id"]
        msg_id  = state["msg_id"]
        turn_uid   = state["draft_turn"]
        turn_name  = state["name_a"] if turn_uid == state["challenger"] else state["name_b"]
        del active_versus[key]

        try:
            await bot.edit_message_text(
                f"<b>「 ⚡ NEXUS AWAKENING ぁ 」</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏛ <b>Versus ended</b> — {turn_name} didn't pick in time.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━",
                chat_id=chat_id, message_id=msg_id,
                parse_mode=ParseMode.HTML, reply_markup=None
            )
        except Exception:
            pass
        break


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

    try:
        battle      = resolve_battle(state, db)
        result_text = build_result_text(state, battle, db)
    except Exception as e:
        try:
            await bot.edit_message_text(
                f"⚠️ <b>Something went wrong calculating the result.</b>\n<code>{e}</code>",
                chat_id=chat_id, message_id=msg_id, parse_mode=ParseMode.HTML
            )
        except Exception:
            await bot.send_message(
                chat_id, f"⚠️ <b>Something went wrong calculating the result.</b>\n<code>{e}</code>",
                parse_mode=ParseMode.HTML
            )
        del active_versus[key]
        return

    winner_uid = battle["winner"]
    if winner_uid is None:
        shards_a = shards_b = 0
    elif winner_uid == uid_a:
        shards_a = (SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN) + (SHARD_ARRREV if battle["arr_bonus"] else 0)
        shards_b = 0
    else:
        shards_b = (SHARD_UPSET if battle["upset_bonus"] else SHARD_WIN) + (SHARD_ARRREV if battle["arr_bonus"] else 0)
        shards_a = 0

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
