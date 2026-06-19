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

MODES        = ["Divine", "Elite", "Basic", "Mix"]
MODE_ICONS   = {"Divine": "❄️", "Elite": "⚓", "Basic": "🃏", "Mix": "🌀"}

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


def _get_owned_cards(uid: int, db: dict) -> list:
    """All card_ids user owns with amount >= 1."""
    user_cards = db["users"].get(str(uid), {}).get("cards", {})
    return [cid for cid, cd in user_cards.items() if cd.get("amount", 0) >= 1]


def _eligible_cards(uid: int, mode: str, db: dict) -> list:
    """
    Owned cards that are usable in Versus:
    - must have stats written in char_stats.py (otherwise hidden entirely)
    - must match the selected match mode (tier), unless mode is Mix
    """
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


def _pull_random_card(uid: int, mode: str, used: set, db: dict) -> str | None:
    """Pull a random eligible card from user's deck excluding already-used ones."""
    eligible  = _eligible_cards(uid, mode, db)
    available = [c for c in eligible if c not in used]
    return random.choice(available) if available else None


async def _safe_edit_photo_board(chat_id: int, msg_id: int, text: str, kb: InlineKeyboardMarkup | None = None, file_id: str = None) -> int:
    """
    Safely edits a board message.
    If file_id is provided, it changes the photo media and updates the caption.
    If file_id is NOT provided, it only edits the caption.
    """
    if file_id:
        try:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=msg_id,
                media=InputMediaPhoto(
                    media=file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    has_spoiler=True,
                    show_caption_above_media=True
                ),
                reply_markup=kb
            )
            return msg_id
        except Exception:
            pass

    # Edit caption (for photo messages)
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=msg_id,
            caption=text,
            parse_mode=ParseMode.HTML,
            show_caption_above_media=True,
            reply_markup=kb
        )
        return msg_id
    except Exception:
        # Fallback to editing as plain text
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=msg_id,
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
            return msg_id
        except Exception:
            # Ultimate fallback: send a new message entirely
            try:
                if file_id:
                    sent = await bot.send_photo(
                        chat_id=chat_id,
                        photo=file_id,
                        caption=text,
                        parse_mode=ParseMode.HTML,
                        has_spoiler=True,
                        show_caption_above_media=True,
                        reply_markup=kb
                    )
                else:
                    sent = await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb
                    )
                return sent.message_id
            except Exception:
                return msg_id


# ==========================================
# BOARD BUILDER
# ==========================================
def _link(uid: int, name: str) -> str:
    """Telegram profile link for a user."""
    return f'<a href="tg://user?id={uid}">{name}</a>'


def _pending_kb(uid_a: int, uid_b: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Accept", callback_data=f"vs_accept_{uid_a}_{uid_b}"),
            InlineKeyboardButton(text="❌ Decline", callback_data=f"vs_decline_{uid_a}_{uid_b}"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Settings", callback_data=f"vs_settings_{uid_a}_{uid_b}"),
        ]
    ])


