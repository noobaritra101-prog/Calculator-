import time
import random
import asyncio
import html
from datetime import datetime, timezone
from aiogram import F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
)
from aiogram.filters import Command
from aiogram.enums import ParseMode, ButtonStyle
from aiogram.exceptions import TelegramBadRequest

from config import (
    bot, main_router, ADMIN_IDS,
    format_rarity, load_db, save_db,
    ensure_user, get_mention,
    is_ghost_banned, is_shadow_banned,
    get_daily_minigame_rewards, DAILY_MINIGAME_REWARD_CAP
)
from char_stats import get_char_stats, STAT_FIELDS
from gcard import GCARD_REWARD_PER_GUESS

# ==========================================
# CONSTANTS
# ==========================================
VERSUS_DAILY_CAP = 10    # legacy — no longer enforced; rewards are gated only by DAILY_MINIGAME_REWARD_CAP
ACCEPT_TIMEOUT   = 60    # seconds to accept challenge
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

ROLE_ICONS = {
    "Strength":     "💪",
    "Mana":         "🔮",
    "Defence":      "💢",
    "Agility":      "🚤",
    "Vitality":     "🐋",
    "Intelligence": "🎓",
    "Luck":         "☘️",
}

MODES        = ["Divine", "Elite", "Basic", "Mix"]
MODE_ICONS   = {"Divine": "❄️", "Elite": "⚓", "Basic": "🃏", "Mix": "🌀"}

# In-memory state
active_versus: dict        = {}
_versus_daily: dict        = {}
_last_click: dict          = {}   # uid -> timestamp of last accepted Versus button click

CLICK_COOLDOWN = 2.0  # seconds — minimum gap between accepted button clicks per user

_vsrule_last_use: dict = {}   # uid -> timestamp of last /vsrule command use
VSRULE_COOLDOWN = 5.0  # seconds — minimum gap between /vsrule uses per user


def _vsrule_command_allowed(uid: int) -> bool:
    """
    Rate-limits the /vsrule command per user to prevent spam.
    """
    now = time.time()
    last = _vsrule_last_use.get(uid, 0)
    if now - last < VSRULE_COOLDOWN:
        return False
    _vsrule_last_use[uid] = now
    return True


def _click_allowed(uid: int) -> bool:
    """
    Debounce rapid/duplicate button taps per user to avoid flooding Telegram
    with edit_message calls.
    """
    now = time.time()
    last = _last_click.get(uid, 0)
    if now - last < CLICK_COOLDOWN:
        return False
    _last_click[uid] = now
    return True


# ==========================================
# HELPERS
# ==========================================
def _today_key(uid: int) -> str:
    return f"{uid}_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def _state_key(uid_a: int, uid_b: int) -> frozenset:
    return frozenset({uid_a, uid_b})


def _get_owned_cards(uid: int, db: dict) -> list:
    """All card_ids user owns with amount >= 1."""
    user_cards = db["users"].get(str(uid), {}).get("cards", {})
    return [cid for cid, cd in user_cards.items() if cd.get("amount", 0) >= 1]


