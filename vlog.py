import os
import json
import time
import asyncio
from datetime import datetime, timezone

from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode

import config
from config import bot, main_router, ADMIN_IDS, resolve_target

# ==========================================
# CONFIGURATION
# ==========================================
LOG_RETENTION_SECS = 7 * 24 * 3600     # Logs older than 7 days are auto-purged
CLEANUP_INTERVAL_SECS = 3600           # Background purge runs every hour
VLOG_DIR = "vlogs"                     # Directory containing individual .logs files

_TYPE_LABELS = {
    "sgive_sent":     "💠 SHARD GIFT — SENT",
    "sgive_received": "💠 SHARD GIFT — RECEIVED",
    "gift_sent":      "🎁 CARD GIFT — SENT",
    "gift_received":  "🎁 CARD GIFT — RECEIVED",
    "burn":           "🔥 CARD BURNED",
}


# ==========================================
# DISK-BASED PATH ROUTING
# ==========================================
def _get_log_filepath(user_id: str) -> str:
    """Ensures log directory exists and returns absolute path for target user's .logs file."""
    os.makedirs(VLOG_DIR, exist_ok=True)
    return os.path.join(VLOG_DIR, f"{user_id}.logs")


# ==========================================
# CORE LOGGING API
# ==========================================
def log_action(db: dict, user_id: str, entry: dict):
    """
    Append a detailed log entry for a user to their dedicated physical .logs file.
    The 'db' parameter is kept for backward-compatibility with your existing handlers.
    """
    entry = dict(entry)
    entry["ts"] = time.time()
    
    filepath = _get_log_filepath(user_id)
    logs = []
    
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
            
    logs.append(entry)
    
    # Prune stale records before writing back
    cutoff = time.time() - LOG_RETENTION_SECS
    logs = [e for e in logs if e.get("ts", 0) >= cutoff]
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[VLOG] Failed to write log to {filepath}: {e}")


async def vlog_cleanup_loop():
    """Background task — purges entries older than 7 days from physical files on disk."""
    while True:
        try:
            if os.path.exists(VLOG_DIR):
                cutoff = time.time() - LOG_RETENTION_SECS
                for filename in os.listdir(VLOG_DIR):
                    if filename.endswith(".logs"):
                        filepath = os.path.join(VLOG_DIR, filename)
                        try:
                            with open(filepath, "r", encoding="utf-8") as f:
                                logs = json.load(f)
                        except Exception:
                            continue
                        
                        before = len(logs)
                        logs = [e for e in logs if e.get("ts", 0) >= cutoff]
                        
                        if not logs:
                            try:
                                os.remove(filepath)
                            except Exception:
                                pass
                        elif len(logs) != before:
                            try:
                                with open(filepath, "w", encoding="utf-8") as f:
                                    json.dump(logs, f, indent=2, ensure_ascii=False)
                            except Exception:
                                pass
        except Exception as e:
            print(f"[VLOG] Background cleanup error: {e}")
        await asyncio.sleep(CLEANUP_INTERVAL_SECS)


# ==========================================
# TEXT REPORT BUILDER
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
    elif etype in ("gift_sent", "gift_received"):
        lines.append(f"Card         : {entry.get('card_name', 'Unknown')}")
        lines.append(f"Rarity       : {entry.get('rarity', 'Unknown')}")
        lines.append(f"Counterparty : {entry.get('cp_name', 'Unknown')} (ID: {entry.get('cp_id', '?')})")
    elif etype == "burn":
        lines.append(f"Card         : {entry.get('card_name', 'Unknown')}")
        lines.append(f"Rarity       : {entry.get('rarity', 'Unknown')}")
        lines.append(f"Shards Earned: +{entry.get('shards_earned', 0):,}")

    lines.append(f"Chat         : {entry.get('chat_title', 'Unknown')} (ID: {entry.get('chat_id', '?')})")
    lines.append("-" * 44)
    return "\n".join(lines)


def _build_report(target_id: str, target_name: str, logs: list) -> str:
    logs_sorted = sorted(logs, key=lambda e: e.get("ts", 0))
    header = (
        "NEXUS VAULT ACTIVITY LOG\n"
        "=" * 44 + "\n"
        f"User       : {target_name} (ID: {target_id})\n"
        f"Generated  : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Window     : Last 7 days (older entries auto-deleted)\n"
        f"Entries    : {len(logs_sorted)}\n"
        + "=" * 44 + "\n\n"
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

    # Resolve target user
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
            "Returns a <code>.logs</code> file of that user's\n"
            "<b>/sgive</b>, <b>/gift</b> and <b>/burn</b> activity from the last 7 days.",
            parse_mode=ParseMode.HTML
        )
        return

    # Load logs from disk file
    filepath = _get_log_filepath(target_id)
    logs = []
    
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            pass

    if not logs:
        await message.reply(
            f"No /sgive, /gift or /burn activity logged for <b>{target_name}</b> (<code>{target_id}</code>) in the last 7 days.",
            parse_mode=ParseMode.HTML
        )
        return

    # Build and deliver the report as a '.logs' document attachment
    report = _build_report(target_id, target_name, logs)
    file = BufferedInputFile(report.encode("utf-8"), filename=f"{target_id}.logs")

    await message.reply_document(
        document=file,
        caption=f"📄 <b>Vault log</b> for <b>{target_name}</b> (<code>{target_id}</code>) — {len(logs)} entries, last 7 days.",
        parse_mode=ParseMode.HTML
    )
