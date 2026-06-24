"""
==========================================
AVIATOR — Crash-style betting mini-game
==========================================
This module is a self-contained aiohttp web server that runs ALONGSIDE the
bot's Telegram polling loop, in the SAME process. It exposes a small HTTP
API for a separately-hosted (e.g. Netlify) frontend to call.

Wiring (done in card_aio.py, not here):
    from aviator import start_aviator_server
    asyncio.create_task(start_aviator_server())

All game state lives in the SAME database.json used by the rest of the bot
(via config.load_db()/save_db()), and shards are debited/credited on the
same "nexus_shards" field used everywhere else — so a user's Aviator
balance is just their normal bot balance.

GAME RULES (server-authoritative — the frontend only renders what the
server tells it; it can never decide outcomes):
  1. BETTING PHASE (5s)   — players place bets via POST /aviator/bet
  2. FLYING PHASE         — multiplier climbs from 1.00x; players call
                             POST /aviator/cashout any time to lock in
  3. CRASH                — at a pre-determined point (chosen server-side
                             BEFORE betting opens, never influenced by
                             client/player behavior), the round ends; any
                             player who didn't cash out loses their bet
  4. Repeat.

HOUSE EDGE: 5%, baked into the crash-point distribution (standard
provably-fair-style crash formula). This is a probability skew, not a
post-hoc adjustment — the crash point for a round is fixed the moment the
round is created and does not change based on who bets or cashes out.
"""

import asyncio
import time
import uuid
import random
import hmac
import hashlib
import json
import math
import os
from urllib.parse import parse_qsl

print("[AVIATOR] Module aviator.py is being imported right now.")

from aiohttp import web

print("[AVIATOR] aiohttp imported successfully.")

import config
from config import load_db, save_db, ensure_user, BOT_TOKEN

# ==========================================
# SETTINGS
# ==========================================
BETTING_PHASE_SECONDS = 5
HOUSE_EDGE_PCT = 0.05            # 5% house edge, matches standard Aviator
TICK_INTERVAL = 0.05             # how often the multiplier updates server-side (50ms)
GROWTH_RATE = 0.00006            # controls how fast the multiplier climbs (tuned for ~30s avg flight)
MIN_BET = 10
MAX_BET = 10_000
MAX_CRASH_POINT = 1000.0         # safety ceiling, astronomically rare to hit
ROUND_HISTORY_LIMIT = 50         # how many past round results to keep in memory

# How long a WebApp initData payload remains valid (replay-attack window)
INITDATA_MAX_AGE_SECONDS = 24 * 60 * 60


# ==========================================
# TELEGRAM WEBAPP initData VERIFICATION
# ==========================================
def verify_init_data(init_data: str):
    """
    Verifies a Telegram WebApp initData string per Telegram's official spec:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app

    Returns the parsed user dict if valid, or None if invalid/expired/missing.
    This is the ONLY accepted way to identify a player — there is no
    fallback to a client-supplied user_id, since that would let anyone
    play as anyone else.
    """
    if not init_data:
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    # Build the data-check-string: all remaining fields, sorted, joined by \n
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))

    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = pairs.get("auth_date")
    if not auth_date or not auth_date.isdigit():
        return None
    if time.time() - int(auth_date) > INITDATA_MAX_AGE_SECONDS:
        return None  # stale/replayed payload

    user_raw = pairs.get("user")
    if not user_raw:
        return None
    try:
        user = json.loads(user_raw)
    except Exception:
        return None

    if "id" not in user:
        return None
    return user


