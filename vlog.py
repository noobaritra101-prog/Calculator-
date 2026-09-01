import os
import json
import time
import asyncio
from datetime import datetime, timezone

from aiogram.types import (
    Message, 
    BufferedInputFile, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton
)
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode, ChatType

import config
from config import bot, main_router, ADMIN_IDS, resolve_target, get_mention, load_db, save_db

# ==========================================
# CONFIGURATION
# ==========================================
LOG_RETENTION_SECS = 7 * 24 * 3600     # Logs older than 7 days are auto-purged
CLEANUP_INTERVAL_SECS = 3600           # Background purge runs every hour
VLOGS_FILE = "vlog.json"               # Saved inside database.zip

_TYPE_LABELS = {
    "sgive_sent":     "💠 SHARD GIFT — SENT",
    "sgive_received": "💠 SHARD GIFT — RECEIVED",
    "gift_sent":      "🎁 CARD GIFT — SENT",
    "gift_received":  "🎁 CARD GIFT — RECEIVED",
    "burn":           "🔥 CARD BURNED",
    "trade_sent":     "🔄 CARD TRADE",
    "store_buy_online":   "🛒 STORE PURCHASE — ONLINE",
    "store_buy_offline":  "🛍️ STORE PURCHASE — OFFLINE",
    "store_sell_offline": "💰 STORE SALE — OFFLINE",
}

_vlogs_cache = {}
_vlogs_dirty = False

# ==========================================
# THREAD-SAFE STORAGE ENGINE
# ==========================================
def load_vlogs() -> dict:
    """Load logs database from disk into memory cache."""
    global _vlogs_cache
    if _vlogs_cache:
        return _vlogs_cache
    if os.path.exists(VLOGS_FILE):
        try:
            with open(VLOGS_FILE, "r", encoding="utf-8") as f:
                _vlogs_cache = json.load(f)
        except Exception:
            _vlogs_cache = {}
    else:
        _vlogs_cache = {}
    return _vlogs_cache


def save_vlogs():
    """Flag logs as dirty. Fast in-memory execution; gets saved asynchronously."""
    global _vlogs_dirty
    _vlogs_dirty = True


def _flush_vlogs(force: bool = False):
    """Write logs to disk safely. Called inside non-blocking background thread pools."""
    global _vlogs_dirty
    if not _vlogs_dirty and not force:
        return
    try:
        tmp = VLOGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_vlogs_cache, f, indent=2, ensure_ascii=False)
        os.replace(tmp, VLOGS_FILE)
        _vlogs_dirty = False
    except Exception as e:
        print(f"[VLOG] Error writing vlog.json: {e}")


def log_action(db: dict, user_id: str, entry: dict):
    """Append a log entry asynchronously."""
    entry = dict(entry)
    entry["ts"] = time.time()
    
    vlogs = load_vlogs()
    user_logs = vlogs.setdefault(str(user_id), [])
    user_logs.append(entry)
    
    _purge_user_logs(user_logs)
    save_vlogs()


def _purge_user_logs(logs: list) -> bool:
    """In-place purge of entries older than the retention window."""
    cutoff = time.time() - LOG_RETENTION_SECS
    before = len(logs)
    logs[:] = [e for e in logs if e.get("ts", 0) >= cutoff]
    return len(logs) != before


def _purge_all_vlogs() -> bool:
    """Purge old entries across all users."""
    changed = False
    vlogs = load_vlogs()
    for uid in list(vlogs.keys()):
        if _purge_user_logs(vlogs[uid]):
            changed = True
        if not vlogs[uid]:
            del vlogs[uid]
            changed = True
    return changed


async def vlog_cleanup_loop():
    """Background task to hourly purge expired records."""
    while True:
        try:
            if _purge_all_vlogs():
                save_vlogs()
        except Exception as e:
            print(f"[VLOG] Background cleanup error: {e}")
        await asyncio.sleep(CLEANUP_INTERVAL_SECS)


