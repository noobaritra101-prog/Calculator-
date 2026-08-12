"""
webapp_api.py
=============
HTTP API that powers the Nexus Card Mini App (profile / deck / leaderboard / burn).

WHY THIS FILE EXISTS
---------------------
card_aio.py / handlers.py only ever talk to Telegram — there was no HTTP surface
a website could call. This module adds one, reading and writing the *same*
db that the bot already uses (config.load_db() / save_db()), so the Mini App
and the bot commands (/deck, /profile, /leaderboard, /burn) stay in sync
automatically — no second database, no sync job.

AUTH MODEL
----------
The Mini App is opened inside Telegram, so every request carries Telegram's
signed `initData` string (see https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
We verify that signature with the bot token on every request instead of
issuing our own sessions/cookies — nothing to log in to, nothing to leak.
Send it as a header: `Authorization: tma <initData>`.

MOUNTING THIS ON YOUR EXISTING SERVER (IMPORTANT)
--------------------------------------------------
Railway exposes exactly ONE public port. aviator.py already starts an aiohttp
server on that port (start_aviator_server). Running a second standalone
server here would try to bind a port Railway never forwards traffic to, and
silently be unreachable from calculator-production-75bf.up.railway.app.

So: don't call start_webapp_api_server() in production. Instead, inside
aviator.py, right after the aiohttp `web.Application()` is created, add:

    from webapp_api import setup_webapp_routes
    setup_webapp_routes(app)

That mounts everything below at /api/* on the port already exposed publicly.
(start_webapp_api_server() is kept below only for local testing on its own
port, e.g. `python webapp_api.py`.)
"""

import hashlib
import hmac
import io
import json
import logging
import time
from urllib.parse import parse_qsl, unquote

from aiohttp import web, ClientSession

import config
from config import bot, load_db, save_db, ensure_user, format_rarity, is_ghost_banned, is_shadow_banned

logger = logging.getLogger("AnimeNexus.webapp_api")

INIT_DATA_MAX_AGE_SECS = 86400  # reject initData older than 24h (replay protection)
BURN_PAYOUTS = {"Basic 🃏": 150, "Elite ⚓": 450, "Divine ❄️": 1800}
LEADERBOARD_SIZE = 10
DECK_PAGE_SIZE = 24  # cards per page — the web grid isn't limited by Telegram's caption length like the bot is

_action_cooldowns: dict[str, float] = {}
ACTION_COOLDOWN_SECS = 3


# ==========================================
# TELEGRAM initData VALIDATION
# ==========================================
def _validate_init_data(init_data: str) -> dict | None:
    """Verifies the HMAC signature Telegram attaches to Mini App launches.
    Returns the parsed dict (including a 'user' key with a dict, not a raw
    JSON string) on success, or None if the signature is missing/invalid/stale."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot.token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = pairs.get("auth_date")
    if auth_date and (time.time() - int(auth_date)) > INIT_DATA_MAX_AGE_SECS:
        return None

    if "user" in pairs:
        try:
            pairs["user"] = json.loads(unquote(pairs["user"]))
        except Exception:
            return None

    return pairs


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Validates initData on every /api/* call and stashes the Telegram
    user dict on request['tg_user']. Card image proxying is exempt since
    <img> tags can't attach custom headers."""
    if request.path.startswith("/api/card-image/"):
        return await handler(request)

    auth_header = request.headers.get("Authorization", "")
    init_data = auth_header[4:] if auth_header.startswith("tma ") else request.headers.get("X-Telegram-Init-Data", "")

    parsed = _validate_init_data(init_data)
    if not parsed or "user" not in parsed:
        return web.json_response({"error": "invalid_init_data"}, status=401)

    tg_user = parsed["user"]
    uid_int = tg_user.get("id")
    if not uid_int:
        return web.json_response({"error": "invalid_init_data"}, status=401)

    if is_ghost_banned(uid_int) or is_shadow_banned(uid_int):
        return web.json_response({"error": "restricted"}, status=403)

    request["tg_user"] = tg_user
    return await handler(request)


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """The Mini App's static frontend may be hosted on a different origin
    than this API (e.g. GitHub Pages / Vercel pointing at this Railway
    backend) — allow that. Auth is via signed initData, not cookies, so an
    open CORS policy here doesn't expose anything a valid signature
    wouldn't already gate."""
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, X-Telegram-Init-Data, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def _cooldown_hit(key: str) -> bool:
    now = time.time()
    last = _action_cooldowns.get(key, 0.0)
    if now - last < ACTION_COOLDOWN_SECS:
        return True
    _action_cooldowns[key] = now
    return False


# ==========================================
# SHARED HELPERS (mirror handlers.py logic so /api and the bot commands
# never drift apart)
# ==========================================
def _rarity_counts(cards: dict) -> dict:
    counts = {"Divine ❄️": 0, "Elite ⚓": 0, "Basic 🃏": 0}
    for cdata in cards.values():
        r = format_rarity(cdata.get("rarity", ""))
        if r in counts:
            counts[r] += cdata.get("amount", 0)
    return counts


