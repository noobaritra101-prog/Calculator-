import os
import sys
import json
import re
import time
import uuid
import asyncio
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message, FSInputFile
from aiogram.enums import ParseMode

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN          = "7658617809:AAGEYNtWaLh-859dyn4pLcd_7Rdw3mLtWeM"
ADMIN_IDS          = [5716292610, 5822885863, 5848489095, 7930421561]
SUPREME_OWNER_ID   = 5716292610
DB_GROUP_ID        = -1003799799158 # Used for uploading new cards
DATABASE_BACKUP_ID = -1002790195961 # Used for database backups

MAIN_GROUP_USERNAME = "@animex_nexus"
MAIN_GROUP_LINK     = "https://t.me/animex_nexus"
OFFLINE_STORE_GROUP = -1003982098657  # 🏪 Peer-to-Peer Consignment Group/Channel ID

# Fixed Shards Card Purchase Prices for Online Shop - Balanced Values
SHOP_PRICES = {
    "Basic 🃏": 500,
    "Elite ⚓": 1500,
    "Divine ❄️": 8000
}

# ==========================================
# STOCK MARKET CONFIGURATION
# ==========================================
MARKET_UPDATE_INTERVAL = 300  # 5 min (in seconds)
MARKET_FEE_PCT = 0.015         # 1.5% Platform Fee
DAILY_STOCK_BUY_LIMIT = 50     # Maximum stock shares a user can buy per day

STOCKS = {
    "CAPS": {"name": "Capsule Corp", "volatility": 0.05, "base_price": 100},
    "SPW": {"name": "Speedwagon Foundation", "volatility": 0.05, "base_price": 120},
    "HNT": {"name": "Hunter Association", "volatility": 0.08, "base_price": 90},
    "SHN": {"name": "Shinra Electric", "volatility": 0.10, "base_price": 150},
    "NRV": {"name": "Nerv HQ", "volatility": 0.12, "base_price": 110},
    "UAH": {"name": "U.A. Hero Agency", "volatility": 0.10, "base_price": 130},
    "TJO": {"name": "Tojo Clan", "volatility": 0.15, "base_price": 80},
    "AGR": {"name": "Aogiri Tree", "volatility": 0.25, "base_price": 60},
    "TRK": {"name": "Team Rocket", "volatility": 0.30, "base_price": 50},
    "NEX": {"name": "Nexus Index", "volatility": 0.40, "base_price": 200}
}

# Pagination settings
DECK_PER_PAGE      = 10
CARDS_PER_PAGE     = 10
BROWSE_PER_PAGE    = 10

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
main_router = Router()

DB_FILE = "database.json"

# ── In-memory DB cache ───────────────────────────────────────────────────────
_db_cache        = None
_db_dirty        = False
DB_SAVE_INTERVAL = 5

# ── In-memory state (Mutable, preserve references) ───────────────────────────
group_counters = {}
active_drops   = {} # Maps chat_id -> dict {"card_id": str, "time": float, "message_id": int}
bot_start_time = time.time()
total_messages = 0

spam_tracker = {}
shadow_banned = {}
ghost_banned  = set()
# uid -> {"reason": str|None, "expires_at": float|None, "banned_by": int, "banned_at": float}
# expires_at of None means permanent.
gban_meta = {}

spoiler_cache = {}
group_member_cache = {}
MEMBER_CACHE_TTL   = 3600

SPAM_WINDOW           = 10
SPAM_THRESHOLD        = 10
SHADOW_BAN_DUR        = 600
AUTOLEAVE_MIN_MEMBERS = 40
autoleave_enabled     = True

RARITIES     = ["Divine ❄️", "Elite ⚓", "Basic 🃏"]
RARITY_ORDER = {"Divine ❄️": 0, "Elite ⚓": 1, "Basic 🃏": 2}
RARITY_SAFE  = {"Divine ❄️": "divine", "Elite ⚓": "elite", "Basic 🃏": "basic"}
SAFE_RARITY  = {v: k for k, v in RARITY_SAFE.items()}

def format_rarity(r: str) -> str:
    r_lower = r.lower()
    if "divine" in r_lower: return "Divine ❄️"
    if "elite" in r_lower: return "Elite ⚓"
    if "basic" in r_lower: return "Basic 🃏"
    return r