# ==========================================
# REPORT BUILDER
# ==========================================
def _format_entry(entry: dict, idx: int) -> str:
    ts    = entry.get("ts", 0)
    dt    = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    etype = entry.get("type")
    label = _TYPE_LABELS.get(etype, etype or "UNKNOWN")

    lines = [f"[{idx}] {label}", f"Time         : {dt}"]

    if etype in ("sgive_sent", "sgive_received"):
        lines.append(f"Amount       : {entry.get('amount', 0):,} Shards")
        lines.append(f"Counterparty : {entry.get('cp_name', 'Unknown')} (ID: {entry.get('cp_id', '?')})")
    elif etype in ("gift_sent", "gift_received", "trade_sent"):
        lines.append(f"Card         : {entry.get('card_name', 'Unknown')}")
        lines.append(f"Rarity       : {entry.get('rarity', 'Unknown')}")
        lines.append(f"Counterparty : {entry.get('cp_name', 'Unknown')} (ID: {entry.get('cp_id', '?')})")
    elif etype == "burn":
        lines.append(f"Card         : {entry.get('card_name', 'Unknown')}")
        lines.append(f"Rarity       : {entry.get('rarity', 'Unknown')}")
        lines.append(f"Shards Earned: +{entry.get('shards_earned', 0):,}")
    elif etype == "store_buy_online":
        lines.append(f"Card         : {entry.get('card_name', 'Unknown')}")
        lines.append(f"Rarity       : {entry.get('rarity', 'Unknown')}")
        lines.append(f"Price Paid   : -{entry.get('price', 0):,} Shards")
    elif etype == "store_buy_offline":
        lines.append(f"Card         : {entry.get('card_name', 'Unknown')}")
        lines.append(f"Rarity       : {entry.get('rarity', 'Unknown')}")
        lines.append(f"Price Paid   : -{entry.get('price', 0):,} Shards")
        lines.append(f"Seller       : {entry.get('cp_name', 'Unknown')} (ID: {entry.get('cp_id', '?')})")
    elif etype == "store_sell_offline":
        lines.append(f"Card         : {entry.get('card_name', 'Unknown')}")
        lines.append(f"Rarity       : {entry.get('rarity', 'Unknown')}")
        lines.append(f"Price Earned : +{entry.get('price', 0):,} Shards")
        lines.append(f"Buyer        : {entry.get('cp_name', 'Unknown')} (ID: {entry.get('cp_id', '?')})")

    lines.append(f"Chat         : {entry.get('chat_title', 'Unknown')} (ID: {entry.get('chat_id', '?')})")
    lines.append("-" * 44)
    return "\n".join(lines)


def _build_report(target_id: str, target_name: str, logs: list) -> str:
    logs_sorted = sorted(logs, key=lambda e: e.get("ts", 0))
    
    separator = "=" * 44
    header = (
        "NEXUS VAULT ACTIVITY LOG\n"
        f"{separator}\n"
        f"User       : {target_name} (ID: {target_id})\n"
        f"Generated  : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Window     : Last 7 days (older entries auto-deleted)\n"
        f"Entries    : {len(logs_sorted)}\n"
        f"{separator}\n\n"
    )
    body = "\n".join(_format_entry(e, i) for i, e in enumerate(logs_sorted, start=1))
    return header + body


# ==========================================
# /vlog — ADMIN ONLY
# ==========================================
@main_router.message(Command("vlog"))
async def vlog_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return

    # ── Resolve target ────────────────────────────────────────────────────────
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id   = str(message.reply_to_message.from_user.id)
        target_name = message.reply_to_message.from_user.first_name
    elif command.args:
        target_id, target_name = await resolve_target(command.args.strip(), message)
        if not target_id:
            await message.reply(
                f"⚠️ Could not resolve target <code>{command.args.strip()}</code>.",
                parse_mode=ParseMode.HTML
            )
            return
        target_id = str(target_id)
    else:
        await message.reply(
            "<b>「 📄 VAULT LOG 」</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "Reply to a user, or:\n"
            "<code>/vlog &lt;@username | user_id&gt;</code>\n\n"
            "Returns a full <code>.logs</code> log of that user's\n"
            "<b>/sgive</b>, <b>/gift</b>, <b>/trade</b>, <b>/burn</b> and\n"
            "<b>store</b> (online + offline) activity from the last 7 days.",
            parse_mode=ParseMode.HTML
        )
        return

    # Prune expired records across the logs database
    if _purge_all_vlogs():
        save_vlogs()

    vlogs = load_vlogs()
    logs = vlogs.get(target_id, [])
    if not logs:
        await message.reply(
            f"No /sgive, /gift, /trade, /burn or store activity logged for <b>{target_name}</b> (<code>{target_id}</code>) in the last 7 days.",
            parse_mode=ParseMode.HTML
        )
        return

    report = _build_report(target_id, target_name, logs)
    file = BufferedInputFile(report.encode("utf-8"), filename=f"{target_id}.logs")
    
    admin_id = message.from_user.id
    caption_text = f"📄 <b>Vault log</b> for <b>{target_name}</b> (<code>{target_id}</code>) — {len(logs)} entries, last 7 days."

    # ── Delivery Execution ───────────────────────────────────────────────────
    if message.chat.type == ChatType.PRIVATE:
        await message.reply_document(
            document=file,
            caption=caption_text,
            parse_mode=ParseMode.HTML
        )
    else:
        try:
            await bot.send_document(
                chat_id=admin_id,
                document=file,
                caption=caption_text,
                parse_mode=ParseMode.HTML
            )
            
            bot_info = await bot.get_me()
            bot_username = bot_info.username
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="View 📁", url=f"https://t.me/{bot_username}")]
            ])
            
            target_mention = get_mention(target_id, target_name)
            await message.reply(
                f"✨ {target_mention} logs Sent to Your DM !",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            await message.reply(
                "⚠️ <b>Could not send logs to your DM!</b>\n"
                "Please verify that you have initiated a private chat with the bot first.",
                parse_mode=ParseMode.HTML
            )