def _eligible_cards(uid: int, mode: str, db: dict) -> list:
    """
    Owned cards that are usable in Versus.
    """
    owned = _get_owned_cards(uid, db)
    eligible = []
    for cid in owned:
        cdata = db["global_cards"].get(cid, {})
        cs    = get_char_stats(cdata.get("name", ""))
        if not cs:
            continue
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
    Safely edits a board message. Never creates a new message.
    """
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0

    for attempt in range(MAX_RETRIES):
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
            pass

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
            pass

        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAY)

    return msg_id


# ==========================================
# BOARD BUILDER
# ==========================================
def _link(uid: int, name: str) -> str:
    """Telegram profile link safe from malformed HTML crashes."""
    return f'<a href="tg://user?id={uid}">{html.escape(name)}</a>'


async def _edit_pending_msg(cq: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    if cq.message.photo:
        await cq.message.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await cq.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


def _pending_kb(uid_a: int, uid_b: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Accept", callback_data=f"vs_accept_{uid_a}_{uid_b}", style=ButtonStyle.SUCCESS),
            InlineKeyboardButton(text="Decline", callback_data=f"vs_decline_{uid_a}_{uid_b}", style=ButtonStyle.DANGER),
        ],
        [
            InlineKeyboardButton(text="⚙️ Settings", callback_data=f"vs_settings_{uid_a}_{uid_b}", style=ButtonStyle.PRIMARY),
        ]
    ])


def _settings_kb(uid_a: int, uid_b: int, current_mode: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for m in MODES:
        is_selected = (m == current_mode)
        row.append(InlineKeyboardButton(
            text=f"{MODE_ICONS[m]} {m}",
            callback_data=f"vs_setmatchmode_{m}_{uid_a}_{uid_b}",
            style=ButtonStyle.SUCCESS if is_selected else ButtonStyle.PRIMARY
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Back & Save", callback_data=f"vs_back_{uid_a}_{uid_b}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_board(state: dict, db: dict,
                 stage_hint: str = "",
                 pulled_card_id: str = None) -> tuple[str, InlineKeyboardMarkup | None]:
    uid_a    = state["challenger"]
    uid_b    = state["opponent"]
    name_a   = state["name_a"]
    name_b   = state["name_b"]
    roster_a = state["roster_a"]
    roster_b = state["roster_b"]
    turn_uid = state["draft_turn"]
    mode     = state["mode"]

    status_line_a = " ~ READY" if state.get("ready_a") else ""
    status_line_b = " ~ READY" if state.get("ready_b") else ""

    link_a = f"{_link(uid_a, name_a)}{status_line_a}"
    link_b = f"{_link(uid_b, name_b)}{status_line_b}"

    def role_line(role: str, roster: dict) -> str:
        cid = roster.get(role)
        padded_role = f"{role:<16}"
        if not cid:
            return f"⦿  <b>{padded_role}</b> ➜  ○○○"
        cdata = db["global_cards"].get(cid, {})
        card_name = cdata.get('name', '?')
        rarity_formatted = format_rarity(cdata.get('rarity',''))
        return f"⦿  <b>{padded_role}</b> ➜  <b>{card_name}</b>  《 {rarity_formatted} 》"

    lines_a = "\n".join(role_line(r, roster_a) for r in ROLES)
    lines_b = "\n".join(role_line(r, roster_b) for r in ROLES)

    turn_link = _link(uid_a, name_a) if turn_uid == uid_a else _link(uid_b, name_b)

    pulled_line = ""
    if pulled_card_id:
        cdata = db["global_cards"].get(pulled_card_id, {})
        pulled_line = (
            f"\n\n🎲 <b>Pulled:</b> {cdata.get('name','?')}"
            f"  《 {format_rarity(cdata.get('rarity',''))} 》"
        )

    draw_req_line = ""
    if stage_hint == "pull":
        req_a = state.get("draw_req_a")
        req_b = state.get("draw_req_b")
        if req_a and not req_b:
            draw_req_line = f"\n\n🤝 <b>{name_a}</b> has offered a draw. Waiting for {name_b}…"
        elif req_b and not req_a:
            draw_req_line = f"\n\n🤝 <b>{name_b}</b> has offered a draw. Waiting for {name_a}…"

    text = (
        f"<b>「  NEXUS  — {mode} Draft ぁ 」</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⬤ <b>{link_a}</b>\n"
        f"{lines_a}\n\n"
        f"⬤ <b>{link_b}</b>\n"
        f"{lines_b}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
    )

    if state["stage"] == "ready_check":
        text += "<b>⚔️ Both players must click Ready to begin!</b>"
    else:
        text += f"<b>Turn   ➜  {turn_link}</b>"

    text += pulled_line
    text += draw_req_line

    kb = None

    if stage_hint == "pull":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎲 Pull Card",
                    callback_data=f"vs_pull_{turn_uid}",
                    style=ButtonStyle.PRIMARY
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🤝 Let's Draw",
                    callback_data=f"vs_drawreq_{uid_a}_{uid_b}",
                    style=ButtonStyle.DANGER
                ),
            ],
        ])

    elif stage_hint == "role" and pulled_card_id:
        roster     = roster_a if turn_uid == uid_a else roster_b
        empty_roles = [r for r in ROLES if r not in roster]
        rows = []
        row  = []
        for role in empty_roles:
            icon = ROLE_ICONS[role]
            row.append(InlineKeyboardButton(
                text=f"{icon} {role}",
                callback_data=f"vs_role_{turn_uid}_{pulled_card_id}_{role}"
            ))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        skips_left = state.get("skip_a", 2) if turn_uid == uid_a else state.get("skip_b", 2)
        if skips_left > 0:
            rows.append([InlineKeyboardButton(
                text=f"⏭️ Skip Card ({skips_left} Left)",
                callback_data=f"vs_skip_{turn_uid}",
                style=ButtonStyle.DANGER
            )])

        kb = InlineKeyboardMarkup(inline_keyboard=rows)

    elif state["stage"] == "ready_check":
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="Ready",
                callback_data=f"vs_ready_{uid_a}_{uid_b}",
                style=ButtonStyle.SUCCESS
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
def _fmt_score(n) -> str:
    return str(int(n)) if float(n) == int(n) else str(n)


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

    lines = [
        "<b>「 ⚡ NEXUS AWAKENING — RESULT ぁ 」</b>",
        "━━━━━━━━━━━━━━",
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

        if role == "Luck":
            if w == "a":
                winner_link = link_a
                body = f" <i><b>{cd_a.get('name','?')} was favored by fortune over {cd_b.get('name','?')}. (+1)</b></i>"
            elif w == "b":
                winner_link = link_b
                body = f" <i><b>{cd_b.get('name','?')} was favored by fortune over {cd_a.get('name','?')}. (+1)</b></i>"
            else:
                winner_link = "Draw"
                body = f" <i><b>{cd_a.get('name','?')} and {cd_b.get('name','?')} shared equal fortune.</b></i>"
        else:
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
        lines.append("")

    if lines[-1] == "":
        lines.pop()

    lines.append("━━━━━━━━━━━━━━")
    lines.append("📊 <b>Score</b>")

    winner_uid = battle["winner"]
    if winner_uid is None:
        lines.append(f" {link_a} — {_fmt_score(battle['score_a'])}")
        lines.append(f" {link_b} — {_fmt_score(battle['score_b'])}")
        lines.append("\n⚖️ <b>DRAW!</b>")
    else:
        if winner_uid == uid_a:
            lines.append(f" {link_a} — {_fmt_score(battle['score_a'])}  [ Winner ]")
            lines.append(f" {link_b} — {_fmt_score(battle['score_b'])}")
        else:
            lines.append(f" {link_a} — {_fmt_score(battle['score_a'])}")
            lines.append(f" {link_b} — {_fmt_score(battle['score_b'])}  [ Winner ]")

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
        await message.reply("You cannot challenge a bot.", parse_mode=ParseMode.HTML)
        return
    if target.id == uid:
        await message.reply("You cannot challenge yourself.", parse_mode=ParseMode.HTML)
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

    saved_mode = db["users"].get(str(uid), {}).get("default_versus_mode", "Mix")
    if saved_mode not in MODES:
        saved_mode = "Mix"

    owned_a = _eligible_cards(uid, saved_mode, db)
    if len(owned_a) < 8:
        await message.reply(
            "You need at least 8 eligible cards in your deck to participate in Versus.",
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
        "mode":        saved_mode,
        "roster_a":    {},
        "roster_b":    {},
        "draft_turn":  uid,
        "score_a":     0,
        "score_b":     0,
        "ready_a":     False,
        "ready_b":     False,
        "skip_a":      2,
        "skip_b":      2,
        "draw_req_a":  False,
        "draw_req_b":  False,
        "expires":     time.time() + ACCEPT_TIMEOUT,
        "photo_board_active": False,
        "processing":  False,
        "pending_card": None,
        "start_time":  None,
    }

    kb = _pending_kb(uid, target.id)

    pending_text = (
        f"<b>{name_a} has challenged {name_b} to a Card Battle!</b>\n\n"
        f"<b>⤷「 Mode: {MODE_ICONS[saved_mode]} {saved_mode} 」</b>\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"<b><i>{name_b}, will you accept the challenge?</i></b>"
    )

    board_pic = db.get("settings", {}).get("pic_versus")
    if board_pic:
        msg = await message.reply_photo(
            photo=board_pic,
            caption=pending_text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
        active_versus[key]["photo_board_active"] = True
    else:
        msg = await message.reply(
            pending_text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
    active_versus[key]["msg_id"] = msg.message_id
    asyncio.create_task(_accept_timeout(key, msg.message_id, message.chat.id))


async def _accept_timeout(key: frozenset, msg_id: int, chat_id: int):
    await asyncio.sleep(ACCEPT_TIMEOUT)
    if key in active_versus and active_versus[key]["stage"] == "pending":
        state = active_versus[key]
        opponent_first_name = state["name_b"].split()[0] if state.get("name_b") else "they"
        del active_versus[key]
        try:
            await _safe_edit_photo_board(
                chat_id=chat_id,
                msg_id=msg_id,
                text=(
                    "<b>「 ⏰ Versus Timeout 」</b>\n\n"
                    f"💢 Hmph... {opponent_first_name} didn't reply in time 😤 !!"
                ),
                kb=None
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

    if cq.from_user.id != uid_a:
        await cq.answer("⚠️ Only the challenger can change settings.", show_alert=True)
        return

    if not _click_allowed(cq.from_user.id):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
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

    await _edit_pending_msg(
        cq,
        f"<b>⚙️ Versus Match Settings</b>\n"
        f"Select the character tier to draft from for this match:\n\n"
        f"Current: {MODE_ICONS[current_mode]} <b>{current_mode}</b>",
        kb
    )
    await cq.answer()


@main_router.callback_query(F.data.startswith("vs_setmatchmode_"))
async def vs_setmatchmode_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    mode  = parts[2]
    uid_a = int(parts[3])
    uid_b = int(parts[4])
    key   = _state_key(uid_a, uid_b)

    if cq.from_user.id != uid_a:
        await cq.answer("⚠️ Only the challenger can change settings.", show_alert=True)
        return

    if not _click_allowed(cq.from_user.id):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
        return

    if key not in active_versus:
        await cq.answer("⚠️ Challenge has expired.", show_alert=True)
        return

    state = active_versus[key]
    if state["stage"] != "pending":
        await cq.answer("⚠️ Challenge already in progress.", show_alert=True)
        return

    db = load_db()
    owned_a_mode = _eligible_cards(uid_a, mode, db)
    owned_b_mode = _eligible_cards(uid_b, mode, db)
    if len(owned_a_mode) < 8:
        await cq.answer(f"⚠️ You don't own 8 eligible characters for {mode} mode.", show_alert=True)
        return
    if len(owned_b_mode) < 8:
        await cq.answer(
            f"⚠️ {state['name_b']} doesn't own 8 eligible characters for {mode} mode.",
            show_alert=True
        )
        return

    state["mode"] = mode
    kb = _settings_kb(uid_a, uid_b, mode)

    await _edit_pending_msg(
        cq,
        f"<b>⚙️ Versus Match Settings</b>\n"
        f"Select the character tier to draft from for this match:\n\n"
        f"Current: {MODE_ICONS[mode]} <b>{mode}</b>",
        kb
    )
    await cq.answer(f"Match mode set to {mode}!")


@main_router.callback_query(F.data.startswith("vs_back_"))
async def vs_back_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid_a = int(parts[2])
    uid_b = int(parts[3])
    key   = _state_key(uid_a, uid_b)

    if cq.from_user.id != uid_a:
        await cq.answer("⚠️ Only the challenger can change settings.", show_alert=True)
        return

    if not _click_allowed(cq.from_user.id):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
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

    db = load_db()
    ensure_user(uid_a, state["name_a"])
    if db["users"].get(str(uid_a), {}).get("default_versus_mode") != mode:
        db["users"][str(uid_a)]["default_versus_mode"] = mode
        save_db()

    kb = _pending_kb(uid_a, uid_b)
    await _edit_pending_msg(
        cq,
        f"<b>{name_a} has challenged {name_b} to a Card Battle!</b>\n\n"
        f"「 Mode: {MODE_ICONS[mode]} {mode} 」\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<b><i>{name_b}, will you accept the challenge?</i></b>",
        kb
    )
    await cq.answer("✅ Settings saved!")


@main_router.callback_query(F.data.startswith("vs_accept_"))
async def vs_accept_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid_a = int(parts[2])
    uid_b = int(parts[3])
    key   = _state_key(uid_a, uid_b)

    if cq.from_user.id != uid_b:
        await cq.answer("⚠️ This challenge isn't for you.", show_alert=True)
        return

    if not _click_allowed(cq.from_user.id):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
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

        owned_a_mode = _eligible_cards(uid_a, state["mode"], db)
        owned_b_mode = _eligible_cards(uid_b, state["mode"], db)
        if len(owned_a_mode) < 8:
            await cq.answer(
                f"⚠️ {state['name_a']} no longer owns 8 eligible characters for {state['mode']} mode.",
                show_alert=True
            )
            void_text = (
                f"<b>「 𝗔𝗡𝗜𝗠𝗘 𝗡𝗘𝗫𝗨𝗦 ぁ 」</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>Challenge cancelled</b> — {state['name_a']} doesn't have 8 eligible "
                f"characters for {state['mode']} mode.\n"
                f"━━━━━━━━━━━━━━━"
            )
            try:
                await _safe_edit_photo_board(state["chat_id"], cq.message.message_id, void_text, kb=None)
            except Exception:
                pass
            del active_versus[key]
            return
        if len(owned_b_mode) < 8:
            await cq.answer(
                f"⚠️ You don't own 8 eligible characters for {state['mode']} mode.",
                show_alert=True
            )
            void_text = (
                f"<b>「 𝗔𝗡𝗜𝗠𝗘 𝗡𝗘𝗫𝗨𝗦 ぁ 」</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>Challenge cancelled</b> — {state['name_b']} doesn't have 8 eligible "
                f"characters for {state['mode']} mode.\n"
                f"━━━━━━━━━━━━━━━"
            )
            try:
                await _safe_edit_photo_board(state["chat_id"], cq.message.message_id, void_text, kb=None)
            except Exception:
                pass
            del active_versus[key]
            return

        state["stage"]      = "drafting"
        state["expires"]    = time.time() + DRAFT_TIMEOUT
        state["start_time"] = time.time()

        text, kb = _build_board(state, db, stage_hint="pull")
        state["msg_id"] = await _safe_edit_photo_board(state["chat_id"], cq.message.message_id, text, kb)
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

    if not _click_allowed(cq.from_user.id):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
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
        if cq.message.photo:
            await cq.message.edit_caption(caption="<b>Challenge declined.</b>", parse_mode=ParseMode.HTML)
        else:
            await cq.message.edit_text("<b>Challenge declined.</b>", parse_mode=ParseMode.HTML)
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

    if not _click_allowed(cq.from_user.id):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
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
            await cq.answer("No available cards left in your deck!", show_alert=True)
            return

        cdata = db["global_cards"].get(card_id, {})
        file_id = cdata.get("file_id")
        state["expires"] = time.time() + DRAFT_TIMEOUT
        state["pending_card"] = card_id

        text, kb = _build_board(state, db, stage_hint="role", pulled_card_id=card_id)
        is_first_pull = not state.get("photo_board_active")

        if is_first_pull:
            if file_id:
                try:
                    sent = await bot.send_photo(
                        chat_id=state["chat_id"],
                        photo=file_id,
                        caption=text,
                        parse_mode=ParseMode.HTML,
                        has_spoiler=True,
                        show_caption_above_media=True,
                        reply_markup=kb
                    )
                    state["msg_id"] = sent.message_id
                    state["photo_board_active"] = True
                    try:
                        await cq.message.delete()
                    except Exception:
                        pass
                except Exception:
                    state["msg_id"] = await _safe_edit_photo_board(state["chat_id"], cq.message.message_id, text, kb)
                    state["photo_board_active"] = False
            else:
                state["msg_id"] = await _safe_edit_photo_board(state["chat_id"], cq.message.message_id, text, kb)
                state["photo_board_active"] = False
        else:
            state["msg_id"] = await _safe_edit_photo_board(
                chat_id=state["chat_id"],
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

    if not _click_allowed(cq.from_user.id):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
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
            await cq.answer("You have no skips remaining!", show_alert=True)
            return

        state[skip_key] = skips_left - 1
        state["expires"] = time.time() + DRAFT_TIMEOUT
        state["pending_card"] = None

        db = load_db()
        text, kb = _build_board(state, db, stage_hint="pull")
        state["msg_id"] = await _safe_edit_photo_board(state["chat_id"], state["msg_id"], text, kb)

        await cq.answer(f"⏭️ Card skipped! {state[skip_key]} skips remaining.")
    finally:
        state["processing"] = False


# ==========================================
# DRAW REQUEST — both players must agree
# ==========================================
async def _finalize_mutual_draw(key: frozenset, chat_id: int, db: dict, msg_id: int):
    if key not in active_versus:
        return

    state = active_versus[key]
    uid_a = state["challenger"]
    uid_b = state["opponent"]

    vstats = db.setdefault("versus_stats", {
        "total_battles": 0,
        "completed_battles": 0,
        "cancelled_battles": 0,
        "total_shards_distributed": 0,
        "total_match_duration": 0,
        "match_duration_count": 0,
        "pvp_players": {}
    })

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_stats = db.setdefault("versus_daily_stats", {})
    if daily_stats.get("date") != today_str:
        daily_stats["date"] = today_str
        daily_stats["battles_today"] = 0
        daily_stats["shards_distributed_today"] = 0
        daily_stats["active_players"] = []
        daily_stats["player_battles_today"] = {}

    vstats["total_battles"] = vstats.get("total_battles", 0) + 1
    vstats["completed_battles"] = vstats.get("completed_battles", 0) + 1
    daily_stats["battles_today"] = daily_stats.get("battles_today", 0) + 1

    for u in [uid_a, uid_b]:
        daily_stats.setdefault("active_players", [])
        if u not in daily_stats["active_players"]:
            daily_stats["active_players"].append(u)
        daily_stats["player_battles_today"][str(u)] = daily_stats["player_battles_today"].get(str(u), 0) + 1
        p_record = vstats.setdefault("pvp_players", {}).setdefault(
            str(u), {"wins": 0, "losses": 0, "draws": 0, "streak": 0, "max_streak": 0}
        )
        p_record["draws"] = p_record.get("draws", 0) + 1
        p_record["streak"] = 0

    save_db()

    uid_key_a = _today_key(uid_a)
    uid_key_b = _today_key(uid_b)
    _versus_daily[uid_key_a] = _versus_daily.get(uid_key_a, 0) + 1
    _versus_daily[uid_key_b] = _versus_daily.get(uid_key_b, 0) + 1

    final_text = (
        "<b>「 ⚡ NEXUS AWAKENING ぁ 」</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🤝 <b>{_link(uid_a, state['name_a'])}</b> and <b>{_link(uid_b, state['name_b'])}</b> "
        "agreed to a draw.\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "<b>「 𝗩𝗘𝗥𝗦𝗨𝗦 𝗥𝗘𝗪𝗔𝗥𝗗𝗦 」</b>\n"
        "⚖️ Match ended in a mutual draw! No rewards distributed."
    )
    await _safe_edit_photo_board(chat_id=chat_id, msg_id=msg_id, text=final_text, kb=None)

    del active_versus[key]


@main_router.callback_query(F.data.startswith("vs_drawreq_"))
async def vs_drawreq_cb(cq: CallbackQuery):
    parts = cq.data.split("_")
    uid_a = int(parts[2])
    uid_b = int(parts[3])
    key   = _state_key(uid_a, uid_b)

    clicker = cq.from_user.id
    if clicker != uid_a and clicker != uid_b:
        await cq.answer("⚠️ You are not part of this battle.", show_alert=True)
        return

    if not _click_allowed(clicker):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
        return

    if key not in active_versus:
        await cq.answer("⚠️ No active versus found.", show_alert=True)
        return

    state = active_versus[key]
    if state["stage"] != "drafting":
        await cq.answer("⚠️ Draw offers are only available during the draft.", show_alert=True)
        return

    if state.get("processing"):
        await cq.answer("⏳ Processing…", show_alert=False)
        return
    state["processing"] = True

    try:
        req_key = "draw_req_a" if clicker == uid_a else "draw_req_b"
        if state.get(req_key):
            await cq.answer("You've already offered a draw. Waiting for your opponent.", show_alert=True)
            return

        state[req_key] = True
        await cq.answer("🤝 Draw offer sent!")

        if state.get("draw_req_a") and state.get("draw_req_b"):
            db = load_db()
            await _finalize_mutual_draw(key, state["chat_id"], db, state["msg_id"])
            return

        db = load_db()
        text, kb = _build_board(state, db, stage_hint="pull")
        state["msg_id"] = await _safe_edit_photo_board(state["chat_id"], state["msg_id"], text, kb)
    finally:
        if key in active_versus:
            active_versus[key]["processing"] = False


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

    if not _click_allowed(cq.from_user.id):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
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

        if state.get("pending_card") != card_id:
            await cq.answer("⚠️ This card has already been assigned. Pull a new card.", show_alert=True)
            return

        if role in roster:
            await cq.answer("⚠️ Role already taken. Pick another.", show_alert=True)
            return

        roster[role]          = card_id
        state["pending_card"] = None
        state["expires"]      = time.time() + DRAFT_TIMEOUT

        db = load_db()
        await cq.answer(f"✅ {role} assigned!")

        if len(state["roster_a"]) == len(ROLES) and len(state["roster_b"]) == len(ROLES):
            state["stage"] = "ready_check"
            state["ready_a"] = False
            state["ready_b"] = False
            state["expires"] = time.time() + DRAFT_TIMEOUT
            text, kb = _build_board(state, db)
            state["msg_id"] = await _safe_edit_photo_board(state["chat_id"], state["msg_id"], text, kb)
            return

        state["draft_turn"] = uid_b if turn_uid == uid_a else uid_a
        text, kb = _build_board(state, db, stage_hint="pull")
        state["msg_id"] = await _safe_edit_photo_board(state["chat_id"], state["msg_id"], text, kb)
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

    if not _click_allowed(cq.from_user.id):
        await cq.answer("⏳ Slow down a bit!", show_alert=False)
        return

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

        state["expires"] = time.time() + DRAFT_TIMEOUT
        db = load_db()

        if state.get("ready_a") and state.get("ready_b"):
            await _finalize_battle(key, state["chat_id"], db, state["msg_id"])
            return

        text, kb = _build_board(state, db)
        state["msg_id"] = await _safe_edit_photo_board(state["chat_id"], state["msg_id"], text, kb)
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

        db = load_db()
        vstats = db.setdefault("versus_stats", {})
        vstats["total_battles"] = vstats.get("total_battles", 0) + 1
        vstats["cancelled_battles"] = vstats.get("cancelled_battles", 0) + 1
        save_db()

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
# FINALIZE BATTLE & REWARDS
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

    MAX_FINALIZE_ATTEMPTS = 3
    RETRY_DELAY = 3.0

    battle = None
    result_text = None
    last_error = None

    for attempt in range(1, MAX_FINALIZE_ATTEMPTS + 1):
        try:
            fresh_db = load_db()
            battle = resolve_battle(state, fresh_db)
            result_text = build_result_text(state, battle, fresh_db)
            last_error = None
            break
        except Exception as e:
            last_error = e
            if attempt < MAX_FINALIZE_ATTEMPTS:
                try:
                    await _safe_edit_photo_board(
                        chat_id=chat_id,
                        msg_id=state["msg_id"],
                        text=(
                            "<b>「 ⚡ NEXUS AWAKENING ぁ 」</b>\n"
                            "━━━━━━━━━━━━━━━━━\n"
                            "⚠️ <b>Hiccup calculating the result — retrying…</b>\n"
                            f"<i>Attempt {attempt}/{MAX_FINALIZE_ATTEMPTS}</i>\n"
                            "━━━━━━━━━━━━━━━━━"
                        ),
                        kb=None
                    )
                except Exception:
                    pass
                await asyncio.sleep(RETRY_DELAY)

    if last_error is not None:
        error_text = (
            "<b>「 ⚡ NEXUS AWAKENING ぁ 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>Match voided</b> — couldn't calculate a result after several tries.\n"
            f"<code>{last_error}</code>\n"
            "Please start a new Versus.\n"
            "━━━━━━━━━━━━━━━━━"
        )
        try:
            await _safe_edit_photo_board(chat_id=chat_id, msg_id=state["msg_id"], text=error_text, kb=None)
        except Exception:
            pass
        del active_versus[key]
        return

    db = load_db()
    vstats = db.setdefault("versus_stats", {
        "total_battles": 0,
        "completed_battles": 0,
        "cancelled_battles": 0,
        "total_shards_distributed": 0,
        "total_match_duration": 0,
        "match_duration_count": 0,
        "pvp_players": {}
    })

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_stats = db.setdefault("versus_daily_stats", {})
    if daily_stats.get("date") != today_str:
        daily_stats["date"] = today_str
        daily_stats["battles_today"] = 0
        daily_stats["shards_distributed_today"] = 0
        daily_stats["active_players"] = []
        daily_stats["player_battles_today"] = {}

    vstats["total_battles"] = vstats.get("total_battles", 0) + 1
    vstats["completed_battles"] = vstats.get("completed_battles", 0) + 1
    daily_stats["battles_today"] = daily_stats.get("battles_today", 0) + 1

    if state.get("start_time"):
        match_dur = time.time() - state["start_time"]
        vstats["total_match_duration"] = vstats.get("total_match_duration", 0) + match_dur
        vstats["match_duration_count"] = vstats.get("match_duration_count", 0) + 1

    for u in [uid_a, uid_b]:
        daily_stats.setdefault("active_players", [])
        if u not in daily_stats["active_players"]:
            daily_stats["active_players"].append(u)
        daily_stats["player_battles_today"][str(u)] = daily_stats["player_battles_today"].get(str(u), 0) + 1
        vstats.setdefault("pvp_players", {}).setdefault(str(u), {"wins": 0, "losses": 0, "draws": 0, "streak": 0, "max_streak": 0})

    reward_msg = ""
    winner_uid = battle["winner"]

    if winner_uid:
        loser_uid = uid_b if winner_uid == uid_a else uid_a
        w_record = vstats["pvp_players"][str(winner_uid)]
        l_record = vstats["pvp_players"][str(loser_uid)]

        w_record["wins"] = w_record.get("wins", 0) + 1
        w_record["streak"] = w_record.get("streak", 0) + 1
        if w_record["streak"] > w_record.get("max_streak", 0):
            w_record["max_streak"] = w_record["streak"]

        l_record["losses"] = l_record.get("losses", 0) + 1
        l_record["streak"] = 0

        ensure_user(winner_uid, state["name_a"] if winner_uid == uid_a else state["name_b"])
        winner_data = db["users"][str(winner_uid)]
        v_rewards = get_daily_minigame_rewards(winner_data)

        current_rewarded_today = v_rewards.get("shards", 0)
        if current_rewarded_today < DAILY_MINIGAME_REWARD_CAP:
            reward_amount = min(150, DAILY_MINIGAME_REWARD_CAP - current_rewarded_today)
            winner_data["nexus_shards"] = winner_data.get("nexus_shards", 0) + reward_amount
            v_rewards["shards"] = current_rewarded_today + reward_amount

            vstats["total_shards_distributed"] = vstats.get("total_shards_distributed", 0) + reward_amount
            daily_stats["shards_distributed_today"] = daily_stats.get("shards_distributed_today", 0) + reward_amount

            reward_msg = (
                f"\n\n<b>「 𝗩𝗘𝗥𝗦𝗨𝗦 𝗥𝗘𝗪𝗔𝗥𝗗𝗦 」</b>\n"
                f"🏆 {_link(winner_uid, state['name_a'] if winner_uid == uid_a else state['name_b'])} won and received <b>+{reward_amount} 💠 Nexus Shards</b>!"
            )
            if v_rewards["shards"] >= DAILY_MINIGAME_REWARD_CAP:
                reward_msg += f"\n🎉 <i>You have reached your daily reward cap of {DAILY_MINIGAME_REWARD_CAP:,} 💠 shards (shared with Guess-the-Card)!</i>"
        else:
            reward_msg = (
                f"\n\n<b>「 𝗩𝗘𝗥𝗦𝗨𝗦 𝗥𝗘𝗪𝗔𝗥𝗗𝗦 」</b>\n"
                f"🏆 {_link(winner_uid, state['name_a'] if winner_uid == uid_a else state['name_b'])} won! "
                f"(No shards rewarded - daily cap reached)."
            )
    else:
        for u in [uid_a, uid_b]:
            p_record = vstats["pvp_players"][str(u)]
            p_record["draws"] = p_record.get("draws", 0) + 1
            p_record["streak"] = 0
        reward_msg = "\n\n<b>「 𝗩𝗘𝗥𝗦𝗨𝗦 𝗥𝗘𝗪𝗔𝗥𝗗𝗦 」</b>\n⚖️ Match ended in a draw! No rewards distributed."

    save_db()

    uid_key_a = _today_key(uid_a)
    uid_key_b = _today_key(uid_b)
    _versus_daily[uid_key_a] = _versus_daily.get(uid_key_a, 0) + 1
    _versus_daily[uid_key_b] = _versus_daily.get(uid_key_b, 0) + 1

    final_text = result_text + reward_msg
    await _safe_edit_photo_board(chat_id=chat_id, msg_id=state["msg_id"], text=final_text, kb=None)

    del active_versus[key]


# ==========================================
# ADMIN COMMANDS
# ==========================================
@main_router.message(Command("vim"))
async def vim_cmd(message: Message):
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        return

    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply(
            "⚠️ <b>Usage:</b> Reply to an image with <code>/vim</code> to set it as the Versus board image.",
            parse_mode=ParseMode.HTML
        )
        return

    file_id = message.reply_to_message.photo[-1].file_id

    db = load_db()
    db["settings"]["pic_versus"] = file_id
    save_db()

    await message.reply("✅ <b>Versus board image updated!</b>", parse_mode=ParseMode.HTML)


def _build_vstats_text(db: dict) -> str:
    vstats = db.get("versus_stats", {})
    daily_stats = db.get("versus_daily_stats", {})
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total_battles     = vstats.get("total_battles", 0)
    completed_battles = vstats.get("completed_battles", 0)
    cancelled_battles = vstats.get("cancelled_battles", 0)
    active_battles    = len(active_versus)

    battles_today = daily_stats.get("battles_today", 0) if daily_stats.get("date") == today_str else 0
    shards_today  = daily_stats.get("shards_distributed_today", 0) if daily_stats.get("date") == today_str else 0
    shards_total  = vstats.get("total_shards_distributed", 0)

    cap_hits = 0
    for user_id, udata in db.get("users", {}).items():
        vr = udata.get("minigame_rewards_today", {})
        if vr.get("date") == today_str and vr.get("shards", 0) >= DAILY_MINIGAME_REWARD_CAP:
            cap_hits += 1

    pvp_players   = vstats.get("pvp_players", {})
    total_players = len(pvp_players)
    active_today  = len(daily_stats.get("active_players", [])) if daily_stats.get("date") == today_str else 0

    highest_streak = 0
    for p_id, p_rec in pvp_players.items():
        highest_streak = max(highest_streak, p_rec.get("max_streak", 0))

    highest_battles_today = 0
    if daily_stats.get("date") == today_str:
        for p_id, cnt in daily_stats.get("player_battles_today", {}).items():
            highest_battles_today = max(highest_battles_today, cnt)

    dur = vstats.get("total_match_duration", 0)
    cnt = vstats.get("match_duration_count", 0)
    if cnt > 0:
        avg_sec = dur / cnt
        minutes, seconds = divmod(int(avg_sec), 60)
        avg_time_str = f"{minutes}m {seconds}s"
    else:
        avg_time_str = "2m 18s"

    # ── Guess-the-Card stats ────────────────────────────────────────────────
    gstats = db.get("gcard_stats", {})
    gdaily = db.get("gcard_daily_stats", {})

    g_total_rounds  = gstats.get("total_rounds", 0)
    g_correct       = gstats.get("correct_guesses", 0)
    g_timeouts      = gstats.get("timeouts", 0)
    g_shards_total  = gstats.get("total_shards_distributed", 0)
    g_accuracy      = (g_correct / g_total_rounds * 100) if g_total_rounds else 0.0

    g_rounds_today  = gdaily.get("rounds_today", 0) if gdaily.get("date") == today_str else 0
    g_correct_today = gdaily.get("correct_today", 0) if gdaily.get("date") == today_str else 0
    g_shards_today  = gdaily.get("shards_distributed_today", 0) if gdaily.get("date") == today_str else 0
    g_active_today  = len(gdaily.get("active_players", [])) if gdaily.get("date") == today_str else 0

    stats_text = (
    "<b>「 𝗩𝗘𝗥𝗦𝗨𝗦 𝗔𝗗𝗠𝗜𝗡 𝗦𝗧𝗔𝗧𝗦 」</b>\n\n"

    f"• <b>Total Battles:</b> <code>{total_battles:,}</code>\n"
    f"• <b>Battles Today:</b> <code>{battles_today:,}</code>\n"
    f"• <b>Active Battles:</b> <code>{active_battles:,}</code>\n"
    f"• <b>Completed Battles:</b> <code>{completed_battles:,}</code>\n"
    f"• <b>Cancelled / AFK:</b> <code>{cancelled_battles:,}</code>\n\n"

    "<b>【 Reward Statistics 】</b>\n\n"
    f"• <b>Shards Distributed Today:</b> <code>{shards_today:,} 💠</code>\n"
    f"• <b>Total Shards Distributed:</b> <code>{shards_total:,} 💠</code>\n"
    f"• <b>Players Hit Daily Cap:</b> <code>{cap_hits:,}</code>\n\n"

    "<b>【 Player Statistics 】</b>\n\n"
    f"• <b>Total PvP Players:</b> <code>{total_players:,}</code>\n"
    f"• <b>Active Today:</b> <code>{active_today:,}</code>\n"
    f"• <b>Highest Win Streak:</b> <code>{highest_streak:,}</code>\n"
    f"• <b>Highest Battles Today:</b> <code>{highest_battles_today:,}</code>\n\n"

    "<b>【 System 】</b>\n\n"
    f"• <b>Average Match Time:</b> <code>{avg_time_str}</code>\n"
    "• <b>AFK Timeout:</b> <code>60s</code>\n"
    "• <b>Reward Per Win:</b> <code>150 💠</code>\n"
    f"• <b>Daily Reward Cap:</b> <code>{DAILY_MINIGAME_REWARD_CAP:,} 💠 (shared w/ Guess-the-Card)</code>\n\n"

    "<b>「 𝗚𝗨𝗘𝗦𝗦-𝗧𝗛𝗘-𝗖𝗔𝗥𝗗 𝗦𝗧𝗔𝗧𝗦 」</b>\n\n"
    f"• <b>Total Rounds:</b> <code>{g_total_rounds:,}</code>\n"
    f"• <b>Rounds Today:</b> <code>{g_rounds_today:,}</code>\n"
    f"• <b>Correct Guesses:</b> <code>{g_correct:,}</code>\n"
    f"• <b>Correct Today:</b> <code>{g_correct_today:,}</code>\n"
    f"• <b>Timeouts (No Guess):</b> <code>{g_timeouts:,}</code>\n"
    f"• <b>Accuracy:</b> <code>{g_accuracy:.1f}%</code>\n"
    f"• <b>Active Today:</b> <code>{g_active_today:,}</code>\n"
    f"• <b>Shards Distributed Today:</b> <code>{g_shards_today:,} 💠</code>\n"
    f"• <b>Total Shards Distributed:</b> <code>{g_shards_total:,} 💠</code>\n"
    f"• <b>Reward Per Correct Guess:</b> <code>{GCARD_REWARD_PER_GUESS} 💠</code>"
)

    return stats_text


def _vstats_refresh_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="vstats_refresh")]
    ])


@main_router.message(Command("vstats"))
async def vstats_cmd(message: Message):
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        return

    db = load_db()
    stats_text = _build_vstats_text(db)
    await message.reply(stats_text, parse_mode=ParseMode.HTML, reply_markup=_vstats_refresh_kb())


@main_router.callback_query(F.data == "vstats_refresh")
async def vstats_refresh_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    if uid not in ADMIN_IDS:
        await cq.answer("Admins only.", show_alert=True)
        return

    db = load_db()
    stats_text = _build_vstats_text(db)
    try:
        await cq.message.edit_text(stats_text, parse_mode=ParseMode.HTML, reply_markup=_vstats_refresh_kb())
        await cq.answer("Refreshed ✅")
    except TelegramBadRequest:
        # Content unchanged since last refresh — nothing to edit
        await cq.answer("Already up to date.")


# ==========================================
# RULES COMMAND
# ==========================================
def _vsrule_menu_text() -> str:
    return (
        "<b>⚡ NEXUS AWAKENING — Versus Arena ⚡</b>\n\n"
        "Welcome to the Arena! Card Battle is a 1v1 PvP minigame where you draft "
        "cards from your own collection and clash stat-for-stat against another player.\n\n"
        "Tap a button below for details:\n"
        "📖 <b>How to Play</b> — step-by-step walkthrough of a match.\n"
        "📜 <b>Rules</b> — rewards, limits, and restrictions."
    )


def _vsrule_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📖 How to Play", callback_data="vsrule_howto", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="📜 Rules", callback_data="vsrule_rules", style=ButtonStyle.SUCCESS),
        ]
    ])


def _vsrule_howto_text() -> str:
    return (
        "<b>📖 HOW TO PLAY — Versus Mode</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Tap a step below to expand it:\n\n"

        "1️⃣ <b>Issue the Challenge</b>\n"
        "<blockquote expandable>"
        "• Reply to a player's message with <code>/versus</code> to challenge them "
        "(<b>60s</b> to Accept).\n"
        "• Challenger picks the card tier via <b>⚙️ Settings</b>: <b>Divine, Elite, "
        "Basic,</b> or <b>Mix</b>."
        "</blockquote>\n\n"

        "2️⃣ <b>Ready Up</b>\n"
        "<blockquote expandable>"
        "• Both players tap <b>Ready</b> to start the draft."
        "</blockquote>\n\n"

        "3️⃣ <b>The Draft Phase</b>\n"
        "<blockquote expandable>"
        "• Take turns: tap <b>🎲 Pull Card</b>, then assign it to a slot — "
        "<b>Strength, Mana, Defence, Agility, Vitality, Intelligence, Luck</b>.\n"
        "• <b>⏭️ Skip Card</b> discards a pull for a new one (max <b>2</b> per player).\n"
        "• <b>🤝 Let's Draw</b> ends the match early if both players agree — no reward.\n"
        "• <b>5 minutes</b> per turn, or the match may be forfeited as AFK."
        "</blockquote>\n\n"

        "4️⃣ <b>The Clash</b>\n"
        "<blockquote expandable>"
        "• Matching slots are compared — higher stat wins the slot (+1 point), ties split it.\n"
        "• <b>Luck</b> is a 50/50 coin flip."
        "</blockquote>\n\n"

        "5️⃣ <b>The Result</b>\n"
        "<blockquote expandable>"
        "• Highest total score across all 7 slots wins the duel."
        "</blockquote>\n\n"

        "Tap <b>📜 Rules</b> to see rewards and limits, or <b>🔙 Back</b> to return."
    )


def _vsrule_rules_text() -> str:
    return (
        "<b>📜 RULES — Rewards & Limits</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "<b>「 𝗥𝗘𝗪𝗔𝗥𝗗𝗦 」</b>\n"
        "◉ <b>Victory Reward:</b> +150 💠 Nexus Shards per win.\n"
        f"◉ <b>Daily Shard Cap:</b> {DAILY_MINIGAME_REWARD_CAP:,} 💠 Nexus Shards per day — "
        "shared with the Guess-the-Card game. Once you hit it (from either game), further "
        "wins pay 0 shards until it resets, though a win that lands right at the cap can "
        "still pay out a partial amount.\n"
        "◉ <b>Defeat / Draw Reward:</b> No shards, win or nothing.\n"
        "◉ <b>Mutual Draw:</b> If both players tap <b>🤝 Let's Draw</b>, the match ends "
        "instantly as a draw — pays no shards.\n\n"
        "<b>「 𝗥𝗘𝗦𝗧𝗥𝗜𝗖𝗧𝗜𝗢𝗡𝗦 」</b>\n"
        "◉ You need at least <b>8 eligible cards</b> in the chosen tier to start or accept a duel.\n"
        "◉ You can't challenge yourself or a bot.\n"
        "◉ You can only have <b>one active Versus</b> at a time — finish it before starting another.\n"
        "◉ <b>AFK Rule:</b> If either player fails to respond or participate in time, "
        "no rewards will be granted.\n\n"
        "Tap <b>📖 How to Play</b> for a full walkthrough, or <b>🔙 Back</b> to return."
    )


def _vsrule_detail_kb(other: str) -> InlineKeyboardMarkup:
    other_label = "📖 How to Play" if other == "vsrule_howto" else "📜 Rules"
    other_style = ButtonStyle.PRIMARY if other == "vsrule_howto" else ButtonStyle.SUCCESS
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=other_label, callback_data=other, style=other_style)],
        [InlineKeyboardButton(text="🔙 Back", callback_data="vsrule_menu", style=ButtonStyle.DANGER)]
    ])


@main_router.message(Command("vsrule"))
async def vsrule_cmd(message: Message):
    uid = message.from_user.id
    if is_ghost_banned(uid) or is_shadow_banned(uid): return

    if not _vsrule_command_allowed(uid):
        return

    await message.reply(
        _vsrule_menu_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_vsrule_menu_kb()
    )


@main_router.callback_query(F.data == "vsrule_howto")
async def vsrule_howto_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    if is_ghost_banned(uid) or is_shadow_banned(uid):
        await cq.answer()
        return

    await cq.message.edit_text(
        _vsrule_howto_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_vsrule_detail_kb("vsrule_rules")
    )
    await cq.answer()


@main_router.callback_query(F.data == "vsrule_rules")
async def vsrule_rules_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    if is_ghost_banned(uid) or is_shadow_banned(uid):
        await cq.answer()
        return

    await cq.message.edit_text(
        _vsrule_rules_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_vsrule_detail_kb("vsrule_howto")
    )
    await cq.answer()


@main_router.callback_query(F.data == "vsrule_menu")
async def vsrule_menu_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    if is_ghost_banned(uid) or is_shadow_banned(uid):
        await cq.answer()
        return

    await cq.message.edit_text(
        _vsrule_menu_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_vsrule_menu_kb()
    )
    await cq.answer()