def _global_rank(db: dict, user_id: str) -> int:
    ranked = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)
    for i, (uid, _) in enumerate(ranked):
        if uid == user_id:
            return i + 1
    return 9999


def _card_image_url(request: web.Request, file_id: str | None) -> str | None:
    if not file_id:
        return None
    return str(request.url.origin()) + f"/api/card-image/{file_id}"


# ==========================================
# ROUTE HANDLERS
# ==========================================
async def get_profile(request: web.Request):
    tg_user = request["tg_user"]
    user_id = str(tg_user["id"])
    db = ensure_user(user_id, tg_user.get("first_name", "User"), tg_user.get("username"))
    user_data = db["users"][user_id]
    cards = user_data.get("cards", {})
    counts = _rarity_counts(cards)

    return web.json_response({
        "user_id": user_id,
        "name": user_data.get("name", "User"),
        "username": tg_user.get("username"),
        "shards": user_data.get("nexus_shards", 0),
        "total_cards": sum(counts.values()),
        "unique_cards": len(cards),
        "rarity_counts": {"divine": counts["Divine ❄️"], "elite": counts["Elite ⚓"], "basic": counts["Basic 🃏"]},
        "rank": _global_rank(db, user_id),
        "joined": user_data.get("joined"),
        "sort_pref": user_data.get("sort_pref", "default"),
    })


async def get_deck(request: web.Request):
    tg_user = request["tg_user"]
    user_id = str(tg_user["id"])
    sort_mode = request.query.get("sort", "default")
    page = int(request.query.get("page", 0))

    db = ensure_user(user_id, tg_user.get("first_name", "User"), tg_user.get("username"))
    user_data = db["users"][user_id]
    cards = user_data.get("cards", {})
    global_cards = db.get("global_cards", {})
    special_id = user_data.get("special_card")

    enriched = []
    for cid, cdata in cards.items():
        anime = global_cards.get(cid, {}).get("anime", "Unknown")
        enriched.append((cid, cdata, anime))

    if sort_mode == "rarity":
        from config import RARITY_ORDER
        enriched.sort(key=lambda x: (x[2], RARITY_ORDER.get(format_rarity(x[1]["rarity"]), 99)))
    elif sort_mode == "name":
        enriched.sort(key=lambda x: (x[2], x[1]["name"].lower()))
    elif sort_mode == "amount":
        enriched.sort(key=lambda x: (x[2], x[1]["amount"]), reverse=True)
    else:
        enriched.sort(key=lambda x: x[2])

    anime_owned, anime_total = {}, {}
    for _, _, a in enriched:
        anime_owned[a] = anime_owned.get(a, 0) + 1
    for cdata in global_cards.values():
        a = cdata.get("anime", "Unknown")
        anime_total[a] = anime_total.get(a, 0) + 1

    total_pages = max(1, (len(enriched) + DECK_PAGE_SIZE - 1) // DECK_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_items = enriched[page * DECK_PAGE_SIZE:(page + 1) * DECK_PAGE_SIZE]

    out_cards = []
    for cid, cdata, anime in page_items:
        rarity = format_rarity(cdata["rarity"])
        out_cards.append({
            "card_id": cid,
            "name": cdata["name"],
            "rarity": rarity,
            "amount": cdata["amount"],
            "anime": anime,
            "anime_progress": f"{anime_owned.get(anime, 0)}/{anime_total.get(anime, 0)}",
            "is_special": cid == special_id,
            "image_url": _card_image_url(request, global_cards.get(cid, {}).get("file_id")),
        })

    return web.json_response({
        "page": page,
        "total_pages": total_pages,
        "total_unique": len(enriched),
        "sort_pref": sort_mode,
        "cards": out_cards,
    })


async def set_deck_sort(request: web.Request):
    tg_user = request["tg_user"]
    user_id = str(tg_user["id"])
    body = await request.json()
    mode = body.get("mode", "default")
    if mode not in ("default", "rarity", "name", "amount"):
        return web.json_response({"error": "invalid_mode"}, status=400)

    db = load_db()
    db.setdefault("users", {}).setdefault(user_id, {})["sort_pref"] = mode
    save_db()
    return web.json_response({"ok": True, "sort_pref": mode})


async def get_leaderboard(request: web.Request):
    tg_user = request["tg_user"]
    user_id = str(tg_user["id"])
    db = load_db()
    ranked = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)

    entries = [
        {"rank": i + 1, "name": ud.get("name", "Unknown"), "card_count": len(ud.get("cards", {})), "is_you": uid == user_id}
        for i, (uid, ud) in enumerate(ranked[:LEADERBOARD_SIZE])
    ]
    your_rank = next((i + 1 for i, (uid, _) in enumerate(ranked) if uid == user_id), None)

    return web.json_response({"entries": entries, "your_rank": your_rank, "total_players": len(ranked)})


