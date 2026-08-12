"""
webapp_api.py
=============
HTTP API that powers the Nexus Card Mini App (profile / deck / leaderboard / burn).

WHY THIS FILE EXISTS
---------------------
card_aio.py / handlers.py only ever talk to Telegram — there was no HTTP surface
a website could call. This module adds one, reading and writing the *same*
db the bot already uses (config.load_db() / save_db()), so the Mini App and
the bot commands (/deck, /profile, /leaderboard, /burn) stay in sync
automatically — no second database, no sync job.

AUTH MODEL
----------
Reuses aviator.py's own `get_authed_user` / `verify_init_data` — same HMAC
check against BOT_TOKEN that /aviator/bet and /aviator/cashout already use,
so there's exactly one place that logic lives. Each route checks auth itself
(no global middleware) — that matters because aviator.py's own routes like
/health and /aviator/state are intentionally public, and a blanket
auth-everything middleware on the shared app would have broken them.

MOUNTING THIS ON YOUR EXISTING SERVER
--------------------------------------
Railway exposes exactly one public port, and aviator.py's build_app() already
binds it. In aviator.py, inside build_app(), right after the routes it
already registers, add:

    from webapp_api import setup_webapp_routes
    setup_webapp_routes(app)

That's it — see the bottom of this file for the exact diff.
"""

import logging
import time
import difflib

from aiohttp import web, ClientSession

from config import load_db, save_db, ensure_user, format_rarity, is_ghost_banned, is_shadow_banned, bot
from aviator import get_authed_user, _cors_headers

logger = logging.getLogger("AnimeNexus.webapp_api")

BURN_PAYOUTS = {"Basic 🃏": 150, "Elite ⚓": 450, "Divine ❄️": 1800}
LEADERBOARD_SIZE = 10
DECK_PAGE_SIZE = 24  # cards per page — the web grid isn't capped by Telegram's caption length like the bot is
ACTION_COOLDOWN_SECS = 3

_action_cooldowns: dict[str, float] = {}


def _cooldown_hit(key: str) -> bool:
    now = time.time()
    last = _action_cooldowns.get(key, 0.0)
    if now - last < ACTION_COOLDOWN_SECS:
        return True
    _action_cooldowns[key] = now
    return False


def _authed_or_401(request: web.Request):
    """Returns the Telegram user dict, or None (caller should 401).
    Also enforces ghost/shadow ban, same as every bot command does."""
    user = get_authed_user(request)
    if not user:
        return None
    if is_ghost_banned(user["id"]) or is_shadow_banned(user["id"]):
        return None
    return user


# ==========================================
# SHARED HELPERS (mirror handlers.py so /api and the bot commands
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
    user = _authed_or_401(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401, headers=_cors_headers())

    user_id = str(user["id"])
    db = ensure_user(user_id, user.get("first_name"), user.get("username"))
    user_data = db["users"][user_id]
    cards = user_data.get("cards", {})
    counts = _rarity_counts(cards)

    return web.json_response({
        "user_id": user_id,
        "name": user_data.get("name", "User"),
        "username": user.get("username"),
        "shards": user_data.get("nexus_shards", 0),
        "total_cards": sum(counts.values()),
        "unique_cards": len(cards),
        "rarity_counts": {"divine": counts["Divine ❄️"], "elite": counts["Elite ⚓"], "basic": counts["Basic 🃏"]},
        "rank": _global_rank(db, user_id),
        "joined": user_data.get("joined"),
        "sort_pref": user_data.get("sort_pref", "default"),
    }, headers=_cors_headers())


async def get_deck(request: web.Request):
    user = _authed_or_401(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401, headers=_cors_headers())

    user_id = str(user["id"])
    sort_mode = request.query.get("sort", "default")
    page = int(request.query.get("page", 0))

    db = ensure_user(user_id, user.get("first_name"), user.get("username"))
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
    }, headers=_cors_headers())


async def set_deck_sort(request: web.Request):
    user = _authed_or_401(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401, headers=_cors_headers())

    user_id = str(user["id"])
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_body"}, status=400, headers=_cors_headers())

    mode = body.get("mode", "default")
    if mode not in ("default", "rarity", "name", "amount"):
        return web.json_response({"error": "invalid_mode"}, status=400, headers=_cors_headers())

    db = load_db()
    db.setdefault("users", {}).setdefault(user_id, {})["sort_pref"] = mode
    save_db()
    return web.json_response({"ok": True, "sort_pref": mode}, headers=_cors_headers())