def _settings_kb(uid_a: int, uid_b: int, current_mode: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for m in MODES:
        mark = "✅ " if m == current_mode else ""
        row.append(InlineKeyboardButton(
            text=f"{mark}{MODE_ICONS[m]} {m}",
            callback_data=f"vs_setmatchmode_{m}_{uid_a}_{uid_b}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data=f"vs_back_{uid_a}_{uid_b}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_board(state: dict, db: dict,
                 stage_hint: str = "",
                 pulled_card_id: str = None) -> tuple[str, InlineKeyboardMarkup | None]:
    """
    Returns (board_text, keyboard_or_None).
    """
    uid_a    = state["challenger"]
    uid_b    = state["opponent"]
    name_a   = state["name_a"]
    name_b   = state["name_b"]
    roster_a = state["roster_a"]
    roster_b = state["roster_b"]
    turn_uid = state["draft_turn"]
    mode     = state["mode"]

    status_line_a = " 🟢 READY" if state.get("ready_a") else ""
    status_line_b = " 🟢 READY" if state.get("ready_b") else ""

    link_a = f"{_link(uid_a, name_a)}{status_line_a}"
    link_b = f"{_link(uid_b, name_b)}{status_line_b}"

    def role_line(role: str, roster: dict, hide_luck: bool) -> str:
        cid = roster.get(role)
        padded_role = f"{role:<16}"
        if not cid:
            return f"  {padded_role} ➜  ○○○"
        if role == "Luck" and hide_luck:
            return f"❯  {padded_role} ➜  ░░░░░░"
        cdata = db["global_cards"].get(cid, {})
        card_name = cdata.get('name', '?')
        rarity_formatted = format_rarity(cdata.get('rarity',''))
        return f"❯  {padded_role} ➜  {card_name}  《 {rarity_formatted} 》"

    lines_a = "\n".join(role_line(r, roster_a, False) for r in ROLES)
    lines_b = "\n".join(role_line(r, roster_b, True)  for r in ROLES)

    turn_link = _link(uid_a, name_a) if turn_uid == uid_a else _link(uid_b, name_b)

    # Pulled card line
    pulled_line = ""
    if pulled_card_id:
        cdata = db["global_cards"].get(pulled_card_id, {})
        pulled_line = (
            f"\n\n🎲 <b>Pulled:</b> {cdata.get('name','?')}"
            f"  《 {format_rarity(cdata.get('rarity',''))} 》"
        )

    text = (
        f"<b>「 ⚡ NEXUS AWAKENING — Draft ぁ 」</b>\n"
        f"<b>⚙️ Mode: {MODE_ICONS[mode]} {mode}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⬤ {link_a}\n"
        f"<b>{lines_a}</b>\n\n"
        f"⬤ {link_b}\n"
        f"<b>{lines_b}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if state["stage"] == "ready_check":
        text += "<b>⚔️ Both players must click Ready to begin!</b>"
    else:
        text += f"<b>❯ Turn   ➜  {turn_link}</b>"

    text += pulled_line

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

        # Add Skip Button if skips remain for this user
        skips_left = state.get("skip_a", 2) if turn_uid == uid_a else state.get("skip_b", 2)
        if skips_left > 0:
            rows.append([InlineKeyboardButton(
                text=f"⏭️ Skip Card ({skips_left} Left)",
                callback_data=f"vs_skip_{turn_uid}"
            )])

        kb = InlineKeyboardMarkup(inline_keyboard=rows)

    elif state["stage"] == "ready_check":
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🟢 Ready",
                callback_data=f"vs_ready_{uid_a}_{uid_b}"
            )
        ]])

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

    score_a = score_b = 0
    clash_results = {}

    # Each of the six named roles is a direct stat-vs-stat comparison
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

    # Luck is purely randomized
    luck_winner = random.choice(["a", "b"])
    clash_results["Luck"] = {
        "winner": luck_winner, "value_a": 0, "value_b": 0,
        "arrancar_side": luck_winner
    }
    if luck_winner == "a":
        score_a += 1
    else:
        score_b += 1

    overall_winner = uid_a if score_a > score_b else (uid_b if score_b > score_a else None)

    return {
        "clash_results": clash_results,
        "score_a": score_a, "score_b": score_b,
        "winner": overall_winner
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
            side = name_a if w == "a" else name_b
            note = f" <i>(→ {side})</i>"

        if w == "a":
            winner_link = link_a
            body = f" <i><b>{cd_a.get('name','?')} defeated {cd_b.get('name','?')}. (+1)</b></i>"
        elif w == "b":
            winner_link = link_b
            body = f" <i><b>{cd_b.get('name','?')} defeated {cd_a.get('name','?')}. (+1)</b></i>"
        else:
            winner_link = "Draw"
            body = f" <i><b>{cd_a.get('name','?')} and {cd_b.get('name','?')} fought to a draw.</b></i>"

        lines.append(f"{icon} <b>{role}</b> - {winner_link}{note}")
        lines.append(body)

    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("📊 <b>Score</b>")

    winner_uid = battle["winner"]
    if winner_uid is None:
        lines.append(f" {link_a} — {battle['score_a']}")
        lines.append(f" {link_b} — {battle['score_b']}")
        lines.append("\n⚖️ <b>DRAW!</b>")
    else:
        if winner_uid == uid_a:
            lines.append(f" {link_a} — {battle['score_a']}  [ Winner ]")
            lines.append(f" {link_b} — {battle['score_b']}")
        else:
            lines.append(f" {link_a} — {battle['score_a']}")
            lines.append(f" {link_b} — {battle['score_b']}  [ Winner ]")

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

    # Initial general deck capacity check (using Mix mode)
    owned_a = _eligible_cards(uid, "Mix", db)
    owned_b = _eligible_cards(target.id, "Mix", db)
    if len(owned_a) < 8:
        await message.reply(
            "❌ You need at least 8 eligible cards in your deck to participate in Versus.",
            parse_mode=ParseMode.HTML
        )
        return
    if len(owned_b) < 8:
        await message.reply(
            f"❌ {target.full_name} needs at least 8 eligible cards to participate.",
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
        "mode":        "Mix",
        "roster_a":    {},
        "roster_b":    {},
        "draft_turn":  uid,
        "score_a":     0,
        "score_b":     0,
        "ready_a":     False,
        "ready_b":     False,
        "skip_a":      2,
        "skip_b":      2,
        "expires":     now + ACCEPT_TIMEOUT,
        "photo_board_active": False,
        "processing":  False,
    }

    kb = _pending_kb(uid, target.id)

    msg = await message.reply(
        f"{name_a} has challenged {name_b} to a Card Battle!\n"
        f"⚙️ <b>Mode:</b> {MODE_ICONS['Mix']} Mix\n\n"
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
# ACCEPT / DECLINE / SETTINGS CALLBACKS
# ==========================================
@main_router.callback_query(F.data.startswith("vs_settings_"))
async def vs_settings_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid_a = int(parts[2])
    uid_b = int(parts[3])
    key   = _state_key(uid_a, uid_b)

    # Restrict settings visibility to the challenger
    if cq.from_user.id != uid_a:
        await cq.answer("⚠️ Only the challenger can change settings.", show_alert=True)
        return

    if key not in active_versus:
        await cq.answer("⚠️ Challenge has expired.", show_alert=True)
        return

    state = active_versus[key]
    if state["stage"] != "pending":
        await cq.answer("⚠️ Challenge already in progress.", show_alert=True)
        return

    current_mode = state["mode"]
    kb = _settings_kb(uid_a, uid_b, current_mode)

    await cq.message.edit_text(
        f"<b>⚙️ Versus Match Settings</b>\n"
        f"Select the character tier to draft from for this match:\n\n"
        f"Current: {MODE_ICONS[current_mode]} <b>{current_mode}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )
    await cq.answer()


@main_router.callback_query(F.data.startswith("vs_setmatchmode_"))
async def vs_setmatchmode_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    mode  = parts[2]
    uid_a = int(parts[3])
    uid_b = int(parts[4])
    key   = _state_key(uid_a, uid_b)

    # Restrict match-mode selection to the challenger
    if cq.from_user.id != uid_a:
        await cq.answer("⚠️ Only the challenger can change settings.", show_alert=True)
        return

    if key not in active_versus:
        await cq.answer("⚠️ Challenge has expired.", show_alert=True)
        return

    state = active_versus[key]
    if state["stage"] != "pending":
        await cq.answer("⚠️ Challenge already in progress.", show_alert=True)
        return

    state["mode"] = mode
    kb = _settings_kb(uid_a, uid_b, mode)

    await cq.message.edit_text(
        f"<b>⚙️ Versus Match Settings</b>\n"
        f"Select the character tier to draft from for this match:\n\n"
        f"Current: {MODE_ICONS[mode]} <b>{mode}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )
    await cq.answer(f"Match mode set to {mode}!")


@main_router.callback_query(F.data.startswith("vs_back_"))
async def vs_back_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid_a = int(parts[2])
    uid_b = int(parts[3])
    key   = _state_key(uid_a, uid_b)

    # Restrict "Back" navigation to the challenger
    if cq.from_user.id != uid_a:
        await cq.answer("⚠️ Only the challenger can change settings.", show_alert=True)
        return

    if key not in active_versus:
        await cq.answer("⚠️ Challenge has expired.", show_alert=True)
        return

    state = active_versus[key]
    if state["stage"] != "pending":
        await cq.answer("⚠️ Challenge already in progress.", show_alert=True)
        return

    name_a = get_mention(uid_a, state["name_a"])
    name_b = get_mention(uid_b, state["name_b"])
    mode   = state["mode"]

    kb = _pending_kb(uid_a, uid_b)
    await cq.message.edit_text(
        f"{name_a} has challenged {name_b} to a Card Battle!\n"
        f"⚙️ <b>Mode:</b> {MODE_ICONS[mode]} {mode}\n\n"
        f"{name_b}, will you accept the challenge?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )
    await cq.answer()


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

    if state.get("processing"):
        await cq.answer("⏳ Processing acceptance...", show_alert=False)
        return
    state["processing"] = True

    try:
        db = load_db()

        # Verify that both players have enough eligible cards for the SELECTED match mode
        owned_a_mode = _eligible_cards(uid_a, state["mode"], db)
        owned_b_mode = _eligible_cards(uid_b, state["mode"], db)
        if len(owned_a_mode) < 8:
            await cq.answer(f"❌ Challenger lacks 8 eligible cards for {state['mode']} mode!", show_alert=True)
            return
        if len(owned_b_mode) < 8:
            await cq.answer(f"❌ You lack 8 eligible cards for {state['mode']} mode!", show_alert=True)
            return

        state["stage"]   = "drafting"
        state["expires"] = time.time() + DRAFT_TIMEOUT

        text, kb = _build_board(state, db, stage_hint="pull")
        await cq.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        await cq.answer("✅ Challenge accepted! Draft begins.")
        asyncio.create_task(_draft_timeout_loop(key))
    finally:
        state["processing"] = False


@main_router.callback_query(F.data.startswith("vs_decline_"))
async def vs_decline_cb(cq: CallbackQuery):
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
    if state.get("processing"):
        await cq.answer("⏳ Processing decline...", show_alert=False)
        return
    state["processing"] = True

    try:
        del active_versus[key]
        await cq.message.edit_text("❌ <b>Challenge declined.</b>", parse_mode=ParseMode.HTML)
        await cq.answer()
    finally:
        state["processing"] = False


# ==========================================
# PULL CARD — random pick, send or update photo board
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

    state = active_versus[key]
    if state.get("processing"):
        await cq.answer("⏳ Processing card pull...", show_alert=False)
        return
    state["processing"] = True

    try:
        uid_a      = state["challenger"]
        roster_key = "roster_a" if turn_uid == uid_a else "roster_b"
        roster     = state[roster_key]
        used       = set(roster.values())
        mode       = state["mode"]

        db      = load_db()
        card_id = _pull_random_card(turn_uid, mode, used, db)

        if not card_id:
            await cq.answer("❌ No available cards left in your deck!", show_alert=True)
            return

        cdata = db["global_cards"].get(card_id, {})
        file_id = cdata.get("file_id")
        state["expires"] = time.time() + DRAFT_TIMEOUT

        # Build board text with pulled card shown, and role buttons
        text, kb = _build_board(state, db, stage_hint="role", pulled_card_id=card_id)

        # Check if this is the first pull transition (Text message -> Photo message)
        is_first_pull = not state.get("photo_board_active")

        if is_first_pull:
            try:
                sent = await bot.send_photo(
                    chat_id=cq.message.chat.id,
                    photo=file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    has_spoiler=True,
                    show_caption_above_media=True,
                    reply_markup=kb
                )
                state["msg_id"] = sent.message_id
                state["photo_board_active"] = True

                # Safely delete the initial invitation text board
                try:
                    await cq.message.delete()
                except Exception:
                    pass
            except Exception:
                # Fallback if photo send fails
                state["msg_id"] = await _safe_edit_photo_board(cq.message.chat.id, cq.message.message_id, text, kb)
        else:
            # In-place edit of the existing photo and its caption
            state["msg_id"] = await _safe_edit_photo_board(
                chat_id=cq.message.chat.id,
                msg_id=state["msg_id"],
                text=text,
                kb=kb,
                file_id=file_id
            )

        await cq.answer(f"🎲 Pulled: {cdata.get('name','?')}!")
    finally:
        state["processing"] = False


# ==========================================
# SKIP CARD — ignore pull, allow a fresh pull
# ==========================================
@main_router.callback_query(F.data.startswith("vs_skip_"))
async def vs_skip_cb(cq: CallbackQuery):
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

    state = active_versus[key]
    if state.get("processing"):
        await cq.answer("⏳ Processing card skip...", show_alert=False)
        return
    state["processing"] = True

    try:
        uid_a = state["challenger"]
        skip_key = "skip_a" if turn_uid == uid_a else "skip_b"

        skips_left = state.get(skip_key, 2)
        if skips_left <= 0:
            await cq.answer("❌ You have no skips remaining!", show_alert=True)
            return

        # Deduct a skip
        state[skip_key] = skips_left - 1
        state["expires"] = time.time() + DRAFT_TIMEOUT

        db = load_db()
        # Reset stage to allow pulling again
        text, kb = _build_board(state, db, stage_hint="pull")
        state["msg_id"] = await _safe_edit_photo_board(cq.message.chat.id, state["msg_id"], text, kb)

        await cq.answer(f"⏭️ Card skipped! {state[skip_key]} skips remaining.")
    finally:
        state["processing"] = False


# ==========================================
# ROLE PICK — assign pulled card to role, update board caption
# ==========================================
@main_router.callback_query(F.data.startswith("vs_role_"))
async def vs_role_pick_cb(cq: CallbackQuery):
    parts    = cq.data.split("_")
    turn_uid = int(parts[2])

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

    state = active_versus[key]
    if state.get("processing"):
        await cq.answer("⏳ Processing role assignment...", show_alert=False)
        return
    state["processing"] = True

    try:
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

        # Check if draft is fully complete
        if len(state["roster_a"]) == len(ROLES) and len(state["roster_b"]) == len(ROLES):
            state["stage"] = "ready_check"
            state["ready_a"] = False
            state["ready_b"] = False
            state["expires"] = time.time() + DRAFT_TIMEOUT # Refresh timeout for ready stage
            text, kb = _build_board(state, db)
            state["msg_id"] = await _safe_edit_photo_board(cq.message.chat.id, state["msg_id"], text, kb)
            return

        # Switch turn — board goes back to "Pull" stage
        state["draft_turn"] = uid_b if turn_uid == uid_a else uid_a
        text, kb = _build_board(state, db, stage_hint="pull")

        state["msg_id"] = await _safe_edit_photo_board(cq.message.chat.id, state["msg_id"], text, kb)
    finally:
        state["processing"] = False


# ==========================================
# READY CHECK CALLBACK
# ==========================================
@main_router.callback_query(F.data.startswith("vs_ready_"))
async def vs_ready_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid_a = int(parts[2])
    uid_b = int(parts[3])
    key   = _state_key(uid_a, uid_b)

    if key not in active_versus:
        await cq.answer("⚠️ Challenge has expired.", show_alert=True)
        return

    state = active_versus[key]
    if state["stage"] != "ready_check":
        await cq.answer("⚠️ Not in ready check stage.", show_alert=True)
        return

    if state.get("processing"):
        await cq.answer("⏳ Processing readiness...", show_alert=False)
        return
    state["processing"] = True

    try:
        clicker = cq.from_user.id
        if clicker == uid_a:
            if state.get("ready_a"):
                await cq.answer("You are already ready!", show_alert=True)
                return
            state["ready_a"] = True
            await cq.answer("✅ You are ready!")
        elif clicker == uid_b:
            if state.get("ready_b"):
                await cq.answer("You are already ready!", show_alert=True)
                return
            state["ready_b"] = True
            await cq.answer("✅ You are ready!")
        else:
            await cq.answer("⚠️ You are not part of this battle.", show_alert=True)
            return

        state["expires"] = time.time() + DRAFT_TIMEOUT # Extend alive time
        db = load_db()

        # If both players clicked Ready, resolve battle!
        if state.get("ready_a") and state.get("ready_b"):
            await _finalize_battle(key, cq.message.chat.id, db, cq.message.message_id)
            return

        # Otherwise, update ready status representation in-place
        text, kb = _build_board(state, db)
        state["msg_id"] = await _safe_edit_photo_board(cq.message.chat.id, state["msg_id"], text, kb)
    finally:
        state["processing"] = False


# ==========================================
# DRAFT TIMEOUT LOOP
# ==========================================
async def _draft_timeout_loop(key: frozenset):
    while key in active_versus:
        await asyncio.sleep(10)
        if key not in active_versus:
            break
        state = active_versus[key]
        if state["stage"] not in ("drafting", "ready_check"):
            break
        if time.time() <= state["expires"]:
            continue

        chat_id = state["chat_id"]
        msg_id  = state["msg_id"]
        del active_versus[key]

        try:
            await _safe_edit_photo_board(
                chat_id=chat_id,
                msg_id=msg_id,
                text=(
                    f"<b>「 ⚡ NEXUS AWAKENING ぁ 」</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏛ <b>Versus ended</b> — Match timed out.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━"
                ),
                kb=None
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

    state["msg_id"] = await _safe_edit_photo_board(
        chat_id=chat_id,
        msg_id=msg_id,
        text=(
            "<b>「 ⚡ NEXUS AWAKENING ぁ 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "⚔️ <b>Draft complete! Calculating clash…</b>\n"
            "━━━━━━━━━━━━━━━━━"
        ),
        kb=None
    )

    await asyncio.sleep(1.5)

    try:
        battle = resolve_battle(state, db)
        result_text = build_result_text(state, battle, db)
    except Exception as e:
        try:
            await bot.send_message(
                chat_id, f"⚠️ <b>Something went wrong calculating the result.</b>\n<code>{e}</code>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        del active_versus[key]
        return

    now = time.time()
    _versus_cooldowns[uid_a] = now
    _versus_cooldowns[uid_b] = now
    _versus_daily[_today_key(uid_a)] = _versus_daily.get(_today_key(uid_a), 0) + 1
    _versus_daily[_today_key(uid_b)] = _versus_daily.get(_today_key(uid_b), 0) + 1

    state["msg_id"] = await _safe_edit_photo_board(chat_id=chat_id, msg_id=msg_id, text=result_text, kb=None)

    del active_versus[key]


# ==========================================
# RULES COMMAND
# ==========================================
@main_router.message(Command("vsrule"))
async def vsrule_cmd(message: Message):
    uid = message.from_user.id
    if is_ghost_banned(uid) or is_shadow_banned(uid): return

    rules_text = (
        "<b>⚡ NEXUS AWAKENING — Versus Rules ⚡</b>\n\n"
        "Welcome to the Arena! Here is how Versus Mode works:\n\n"
        "1️⃣ <b>The Challenge</b>\n"
        "• Reply to another player's message with <code>/versus</code> to challenge them.\n"
        "• Use the <b>⚙️ Settings</b> button before accepting to choose a card tier: "
        "Divine, Elite, Basic, or Mix.\n\n"
        "2️⃣ <b>The Draft Phase</b>\n"
        "• Players take turns pulling a random card from their owned deck matching the chosen tier.\n"
        "• Assign the pulled card to one of the 7 slots: <b>Strength, Mana, Defence, Agility, Vitality, Intelligence, or Luck</b>.\n"
        "• Each slot can only be used once per player.\n"
        "• You can discard any card you don't want using the <b>⏭️ Skip Card</b> button (max 2 skips per duel).\n\n"
        "3️⃣ <b>The Clash Resolution</b>\n"
        "• <b>Stats (Strength to Intelligence):</b> The cards in each corresponding slot are compared directly. "
        "The card with the higher stat value in that field wins and scores 1 point.\n"
        "• <b>Luck Slot:</b> The winner of this slot is determined entirely at random (50/50 chance), scoring 1 point.\n\n"
        "4️⃣ <b>Winning</b>\n"
        "• The player with the highest score after comparing all 7 slots is the winner!"
    )
    await message.reply(rules_text, parse_mode=ParseMode.HTML)