def get_authed_user(request: web.Request):
    """Pulls initData from the Authorization header and verifies it."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("tma "):
        return None
    init_data = auth_header[len("tma "):]
    return verify_init_data(init_data)


# ==========================================
# CRASH POINT GENERATION (provably-fair-style)
# ==========================================
def generate_crash_point() -> float:
    """
    Standard crash-game distribution with a built-in house edge. Computed
    fresh for each round at round-creation time, BEFORE betting opens —
    nothing that happens during betting or flying can change it.

    Formula: crash = (1 - house_edge) / (1 - r), r uniform in [0, 1)
    This gives a long-tail distribution: most rounds crash low (1.0x-2.0x),
    rare rounds go very high, and the average return-to-player is exactly
    (1 - house_edge) over many rounds.
    """
    r = random.random()
    r = min(r, 0.999999)  # avoid division by zero / runaway values right at r -> 1
    crash = (1 - HOUSE_EDGE_PCT) / (1 - r)
    return round(min(crash, MAX_CRASH_POINT), 2)


def multiplier_at(elapsed_seconds: float) -> float:
    """Exponential growth curve for the live multiplier display."""
    return round(max(1.00, math.exp(GROWTH_RATE * elapsed_seconds * 1000)), 2)


def seconds_for_multiplier(target_mult: float) -> float:
    """Inverse of multiplier_at — used to know exactly when a round should crash."""
    if target_mult <= 1.0:
        return 0.0
    return math.log(target_mult) / (GROWTH_RATE * 1000)


# ==========================================
# ROUND ENGINE (single shared round, like real Aviator)
# ==========================================
class AviatorRound:
    __slots__ = ("round_id", "phase", "crash_point", "crash_at_time",
                 "betting_started_at", "flying_started_at", "bets", "created_at")

    def __init__(self):
        self.round_id = str(uuid.uuid4())
        self.phase = "betting"  # betting -> flying -> crashed
        self.crash_point = generate_crash_point()
        self.crash_at_time = None  # set once flying starts
        self.betting_started_at = time.time()
        self.flying_started_at = None
        self.created_at = time.time()
        # uid -> {"amount": int, "cashed_out": bool, "cashout_mult": float|None, "payout": int}
        self.bets = {}


class AviatorEngine:
    def __init__(self):
        self.current_round = AviatorRound()
        self.history = []  # list of {"round_id", "crash_point"} most recent first
        self.lock = asyncio.Lock()

    def public_state(self) -> dict:
        r = self.current_round
        live_mult = 1.00
        if r.phase == "flying" and r.flying_started_at:
            elapsed = time.time() - r.flying_started_at
            live_mult = multiplier_at(elapsed)
        elif r.phase == "crashed":
            live_mult = r.crash_point

        betting_time_left = 0.0
        if r.phase == "betting":
            betting_time_left = max(0.0, BETTING_PHASE_SECONDS - (time.time() - r.betting_started_at))

        return {
            "round_id": r.round_id,
            "phase": r.phase,
            "multiplier": live_mult,
            "betting_time_left": round(betting_time_left, 2),
            "crash_point": r.crash_point if r.phase == "crashed" else None,
            "bet_count": len(r.bets),
            "history": self.history[:ROUND_HISTORY_LIMIT],
        }

    async def place_bet(self, uid: str, amount: int) -> dict:
        async with self.lock:
            r = self.current_round
            if r.phase != "betting":
                return {"ok": False, "error": "Betting is closed for this round. Wait for the next one."}
            if uid in r.bets:
                return {"ok": False, "error": "You already placed a bet this round."}
            if amount < MIN_BET or amount > MAX_BET:
                return {"ok": False, "error": f"Bet must be between {MIN_BET} and {MAX_BET} shards."}

            db = ensure_user(uid, None, None)
            user_data = db["users"][uid]
            if user_data.get("nexus_shards", 0) < amount:
                return {"ok": False, "error": "Not enough Shards."}

            user_data["nexus_shards"] -= amount
            save_db()

            r.bets[uid] = {"amount": amount, "cashed_out": False, "cashout_mult": None, "payout": 0}
            return {"ok": True, "round_id": r.round_id, "amount": amount}

    async def cashout(self, uid: str) -> dict:
        async with self.lock:
            r = self.current_round
            if r.phase != "flying":
                return {"ok": False, "error": "Round is not currently flying."}
            bet = r.bets.get(uid)
            if not bet:
                return {"ok": False, "error": "You have no active bet this round."}
            if bet["cashed_out"]:
                return {"ok": False, "error": "You already cashed out this round."}

            elapsed = time.time() - r.flying_started_at
            current_mult = multiplier_at(elapsed)
            # Lock in whichever is lower: the live multiplier, or the crash
            # point itself (covers the edge case where this request arrived
            # in the same tick the plane crashed — better to be conservative
            # than to ever pay out past the crash).
            locked_mult = min(current_mult, r.crash_point)

            payout = int(bet["amount"] * locked_mult)
            bet["cashed_out"] = True
            bet["cashout_mult"] = locked_mult
            bet["payout"] = payout

            db = ensure_user(uid, None, None)
            db["users"][uid]["nexus_shards"] = db["users"][uid].get("nexus_shards", 0) + payout
            save_db()

            return {"ok": True, "multiplier": locked_mult, "payout": payout}

    async def run_forever(self):
        """Background loop driving betting -> flying -> crashed -> repeat."""
        while True:
            r = self.current_round

            # ── BETTING PHASE ──
            await asyncio.sleep(BETTING_PHASE_SECONDS)
            async with self.lock:
                r.phase = "flying"
                r.flying_started_at = time.time()
                r.crash_at_time = r.flying_started_at + seconds_for_multiplier(r.crash_point)

            # ── FLYING PHASE ── poll until crash time is reached
            while True:
                await asyncio.sleep(TICK_INTERVAL)
                if time.time() >= r.crash_at_time:
                    break

            # ── CRASH ──
            async with self.lock:
                r.phase = "crashed"
                self.history.insert(0, {"round_id": r.round_id, "crash_point": r.crash_point})
                self.history = self.history[:ROUND_HISTORY_LIMIT]
                # Any bet not cashed out at this point is simply lost — the
                # shards were already debited at bet time, so there's
                # nothing further to do; we just leave payout at 0.

            # Brief pause so clients can render the crash before next round
            await asyncio.sleep(2.5)

            async with self.lock:
                self.current_round = AviatorRound()


engine = AviatorEngine()


# ==========================================
# HTTP ROUTE HANDLERS
# ==========================================
def _cors_headers() -> dict:
    # Wide-open CORS since the frontend is hosted on a different origin
    # (Netlify). Tighten `Access-Control-Allow-Origin` to your exact
    # Netlify domain once it's live, instead of "*", to prevent other
    # sites from embedding/calling this API on a user's behalf.
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    }


async def handle_options(request: web.Request) -> web.Response:
    return web.Response(status=204, headers=_cors_headers())


async def handle_state(request: web.Request) -> web.Response:
    return web.json_response(engine.public_state(), headers=_cors_headers())


async def handle_bet(request: web.Request) -> web.Response:
    user = get_authed_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "Unauthorized."}, status=401, headers=_cors_headers())

    try:
        body = await request.json()
        amount = int(body.get("amount"))
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid request body."}, status=400, headers=_cors_headers())

    uid = str(user["id"])
    result = await engine.place_bet(uid, amount)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status, headers=_cors_headers())


async def handle_cashout(request: web.Request) -> web.Response:
    user = get_authed_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "Unauthorized."}, status=401, headers=_cors_headers())

    uid = str(user["id"])
    result = await engine.cashout(uid)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status, headers=_cors_headers())


async def handle_balance(request: web.Request) -> web.Response:
    user = get_authed_user(request)
    if not user:
        return web.json_response({"ok": False, "error": "Unauthorized."}, status=401, headers=_cors_headers())

    uid = str(user["id"])
    db = ensure_user(uid, user.get("first_name"), user.get("username"))
    return web.json_response(
        {"ok": True, "nexus_shards": db["users"][uid].get("nexus_shards", 0)},
        headers=_cors_headers()
    )


# ==========================================
# SERVER STARTUP
# ==========================================
def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/aviator/state", handle_state)
    app.router.add_post("/aviator/bet", handle_bet)
    app.router.add_post("/aviator/cashout", handle_cashout)
    app.router.add_get("/aviator/balance", handle_balance)
    for path in ["/aviator/state", "/aviator/bet", "/aviator/cashout", "/aviator/balance"]:
        app.router.add_route("OPTIONS", path, handle_options)
    return app


async def start_aviator_server():
    """Call this once from card_aio.py via asyncio.create_task(...).
    Runs the aiohttp server AND the round engine loop concurrently, forever."""
    import sys
    print("[AVIATOR-DEBUG] start_aviator_server() coroutine has started executing.", flush=True)
    port = int(os.environ.get("PORT", 5000))
    print(f"[AVIATOR] Attempting to bind 0.0.0.0:{port} (PORT env var = {os.environ.get('PORT', '<not set, defaulted to 5000>')})...", flush=True)

    try:
        app = build_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=port)
        await site.start()
        print(f"[AVIATOR] HTTP API listening on 0.0.0.0:{port}", flush=True)
    except Exception as bind_err:
        print(f"[AVIATOR] FAILED TO BIND on port {port}: {bind_err!r}", flush=True)
        raise  # re-raise so the done_callback in card_aio.py also logs it

    # Run the round engine loop forever alongside the server
    await engine.run_forever()