async def burn_search(request: web.Request):
    """Mirrors burn_cmd's fuzzy match — lets the frontend show a live preview
    (card + payout) as the user types, before they commit to burning it."""
    import difflib
    tg_user = request["tg_user"]
    user_id = str(tg_user["id"])
    query = request.query.get("q", "").lower().strip()
    if not query:
        return web.json_response({"match": None})

    db = ensure_user(user_id, tg_user.get("first_name", "User"), tg_user.get("username"))
    my_cards = db["users"][user_id].get("cards", {})

    best_match, best_ratio = None, 0.0
    for cid, cdata in my_cards.items():
        if cdata["amount"] <= 0:
            continue
        name_lower = cdata["name"].lower()
        if query == name_lower:
            best_match = (cid, cdata)
            break
        if query in name_lower:
            ratio = 0.8 + (len(query) / len(name_lower)) * 0.1
            if ratio > best_ratio:
                best_ratio, best_match = ratio, (cid, cdata)
        else:
            ratio = difflib.SequenceMatcher(None, query, name_lower).ratio()
            if ratio > 0.6 and ratio > best_ratio:
                best_ratio, best_match = ratio, (cid, cdata)

    if not best_match:
        return web.json_response({"match": None})

    cid, cdata = best_match
    rarity = format_rarity(cdata["rarity"])
    global_data = db["global_cards"].get(cid, {})
    return web.json_response({
        "match": {
            "card_id": cid,
            "name": cdata["name"],
            "rarity": rarity,
            "amount": cdata["amount"],
            "payout": BURN_PAYOUTS.get(rarity, 150),
            "image_url": _card_image_url(request, global_data.get("file_id")),
        }
    })


async def burn_confirm(request: web.Request):
    tg_user = request["tg_user"]
    user_id = str(tg_user["id"])

    if _cooldown_hit(f"burn_{user_id}"):
        return web.json_response({"error": "cooldown"}, status=429)

    body = await request.json()
    card_id = body.get("card_id")
    if not card_id:
        return web.json_response({"error": "missing_card_id"}, status=400)

    db = load_db()
    my_cards = db["users"].get(user_id, {}).get("cards", {})
    if card_id not in my_cards or my_cards[card_id]["amount"] <= 0:
        return web.json_response({"error": "not_owned"}, status=404)

    card_data = my_cards[card_id]
    rarity = format_rarity(card_data["rarity"])
    payout = BURN_PAYOUTS.get(rarity, 150)

    my_cards[card_id]["amount"] -= 1
    if my_cards[card_id]["amount"] <= 0:
        del my_cards[card_id]
        if db["users"][user_id].get("special_card") == card_id:
            db["users"][user_id]["special_card"] = None

    db["users"][user_id]["nexus_shards"] = db["users"][user_id].get("nexus_shards", 0) + payout

    try:
        from vlog import log_action
        log_action(db, user_id, {
            "type": "burn", "card_name": card_data["name"], "rarity": rarity,
            "shards_earned": payout, "chat_id": None, "chat_title": "Web App",
        })
    except Exception as e:
        logger.warning(f"[webapp burn] log_action failed: {e}")

    save_db()

    return web.json_response({
        "ok": True,
        "burned_name": card_data["name"],
        "rarity": rarity,
        "payout": payout,
        "new_shard_balance": db["users"][user_id]["nexus_shards"],
    })


async def card_image_proxy(request: web.Request):
    """Telegram file_ids aren't public URLs — <img src> can't use them
    directly. This resolves the file_id via getFile and streams the bytes
    through, with caching (card art never changes for a given file_id)."""
    file_id = request.match_info["file_id"]
    try:
        tg_file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{bot.token}/{tg_file.file_path}"
        async with ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    return web.Response(status=404)
                data = await resp.read()
        return web.Response(
            body=data,
            content_type=resp.content_type or "image/jpeg",
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )
    except Exception as e:
        logger.warning(f"[card_image_proxy] failed for {file_id}: {e}")
        return web.Response(status=404)


# ==========================================
# WIRING
# ==========================================
def setup_webapp_routes(app: web.Application):
    """Call this on your EXISTING aiohttp app (the one aviator.py already
    binds to Railway's public PORT). See module docstring."""
    app.middlewares.append(cors_middleware)
    app.middlewares.append(auth_middleware)
    app.router.add_get("/api/profile", get_profile)
    app.router.add_get("/api/deck", get_deck)
    app.router.add_post("/api/deck/sort", set_deck_sort)
    app.router.add_get("/api/leaderboard", get_leaderboard)
    app.router.add_get("/api/burn/search", burn_search)
    app.router.add_post("/api/burn/confirm", burn_confirm)
    app.router.add_get("/api/card-image/{file_id}", card_image_proxy)
    app.router.add_route("OPTIONS", "/api/{tail:.*}", lambda r: web.Response())
    return app


async def start_webapp_api_server(port: int = 8081):
    """LOCAL TESTING ONLY — standalone server on its own port. In
    production, use setup_webapp_routes(app) on the aviator app instead
    (Railway only exposes one port)."""
    app = web.Application()
    setup_webapp_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"webapp_api standalone server running on :{port} (local testing only)")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_webapp_api_server())