def get_shop_rotation_seed() -> str:
    """Generates a stable date-string key to sync item pools globally."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ==========================================
# DATABASE HELPERS
# ==========================================
def load_db() -> dict:
    global _db_cache
    if _db_cache is not None: return _db_cache
    if not os.path.exists(DB_FILE):
        _db_cache = {"users": {}, "global_cards": {}, "groups": {}, "settings": {}, "offline_store": {}, "market": {}, "promos": {}}
        return _db_cache
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            _db_cache = json.load(f)
        except json.JSONDecodeError:
            _db_cache = {"users": {}, "global_cards": {}, "groups": {}, "settings": {}, "offline_store": {}, "market": {}, "promos": {}}
    if "settings" not in _db_cache: _db_cache["settings"] = {}
    if "offline_store" not in _db_cache: _db_cache["offline_store"] = {}
    if "promos" not in _db_cache: _db_cache["promos"] = {}
    
    # Initialize Market DB if missing
    if "market" not in _db_cache or not _db_cache["market"]: 
        _db_cache["market"] = {}
        for sym, data in STOCKS.items():
            _db_cache["market"][sym] = {"current_price": data["base_price"], "history": [data["base_price"]] * 24}
            
    return _db_cache

def save_db(data: dict = None):
    global _db_cache, _db_dirty
    if data is not None: _db_cache = data
    _db_dirty = True

def _flush_db(force: bool = False):
    global _db_dirty
    if (not _db_dirty and not force) or _db_cache is None: return
    try:
        tmp = DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_db_cache, f, indent=2, ensure_ascii=False)
        os.replace(tmp, DB_FILE)
        _db_dirty = False
    except Exception as e: print(f"[DB] Save error: {e}")

async def periodic_save():
    while True:
        await asyncio.sleep(DB_SAVE_INTERVAL)
        if _db_dirty: await asyncio.to_thread(_flush_db)

async def perform_backup():
    # periodic_save() already offloads this to a thread — perform_backup()
    # was calling it directly on the event loop instead, meaning every
    # backup (every 20 min, plus any manual /backup) froze the ENTIRE bot
    # — every command, every group message, every callback — for however
    # long json.dump took to serialize the whole database. That's a real
    # source of periodic slowness/lag spikes as the DB grows.
    await asyncio.to_thread(_flush_db, force=True)
    try:
        chat = await bot.get_chat(DATABASE_BACKUP_ID)
        if chat.pinned_message:
            await bot.delete_message(DATABASE_BACKUP_ID, chat.pinned_message.message_id)
        
        doc = FSInputFile(DB_FILE, filename=f"database_{int(time.time())}.json")
        msg = await bot.send_document(
            DATABASE_BACKUP_ID, 
            document=doc, 
            caption=f"📦 Automated DB Backup\n📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        await bot.pin_chat_message(DATABASE_BACKUP_ID, msg.message_id, disable_notification=True)
        print("[BACKUP] Successfully backed up and pinned to backup group.")
    except Exception as e:
        print(f"[BACKUP] Task failed: {e}")

async def backup_to_group():
    while True:
        await asyncio.sleep(20 * 60) # 20 minutes
        await perform_backup()

async def load_from_group():
    global _db_cache

    # Local database.json is flushed every DB_SAVE_INTERVAL (5s) — it's
    # almost always MORE current than the group backup, which only runs
    # every 20 minutes. Restoring unconditionally on every restart was
    # rolling back up to 20 minutes of real progress every single time
    # the bot restarted. Only pull from the group backup for genuine
    # disaster recovery — i.e. the local file is missing or empty.
    if os.path.exists(DB_FILE) and os.path.getsize(DB_FILE) > 0:
        print("✅ Local database found and non-empty — skipping group restore.")
        return

    print("🔄 Local database missing/empty — checking pinned backup in group...")
    try:
        chat = await bot.get_chat(DATABASE_BACKUP_ID)
        if chat.pinned_message and chat.pinned_message.document:
            doc = chat.pinned_message.document
            if doc.file_name and doc.file_name.endswith(".json"):
                file_info = await bot.get_file(doc.file_id)
                await bot.download_file(file_info.file_path, destination=DB_FILE)
                # Force the next load_db() call to re-read from disk instead
                # of silently keeping whatever (possibly empty) cache was
                # already in memory.
                _db_cache = None
                print("✅ Successfully restored database from pinned message.")
            else:
                print("⚠️ Pinned message is not a JSON file.")
        else:
            print("⚠️ No pinned document found in the backup group.")
    except Exception as e:
        print(f"❌ Failed to restore DB from group: {e}")


async def init_db_on_startup():
    """Single entrypoint to call once, before anything else touches the DB.
    Restores from the pinned backup (if any) and then loads it into memory.
    Call this in main.py before dp.start_polling(...) / anything else that
    might call load_db() or ensure_user()."""
    await load_from_group()
    load_db()

def ensure_user(user_id, name, username=None) -> dict:
    db = load_db()
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "name": name, 
            "username": username, 
            "cards": {}, 
            "total_claimed": 0, 
            "joined": int(time.time()), 
            "sort_pref": "default",
            "nexus_shards": 0,
            "last_daily": 0,
            "last_weekly": 0,
            "roll_count": 0,
            "roll_reset": 0,
            "throw_count": 0,
            "throw_reset": 0,
            "stocks": {},
            "daily_purchases": {
                "date": "",
                "bought": [],
                "free_refreshes_used": 0,
                "paid_refreshes_used": 0,
                "refresh_seed_offset": 0
            },
            "daily_stock_bought": {
                "date": "",
                "amount": 0
            },
            "referred_by": None,
            "referrals": [],
            "referral_rewarded": False,
            "last_mine": 0,
            "last_earn": 0,
            "default_versus_mode": "Mix"
        }
        save_db()
    else:
        updated = False
        if db["users"][uid].get("name") != name:
            db["users"][uid]["name"] = name
            updated = True
        if db["users"][uid].get("username") != username:
            db["users"][uid]["username"] = username
            updated = True
        if "sort_pref" not in db["users"][uid]:
            db["users"][uid]["sort_pref"] = "default"
            updated = True
        if "nexus_shards" not in db["users"][uid]:
            db["users"][uid]["nexus_shards"] = 0
            updated = True
        if "last_daily" not in db["users"][uid]:
            db["users"][uid]["last_daily"] = 0
            updated = True
        if "last_weekly" not in db["users"][uid]:
            db["users"][uid]["last_weekly"] = 0
            updated = True
        if "roll_count" not in db["users"][uid]:
            db["users"][uid]["roll_count"] = 0
            updated = True
        if "roll_reset" not in db["users"][uid]:
            db["users"][uid]["roll_reset"] = 0
            updated = True
        if "throw_count" not in db["users"][uid]:
            db["users"][uid]["throw_count"] = 0
            updated = True
        if "throw_reset" not in db["users"][uid]:
            db["users"][uid]["throw_reset"] = 0
            updated = True
        if "stocks" not in db["users"][uid]:
            db["users"][uid]["stocks"] = {}
            updated = True
        if "last_earn" not in db["users"][uid]:
            db["users"][uid]["last_earn"] = 0
            updated = True
            
        # Hardened safety sweep of Online Store reroll keys
        daily_purchases = db["users"][uid].setdefault("daily_purchases", {})
        if not isinstance(daily_purchases, dict):
            db["users"][uid]["daily_purchases"] = {
                "date": "",
                "bought": [],
                "free_refreshes_used": 0,
                "paid_refreshes_used": 0,
                "refresh_seed_offset": 0
            }
            updated = True
        else:
            if "date" not in daily_purchases: daily_purchases["date"] = ""; updated = True
            if "bought" not in daily_purchases: daily_purchases["bought"] = []; updated = True
            if "free_refreshes_used" not in daily_purchases: daily_purchases["free_refreshes_used"] = 0; updated = True
            if "paid_refreshes_used" not in daily_purchases: daily_purchases["paid_refreshes_used"] = 0; updated = True
            if "refresh_seed_offset" not in daily_purchases: daily_purchases["refresh_seed_offset"] = 0; updated = True

        # Safety sweep of daily stock buy limit keys
        daily_stock_bought = db["users"][uid].setdefault("daily_stock_bought", {})
        if not isinstance(daily_stock_bought, dict):
            db["users"][uid]["daily_stock_bought"] = {
                "date": "",
                "amount": 0
            }
            updated = True
        else:
            if "date" not in daily_stock_bought: daily_stock_bought["date"] = ""; updated = True
            if "amount" not in daily_stock_bought: daily_stock_bought["amount"] = 0; updated = True
            
        if "referred_by" not in db["users"][uid]:
            db["users"][uid]["referred_by"] = None
            updated = True
        if "referrals" not in db["users"][uid]:
            db["users"][uid]["referrals"] = []
            updated = True
        if "referral_rewarded" not in db["users"][uid]:
            db["users"][uid]["referral_rewarded"] = False
            updated = True
        if "last_mine" not in db["users"][uid]:
            db["users"][uid]["last_mine"] = 0
            updated = True
        if "default_versus_mode" not in db["users"][uid]:
            db["users"][uid]["default_versus_mode"] = "Mix"
            updated = True
        if updated: save_db()
    return db

def ensure_group(chat_id, chat_title):
    db  = load_db()
    cid = str(chat_id)
    if cid not in db["groups"]:
        db["groups"][cid] = {
            "title": chat_title, 
            "joined": int(time.time()), 
            "drops": 0, 
            "claims": 0,
            "spawn_min": 100,
            "spawn_max": 110
        }
        save_db()
    return db

def load_settings():
    global autoleave_enabled
    db = load_db()
    autoleave_enabled = db["settings"].get("autoleave", True)
    
    ghost_banned.clear()
    for uid in db["settings"].get("ghost_banned", []):
        ghost_banned.add(int(uid))

    gban_meta.clear()
    for k, v in db["settings"].get("gban_meta", {}).items():
        gban_meta[int(k)] = v
        
    shadow_banned.clear()
    now = time.time()
    raw_shadows = db["settings"].get("shadow_banned", {})
    for k, v in list(raw_shadows.items()):
        if v > now:
            shadow_banned[int(k)] = v

def get_mention(user_id, name):
    safe = str(name).replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'

# ==========================================
# GHOST & SHADOW BAN PROTECTION HELPERS
# ==========================================
def is_ghost_banned(uid: int) -> bool:
    if uid in ADMIN_IDS: return False
    if uid not in ghost_banned: return False

    meta = gban_meta.get(uid)
    if meta and meta.get("expires_at") and time.time() > meta["expires_at"]:
        ghost_banned.discard(uid)
        gban_meta.pop(uid, None)
        db = load_db()
        db["settings"]["ghost_banned"] = list(ghost_banned)
        db["settings"]["gban_meta"] = {str(k): v for k, v in gban_meta.items()}
        save_db()
        return False
    return True


_GBAN_DURATION_RE = re.compile(r'^(\d+)(w|d|h|m)$')
_GBAN_DURATION_MULTIPLIERS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}

def parse_gban_duration_token(token: str):
    """Parses a /gban duration token. Returns:
    - "permanent" if the token means no expiry (permanent/perm/forever)
    - int seconds if it's a valid duration like "30d", "7h", "45m", "2w"
    - None if the token isn't a recognized duration at all (treat as part of the reason instead)"""
    t = token.strip().lower()
    if t in ("permanent", "perm", "forever"):
        return "permanent"
    m = _GBAN_DURATION_RE.match(t)
    if not m:
        return None
    amount, unit = int(m.group(1)), m.group(2)
    return amount * _GBAN_DURATION_MULTIPLIERS[unit]