async def get_leaderboard(request: web.Request):
    user = _authed_or_401(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401, headers=_cors_headers())

    user_id = str(user["id"])
    db = load_db()
    ranked = sorted(db["users"].items(), key=lambda x: len(x[1].get("cards", {})), reverse=True)

    entries = [
        {"rank": i + 1, "name": ud.get("name", "Unknown"), "card_count": len(ud.get("cards", {})), "is_you": uid == user_id}
        for i, (uid, ud) in enumerate(ranked[:LEADERBOARD_SIZE])
    ]
    your_rank = next((i + 1 for i, (uid, _) in enumerate(ranked) if uid == user_id), None)

    return web.json_response(
        {"entries": entries, "your_rank": your_rank, "total_players": len(ranked)},
        headers=_cors_headers()
    )


async def burn_search(request: web.Request):
    """Mirrors burn_cmd's fuzzy match — lets the frontend show a live preview
    (card + payout) as the user types, before they commit to burning it."""
    user = _authed_or_401(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401, headers=_cors_headers())

    user_id = str(user["id"])
    query = request.query.get("q", "").lower().strip()
    if not query:
        return web.json_response({"match": None}, headers=_cors_headers())

    db = ensure_user(user_id, user.get("first_name"), user.get("username"))
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
        return web.json_response({"match": None}, headers=_cors_headers())

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
    }, headers=_cors_headers())


async def burn_confirm(request: web.Request):
    user = _authed_or_401(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=401, headers=_cors_headers())

    user_id = str(user["id"])
    if _cooldown_hit(f"burn_{user_id}"):
        return web.json_response({"error": "cooldown"}, status=429, headers=_cors_headers())

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_body"}, status=400, headers=_cors_headers())

    card_id = body.get("card_id")
    if not card_id:
        return web.json_response({"error": "missing_card_id"}, status=400, headers=_cors_headers())

    db = load_db()
    my_cards = db["users"].get(user_id, {}).get("cards", {})
    if card_id not in my_cards or my_cards[card_id]["amount"] <= 0:
        return web.json_response({"error": "not_owned"}, status=404, headers=_cors_headers())

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
    }, headers=_cors_headers())


async def card_image_proxy(request: web.Request):
    """Telegram file_ids aren't public URLs — <img src> can't use them
    directly. Resolves the file_id via getFile and streams the bytes through,
    with long caching (card art never changes for a given file_id). Public —
    no initData check, since <img> tags can't attach custom headers."""
    file_id = request.match_info["file_id"]
    try:
        tg_file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{bot.token}/{tg_file.file_path}"
        async with ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    return web.Response(status=404)
                data = await resp.read()
                content_type = resp.content_type or "image/jpeg"
        return web.Response(
            body=data,
            content_type=content_type,
            headers={**_cors_headers(), "Cache-Control": "public, max-age=604800, immutable"},
        )
    except Exception as e:
        logger.warning(f"[card_image_proxy] failed for {file_id}: {e}")
        return web.Response(status=404, headers=_cors_headers())


async def handle_options(request: web.Request) -> web.Response:
    return web.Response(status=204, headers=_cors_headers())


# ==========================================
# WIRING
# ==========================================
API_ROUTES = [
    ("GET", "/api/profile", get_profile),
    ("GET", "/api/deck", get_deck),
    ("POST", "/api/deck/sort", set_deck_sort),
    ("GET", "/api/leaderboard", get_leaderboard),
    ("GET", "/api/burn/search", burn_search),
    ("POST", "/api/burn/confirm", burn_confirm),
]


def setup_webapp_routes(app: web.Application):
    """Call this on the SAME app aviator.py's build_app() already builds —
    see the diff at the bottom of this file."""
    for method, path, handler in API_ROUTES:
        app.router.add_route(method, path, handler)
        app.router.add_route("OPTIONS", path, handle_options)

    app.router.add_get("/api/card-image/{file_id}", card_image_proxy)
    app.router.add_route("OPTIONS", "/api/card-image/{file_id}", handle_options)
    return app


# ==========================================
# THE ONLY CHANGE NEEDED IN aviator.py
# ==========================================
# def build_app() -> web.Application:
#     app = web.Application()
#
#     app.router.add_get("/", handle_healthcheck)
#     app.router.add_get("/health", handle_healthcheck)
#     app.router.add_get("/aviator/state", handle_state)
#     app.router.add_post("/aviator/bet", handle_bet)
#     app.router.add_post("/aviator/cashout", handle_cashout)
#     app.router.add_get("/aviator/balance", handle_balance)
#     app.router.add_get("/aviator/weblog", handle_weblog)
#
#     for path in ["/", "/health", "/aviator/state", "/aviator/bet", "/aviator/cashout", "/aviator/balance", "/aviator/weblog"]:
#         app.router.add_route("OPTIONS", path, handle_options)
#
#     from webapp_api import setup_webapp_routes      # <-- ADD THIS LINE
#     setup_webapp_routes(app)                         # <-- ADD THIS LINE
#
#     return app