def format_duration_seconds(seconds: int) -> str:
    """Human-readable duration for display, e.g. 93600 -> '1d 2h'."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes and not days: parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "less than a minute"

def is_shadow_banned(uid: int) -> bool:
    if uid in ADMIN_IDS: return False
    if uid not in shadow_banned: return False
    if time.time() > shadow_banned[uid]:
        shadow_banned.pop(uid, None)
        db = load_db()
        db["settings"]["shadow_banned"] = shadow_banned
        save_db()
        return False
    return True

def check_spam(uid: int) -> bool:
    if uid in ADMIN_IDS: return False
    now = time.time()
    spam_tracker.setdefault(uid, [])
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < SPAM_WINDOW]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= SPAM_THRESHOLD:
        spam_tracker[uid] = []
        shadow_banned[uid] = now + SHADOW_BAN_DUR
        db = load_db()
        db["settings"]["shadow_banned"] = shadow_banned
        save_db()
        return True
    return False

async def check_autoleave(chat_id: int) -> bool:
    if chat_id in [DB_GROUP_ID, DATABASE_BACKUP_ID]: 
        return False
        
    if not autoleave_enabled: return False
    now = time.time()
    cached_count, last_checked = group_member_cache.get(chat_id, (None, 0))
    if cached_count is not None and (now - last_checked) < MEMBER_CACHE_TTL:
        count = cached_count
    else:
        try:
            count = await bot.get_chat_member_count(chat_id)
            group_member_cache[chat_id] = (count, now)
        except Exception:
            return False

    if count is not None and count < AUTOLEAVE_MIN_MEMBERS:
        try:
            await bot.send_message(
                chat_id,
                "<b>「 ANIME NEXUS ぁ 」</b>\n"
                "⚠️ This group has fewer than <b>40 members</b>.\n"
                "I'm leaving now — さようなら 👋",
                parse_mode=ParseMode.HTML
            )
            await bot.leave_chat(chat_id)
            return True
        except Exception: pass
    return False

# ==========================================
# TARGET RESOLVER FOR ADMIN COMMANDS
# ==========================================
async def resolve_target(args: str, message: Message) -> tuple[int, str]:
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, u.first_name
    if not args:
        return None, None
    target = args.strip()
    if target.isdigit():
        uid = int(target)
        db = load_db()
        name = db["users"].get(str(uid), {}).get("name", "User")
        return uid, name
    clean_target = target.lstrip("@").lower()
    db = load_db()
    for uid, udata in db["users"].items():
        if udata.get("username") and udata["username"].lower() == clean_target:
            return int(uid), udata["name"]
    return None, None
