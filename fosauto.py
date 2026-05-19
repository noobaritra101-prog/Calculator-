import io
from PIL import Image, ImageOps
import asyncio
import unicodedata
import re
import os
import random
import difflib
import time
import datetime
import json
import zipfile
import gc
from telethon import TelegramClient, events, Button
from telethon.errors import SessionPasswordNeededError, MessageNotModifiedError
import ddddocr

# --- CONFIGURATION ---
API_ID = 26759620
API_HASH = 'e5c2cfff7011b7fee949ed8293bafde8'
BOT_TOKEN = '8312114130:AAHheUkpnlXSTTKLwsvVq-EynSoxVwzLXLs' # 🔴 PUT YOUR BOTFATHER TOKEN HERE
TARGET_BOT_ID = 8015674697
OWNER_ID = 5716292610 # 👑 Your Owner ID
GROUP_ID = -1003711336964 # 🛡️ Required Group ID (Bot must be admin here)
DATA_FILE = "users_data.json" # 💾 Database File
LOG_FILE = "bot.log" # 📜 Log File

# --- CUSTOM LOGGER ---
def cprint(*args, **kwargs):
    """Custom print function that also appends to bot.log"""
    msg = " ".join(map(str, args))
    print(msg, **kwargs)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# Initialize the offline OCR engine once
ocr_engine = ddddocr.DdddOcr(show_ad=False)

# The Controller Bot (HTML Parse Mode)
bot = TelegramClient('controller_bot', API_ID, API_HASH)
bot.parse_mode = 'html'

active_users = {}

# --- DATABASE HANDLERS ---
def save_users_data():
    """Saves all active users' settings and stats to a JSON file."""
    data = {}
    for uid, ub in active_users.items():
        data[str(uid)] = {
            "phone": ub.phone,
            "user_name": ub.user_name,
            "combat_mode": ub.combat_mode,
            "ignore_nl": ub.ignore_nl,
            "ignore_leg": ub.ignore_leg,
            "ignore_et": ub.ignore_et,
            "sub_slay_nl": ub.sub_slay_nl,
            "sub_slay_leg": ub.sub_slay_leg,
            "sub_slay_et": ub.sub_slay_et,
            "stats": ub.stats
        }
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_users_data():
    """Loads saved settings from the JSON file."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

# --- GROUP MEMBERSHIP CHECKER ---
async def is_subscribed(user_id):
    if user_id == OWNER_ID:
        return True 
    try:
        await bot.get_permissions(GROUP_ID, user_id)
        return True
    except Exception:
        return False

# --- USER BOT CLASS ---
class UserBot:
    def __init__(self, user_id, phone, user_name, client):
        self.user_id = user_id
        self.phone = phone
        self.user_name = user_name
        self.client = client
        self.client.parse_mode = 'html'
        
        self.is_running = False
        self.in_captcha = False
        self.stop_requested = False
        self.combat_mode = 'all'
        
        self.ignore_nl = False
        self.ignore_leg = False
        self.ignore_et = False
        
        self.sub_slay_nl = False
        self.sub_slay_leg = False
        self.sub_slay_et = False
        
        self.stats = {
            "nl": 0, "leg": 0, "et": 0,
            "unknown_char": 0, "monsters_sealed": 0,
            "slayed_names_nl": [],
            "slayed_names_leg": [],
            "slayed_names_et": []
        }
        
        self.response_received_event = asyncio.Event()
        self.watchdog_task = None 
        self.last_g3_time = 0 
        self.last_g4_time = 0 

    def reset_stats(self):
        self.stats = {
            "nl": 0, "leg": 0, "et": 0,
            "unknown_char": 0, "monsters_sealed": 0,
            "slayed_names_nl": [],
            "slayed_names_leg": [],
            "slayed_names_et": []
        }
        save_users_data()

    async def notify(self, message):
        try:
            await bot.send_message(self.user_id, message)
        except Exception as e:
            cprint(f"Failed to notify user {self.user_id}: {e}")

    async def request_stop(self, reply_event=None):
        if not self.is_running:
            if reply_event:
                await reply_event.reply(f"<b>⚠️ Script for {self.user_name} is already stopped!</b>")
            return "already_stopped"
            
        if self.in_captcha:
            self.stop_requested = True
            msg = f"<b>⏳ Safe Stop Initiated for {self.user_name}:</b>\n<blockquote>Finishing the current captcha before safely stopping...</blockquote>"
            if reply_event: await reply_event.reply(msg)
            else: await self.notify(msg)
            return "safe_stop_pending"
        else:
            self.is_running = False
            if self.watchdog_task: self.watchdog_task.cancel()
            msg = f"<b>🔴 Auto-script STOPPED for {self.user_name}.</b>"
            if reply_event: await reply_event.reply(msg)
            else: await self.notify(msg)
            return "stopped"

    async def watchdog_worker(self):
        cprint(f"👁️ Watchdog active for User {self.user_id}.")
        loop_counter = 0
        while self.is_running:
            try:
                await asyncio.wait_for(self.response_received_event.wait(), timeout=12.0)
                self.response_received_event.clear()
            except asyncio.TimeoutError:
                if self.is_running and not self.in_captcha:
                    cprint(f"⚠️ Watchdog (User {self.user_id}): No response for 12s. Poking bot...")
                    try:
                        await self.client.send_message(TARGET_BOT_ID, "/explore")
                    except Exception:
                        pass
                    self.response_received_event.clear()
            except asyncio.CancelledError:
                break
            
            loop_counter += 1
            if loop_counter >= 5:
                loop_counter = 0
                if not await is_subscribed(self.user_id):
                    self.is_running = False
                    await self.notify("<b>🚫 Access Revoked:</b>\n<blockquote>You left the required group. Your auto-grinder has been STOPPED immediately.</blockquote>")
                    break

    async def trigger_explore(self):
        if not self.is_running: return
        asyncio.create_task(self._delayed_explore())

    async def _delayed_explore(self):
        if not self.is_running: return
        delay = random.uniform(1.0, 2.0)
        await asyncio.sleep(delay)
        self.response_received_event.clear()
        try:
            await self.client.send_message(TARGET_BOT_ID, "/explore")
        except Exception:
            pass

# --- EVENT HANDLER GENERATOR ---
def create_event_handler(ub: UserBot):
    async def handler(event):
        await game_handler(event, ub)
    return handler

# --- AUTO-LOGIN SYSTEM ---
async def auto_start_sessions():
    cprint("🔄 Scanning for saved user sessions...")
    saved_data = load_users_data()
    
    for uid_str, data in saved_data.items():
        user_id = int(uid_str)
        session_file = f"session_{user_id}.session"
        
        if not os.path.exists(session_file):
            continue 
            
        client = TelegramClient(f'session_{user_id}', API_ID, API_HASH)
        await client.connect()
        
        if await client.is_user_authorized():
            ub = UserBot(user_id, data.get("phone", "Unknown"), data.get("user_name", "User"), client)
            
            ub.combat_mode = data.get("combat_mode", "all")
            ub.ignore_nl = data.get("ignore_nl", False)
            ub.ignore_leg = data.get("ignore_leg", False)
            ub.ignore_et = data.get("ignore_et", False)
            ub.sub_slay_nl = data.get("sub_slay_nl", False)
            ub.sub_slay_leg = data.get("sub_slay_leg", False)
            ub.sub_slay_et = data.get("sub_slay_et", False)
            ub.stats = data.get("stats", ub.stats)
            
            active_users[user_id] = ub
            
            handler = create_event_handler(ub)
            client.add_event_handler(handler, events.NewMessage(chats=TARGET_BOT_ID))
            client.add_event_handler(handler, events.MessageEdited(chats=TARGET_BOT_ID))
            
            cprint(f"✅ Auto-Logged In: {ub.user_name} ({user_id})")
        else:
            cprint(f"❌ Session Expired: {user_id}")
            
    save_users_data()
    cprint("✦ Auto-login sequence complete.")

# --- GLOBAL MIDNIGHT RESET TASK ---
async def midnight_reset_worker():
    while True:
        now = datetime.datetime.now()
        tomorrow = now + datetime.timedelta(days=1)
        midnight = datetime.datetime(year=tomorrow.year, month=tomorrow.month, day=tomorrow.day, hour=0, minute=0, second=0)
        seconds_until_midnight = (midnight - now).seconds
        
        await asyncio.sleep(seconds_until_midnight)
        
        for user_id, ub in active_users.items():
            ub.reset_stats()
            msg = (
                "<b>❖ ━━━━ SYSTEM RESET ━━━━ ❖</b>\n"
                "<blockquote><i>Your session stats have been zeroed out for the new day.</i></blockquote>"
            )
            await ub.notify(msg)
        
        await asyncio.sleep(60)

# 🛡️ DOUBLE-PASS OCR SYSTEM
async def extract_text_from_image(photo_path):
    try:
        img = Image.open(photo_path)
        
        # --- PASS 1: High Contrast Filter ---
        img_proc = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
        img_proc = img_proc.convert('L')
        img_proc = img_proc.point(lambda p: 0 if p < 230 else 255) 
        inverted_for_bbox = ImageOps.invert(img_proc) 
        bbox = inverted_for_bbox.getbbox()
        if bbox:
            img_proc = img_proc.crop(bbox)
            img_proc = ImageOps.expand(img_proc, border=10, fill='white')
            
        byte_arr_proc = io.BytesIO()
        img_proc.save(byte_arr_proc, format='JPEG')
        
        def _read_proc(): return ocr_engine.classification(byte_arr_proc.getvalue())
        result = await asyncio.wait_for(asyncio.to_thread(_read_proc), timeout=15.0)
        res_str = result.lower().strip()
        
        # --- PASS 2: Fallback to Raw Image if Pass 1 gave garbage ---
        if not re.search(r'[a-z0-9]', res_str):
            byte_arr_raw = io.BytesIO()
            img.save(byte_arr_raw, format='JPEG')
            def _read_raw(): return ocr_engine.classification(byte_arr_raw.getvalue())
            result_raw = await asyncio.wait_for(asyncio.to_thread(_read_raw), timeout=15.0)
            res_str = result_raw.lower().strip()

        return res_str
    except Exception as e:
        cprint(f"OCR Error: {e}")
        return ""
    finally:
        if os.path.exists(photo_path): os.remove(photo_path)

# --- MAIN GAME HANDLER (Attached per user) ---
async def game_handler(event, ub: UserBot):
    if not ub.is_running: return

    ub.response_received_event.set()
    raw_text = event.raw_text or ""
    clean_text = unicodedata.normalize('NFKC', raw_text).lower()
    clean_text = clean_text.replace(" ", "").replace("-", "").replace("_", "").replace("\n", "")

    try:
        current_time = time.time()

        # 1. CHESTS & EVENTS
        if any(kw in clean_text for kw in ["mysterioustrader", "auracrate", "tailedbeast", "wanderunseen"]):
            if current_time - ub.last_g4_time < 3.0: return 
            ub.last_g4_time = current_time 
            await ub.trigger_explore()
            return

        elif any(kw in clean_text for kw in ["solved", "crate", "seal", "reward", "were", "smiles", "retreated"]):
            if current_time - ub.last_g3_time < 3.0: return 
            ub.last_g3_time = current_time 
            await ub.trigger_explore()
            return

        # --- NEW: FLOOR BOSS EVENT (Only in Subjugate Mode) ---
        elif any(kw in clean_text for kw in ["floorboss", "anonymousboss", "tremendousaura"]) and event.message.buttons:
            if ub.combat_mode != 'subjugate':
                # Skip Boss if not in Subjugate mode
                await ub.trigger_explore()
                return

            await asyncio.sleep(random.uniform(1.0, 2.0))
            clicked = False
            for row_idx, row in enumerate(event.message.buttons):
                for col_idx, btn in enumerate(row):
                    if not btn.text: continue
                    if "challenge" in btn.text.lower():
                        for attempt in range(3):
                            try:
                                await event.message.click(row_idx, col_idx)
                                cprint(f"⚔️ {ub.user_name} challenged the Floor Boss!")
                                clicked = True
                                break
                            except Exception: 
                                await asyncio.sleep(1.0)
                        break
                if clicked: break

            if not clicked:
                await ub.trigger_explore()
            return

        # 2. ENCOUNTERS
        elif any(kw in clean_text for kw in ["yourself", "trembles", "encountered", "character"]) and event.message.buttons:
            char_name = "Unknown"
            name_match = re.search(r'Name:\s*([^\n]+)', raw_text)
            if name_match: char_name = name_match.group(1).strip()

            is_monster = "encountered" in clean_text or "yourself" in clean_text
            is_character = "character" in clean_text or "trembles" in clean_text
            is_nl = "rarity:nonlegendary" in clean_text
            is_leg = "rarity:legendary" in clean_text
            is_et = "rarity:eternal" in clean_text

            if ub.combat_mode == 'slay' and is_monster:
                await ub.trigger_explore()
                return

            if ub.combat_mode == 'subjugate' and is_character:
                exception_triggered = False
                if is_nl and ub.sub_slay_nl: exception_triggered = True
                if is_leg and ub.sub_slay_leg: exception_triggered = True
                if is_et and ub.sub_slay_et: exception_triggered = True
                
                if not exception_triggered:
                    await ub.trigger_explore()
                    return

            if is_character and ub.combat_mode != 'subjugate':
                if is_nl and ub.ignore_nl:
                    await ub.trigger_explore()
                    return
                if is_leg and ub.ignore_leg:
                    await ub.trigger_explore()
                    return
                if is_et and ub.ignore_et:
                    await ub.trigger_explore()
                    return

            if is_character:
                if is_nl: 
                    ub.stats['nl'] += 1
                    ub.stats.setdefault('slayed_names_nl', []).append(char_name)
                elif is_leg: 
                    ub.stats['leg'] += 1
                    ub.stats.setdefault('slayed_names_leg', []).append(char_name)
                elif is_et: 
                    ub.stats['et'] += 1
                    ub.stats.setdefault('slayed_names_et', []).append(char_name)
                else: 
                    ub.stats['unknown_char'] += 1
                save_users_data() 
            elif is_monster:
                ub.stats['monsters_sealed'] += 1
                save_users_data()

            await asyncio.sleep(random.uniform(1.0, 2.0))
            clicked = False
            for row_idx, row in enumerate(event.message.buttons):
                for col_idx, btn in enumerate(row):
                    if not btn.text: continue
                    btn_lower = btn.text.lower()
                    if any(word in btn_lower for word in ['slay', 'attack', 'subjugate', 'seal', 'subjucate', 'fight']):
                        for attempt in range(3):
                            try:
                                await event.message.click(row_idx, col_idx)
                                clicked = True
                                break
                            except Exception: await asyncio.sleep(1.0)
                        break
                if clicked: break

            if not clicked:
                for attempt in range(3):
                    try:
                        await event.message.click(0, 0)
                        break
                    except Exception: await asyncio.sleep(1.0)

        # 3. CAPTCHAS
        elif any(kw in clean_text for kw in ["whackamole", "pokeselection", "captchachallenge", "captcha", "verify"]) and event.photo and event.message.buttons:
            button_texts = [btn.text.lower() for row in event.message.buttons for btn in row if btn.text]
            if any(combat_word in b for b in button_texts for combat_word in ['slay', 'seal', 'attack', 'subjugate', 'fight']):
                return 
            
            ub.in_captcha = True 
            try:
                photo_path = await event.download_media()
                ocr_text = await extract_text_from_image(photo_path)
                
                clicked = False
                clean_ocr = re.sub(r'(?i)(pick|pck|pic|piks|pcks:?|hit:?)[^a-z0-9]*', '', ocr_text).strip()
                if not clean_ocr: clean_ocr = ocr_text

                if button_texts:
                    matches = difflib.get_close_matches(clean_ocr, button_texts, n=1, cutoff=0.25)
                    if matches:
                        for row_idx, row in enumerate(event.message.buttons):
                            for col_idx, btn in enumerate(row):
                                if btn.text and btn.text.lower() == matches[0]:
                                    await asyncio.sleep(random.uniform(1.0, 2.0))
                                    for attempt in range(3):
                                        try:
                                            await event.message.click(row_idx, col_idx)
                                            clicked = True
                                            break
                                        except Exception: await asyncio.sleep(1.0)
                                    if not clicked:
                                        ub.is_running = False
                                        if ub.watchdog_task: ub.watchdog_task.cancel()
                                        await ub.notify("<b>🔴 FAILSAFE TRIGGERED</b>\n<blockquote>Captcha match button failed. <u>Script STOPPED.</u></blockquote>")
                                        return
                                    break
                            if clicked: break

                if not clicked:
                    match = re.search(r'\d', ocr_text) 
                    if match:
                        t_num = int(match.group(0))
                        if 1 <= t_num <= 9:
                            await asyncio.sleep(random.uniform(1.0, 2.0))
                            for attempt in range(3):
                                try:
                                    await event.message.click((t_num - 1) // 3, (t_num - 1) % 3)
                                    clicked = True
                                    break
                                except Exception: await asyncio.sleep(1.0)
                            if not clicked:
                                ub.is_running = False
                                if ub.watchdog_task: ub.watchdog_task.cancel()
                                await ub.notify("<b>🔴 FAILSAFE TRIGGERED</b>\n<blockquote>Captcha grid button failed. <u>Script STOPPED.</u></blockquote>")
                                return

                if not clicked:
                    ub.is_running = False 
                    if ub.watchdog_task: ub.watchdog_task.cancel()
                    await ub.notify("<b>🔴 FAILSAFE TRIGGERED</b>\n<blockquote>OCR failed to match anything. <u>Script STOPPED.</u></blockquote>")
                return 

            finally:
                ub.in_captcha = False
                if ub.stop_requested and ub.is_running:
                    ub.stop_requested = False
                    ub.is_running = False
                    if ub.watchdog_task: ub.watchdog_task.cancel()
                    await ub.notify("<b>🔴 Auto-script safely STOPPED</b> after completing the captcha.")

    except Exception as e:
        cprint(f"Error handling message for {ub.user_id}: {e}")

# --- UI GENERATORS ---
def get_stats_msg(ub: UserBot):
    nl_names = ub.stats.get('slayed_names_nl', [])[-5:]
    leg_names = ub.stats.get('slayed_names_leg', [])[-5:]
    et_names = ub.stats.get('slayed_names_et', [])[-5:]
    
    nl_str = ", ".join(nl_names) if nl_names else "None"
    leg_str = ", ".join(leg_names) if leg_names else "None"
    et_str = ", ".join(et_names) if et_names else "None"

    return (
        "<b>❖ ━━━━ LIVE STATS ━━━━ ❖</b>\n"
        f"User : <a href='tg://user?id={ub.user_id}'>{ub.user_name}</a>\n"
        "<i>(Stats reset automatically at Midnight)</i>\n\n"
        "<b>🎗️ Characters Slayed:</b>\n"
        f"  ├ Non-Legendary: <code>{ub.stats.get('nl', 0)}</code>\n"
        f"  ├ Legendary: <code>{ub.stats.get('leg', 0)}</code>\n"
        f"  └ Eternal: <code>{ub.stats.get('et', 0)}</code>\n\n"
        f"<b>⚔️ Monsters Sealed:</b> <code>{ub.stats.get('monsters_sealed', 0)}</code>\n\n"
        "<b>📜 Recent Slayed:</b>\n"
        "<blockquote>"
        f"<b>Non-Legendary:</b> <i>{nl_str}</i>\n"
        f"<b>Legendary:</b> <i>{leg_str}</i>\n"
        f"<b>Eternal:</b> <i>{et_str}</i>\n"
        "</blockquote>"
    )

def get_settings_text(ub: UserBot):
    exp = "<b>❖ ━━━━ BEHAVIOR EXPLANATION ━━━━ ❖</b>\n<blockquote>"
    if ub.combat_mode == 'all': exp += "✦ The bot will engage <b>EVERYTHING</b> <i>(Monsters & Characters)</i>.\n"
    elif ub.combat_mode == 'slay': exp += "✦ The bot will <u>ONLY slay Characters</u>. It will completely SKIP Monsters.\n"
    elif ub.combat_mode == 'subjugate':
        exp += "✦ The bot will <u>ONLY seal Monsters & Fight Bosses</u>. It will normally SKIP all Characters.\n"
        sub_slays = []
        if ub.sub_slay_nl: sub_slays.append("Non-Legendary")
        if ub.sub_slay_leg: sub_slays.append("Legendary")
        if ub.sub_slay_et: sub_slays.append("Eternal")
        if sub_slays: exp += f"  ✠ <i>EXCEPTION:</i> If a <b>{' / '.join(sub_slays)}</b> character spawns, it will STOP sealing and <b>SLAY</b> it!\n"
    if ub.combat_mode != 'subjugate':
        ignores = []
        if ub.ignore_nl: ignores.append("Non-Legendary")
        if ub.ignore_leg: ignores.append("Legendary")
        if ub.ignore_et: ignores.append("Eternal")
        if ignores: exp += f"\n🚫 <i>GLOBAL IGNORE:</i> It will absolutely NEVER fight <b>{' / '.join(ignores)}</b> characters."
    exp += "</blockquote>"

    return (
        f"<b>『 USER DASHBOARD 』</b> ✦ User: <a href='tg://user?id={ub.user_id}'>{ub.user_name}</a>\n\n"
        f"{exp}\n"
        f"<b>❖ CONFIGURATION MENU ❖</b>\n<i>Select an option below to toggle it:</i>"
    )

def get_settings_keyboard(ub: UserBot, owner_id):
    mode_emoji = "🎗️" if ub.combat_mode == 'slay' else "⚔️" if ub.combat_mode == 'subjugate' else "👊"
    cb_prefix = f"{owner_id}:" 
    keyboard = [[Button.inline(f"『 ACTIVE MODE: {ub.combat_mode.upper()} {mode_emoji} 』", f"{cb_prefix}toggle_mode".encode())]]
    
    if ub.combat_mode != 'subjugate':
        keyboard.append([Button.inline("━━━━ 🚫 GLOBAL IGNORES ━━━━", f"{cb_prefix}none".encode())])
        keyboard.append([
            Button.inline(f"NL: {'●' if ub.ignore_nl else '○'}", f"{cb_prefix}ign_nl".encode()),
            Button.inline(f"Leg: {'●' if ub.ignore_leg else '○'}", f"{cb_prefix}ign_leg".encode()),
            Button.inline(f"Et: {'●' if ub.ignore_et else '○'}", f"{cb_prefix}ign_et".encode())
        ])
    if ub.combat_mode == 'subjugate':
        keyboard.append([Button.inline("━━━━ ⚔️ SUBJUGATE OVERRIDES ━━━━", f"{cb_prefix}none".encode())])
        keyboard.append([
            Button.inline(f"Slay NL: {'●' if ub.sub_slay_nl else '○'}", f"{cb_prefix}sub_nl".encode()),
            Button.inline(f"Slay Leg: {'●' if ub.sub_slay_leg else '○'}", f"{cb_prefix}sub_leg".encode()),
            Button.inline(f"Slay Et: {'●' if ub.sub_slay_et else '○'}", f"{cb_prefix}sub_et".encode())
        ])
    return keyboard

def get_log_text():
    if not os.path.exists(LOG_FILE):
        return "<b>📜 System Log:</b>\n<blockquote><i>Log file is empty.</i></blockquote>"
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    last_30 = lines[-30:]
    log_str = "".join(last_30)
    if not log_str: 
        return "<b>📜 System Log:</b>\n<blockquote><i>Log file is empty.</i></blockquote>"
    if len(log_str) > 3800:
        log_str = log_str[-3800:]
    return f"<b>📜 System Log (Last 30 Lines):</b>\n<pre>{log_str}</pre>"

def get_slog_buttons():
    return [
        [Button.inline("🌀 Refresh", b"slog_refresh"), Button.inline("🌩️ Reset", b"slog_reset")],
        [Button.inline("× Close", b"owner_close_wstats")]
    ]

# --- COMMANDS ---
@bot.on(events.NewMessage(pattern=r'(?i)^/start'))
async def start_cmd(event):
    if not await is_subscribed(event.sender_id):
        return await event.reply("<b>🚫 Access Denied!</b>\n<blockquote>You must join our group to use this bot.\n<b>Group ID:</b> <code>-1003711336964</code></blockquote>")
        
    msg = (
        "<b>❖ ━━━━ CONTROLLER BOT ━━━━ ❖</b>\n\n"
        "<blockquote>"
        "✦ <code>/login</code> - Connect your account\n"
        "✦ <code>/logout</code> - Safely remove your session\n"
        "✦ <code>/st_exp</code> - Start farming\n"
        "✦ <code>/stop</code> - Stop farming safely\n"
        "✦ <code>/settings</code> - Configure Modes & Ignores\n"
        "✦ <code>/stats</code> - View your Kill/Seal counts\n"
        "✦ <code>/id</code> - Check structural IDs"
        "</blockquote>"
    )
    await event.reply(msg)

@bot.on(events.NewMessage(pattern=r'(?i)^/id'))
async def id_cmd(event):
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        sender = await reply_msg.get_sender()
        if not sender:
            return await event.reply("Could not fetch sender of the replied message.")
    else:
        sender = await event.get_sender()
        
    name = sender.first_name if sender and sender.first_name else "User"
    user_id = sender.id if sender else "Unknown"
    user_link = f"<a href='tg://user?id={user_id}'>{name}</a>"
    username = f"@{sender.username}" if sender and sender.username else "None"
    
    msg = (
        "╭─❍\n"
        f"├➤ 🫧 <b>𝗡𝗮𝗺𝗲 :</b> {user_link}\n"
        f"├➤ 🐝 <b>𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲 :</b> {username}\n"
        f"├➤ 🍷 <b>𝗨𝘀𝗲𝗿 𝗜𝗗 :</b> <code>{user_id}</code>\n"
        f"╰➤ 🪸 <b>𝗖𝗵𝗮𝘁 𝗜𝗗 :</b> <code>{event.chat_id}</code>"
    )
    await event.reply(msg)

@bot.on(events.NewMessage(pattern=r'(?i)^/login'))
async def login_cmd(event):
    if not await is_subscribed(event.sender_id):
        return await event.reply("<b>🚫 Access Denied!</b>\n<blockquote>You must join our group to use this bot.</blockquote>")

    if event.is_group:
        return await event.reply("<b>⚠️ Security Alert:</b>\n<blockquote>Please send <code>/login</code> to me in a <b>Private Message (DM)</b> so you don't leak your phone number and login code to the public group!</blockquote>")

    user_id = event.sender_id
    if user_id in active_users:
        return await event.reply("<b>✅ You are already logged in!</b>")
        
    sender = await event.get_sender()
    user_name = sender.first_name if sender.first_name else "User"

    async with bot.conversation(event.chat_id, timeout=300) as conv:
        await conv.send_message("<b>📱 Please enter your Phone Number</b>\n<i>(Include the country code, e.g. <u>+1234567890</u>)</i>:")
        try:
            phone_msg = await conv.get_response()
            phone = phone_msg.text.strip()
            
            client = TelegramClient(f'session_{user_id}', API_ID, API_HASH)
            await client.connect()
            
            if await client.is_user_authorized():
                await conv.send_message("<b>✅ Session found!</b> You are already authorized.")
            else:
                send_code_info = await client.send_code_request(phone)
                await conv.send_message("<b>✉️ Code Sent!</b>\nPlease enter the code below.\n<blockquote><i>Tip: If it's a number, put a space in the middle like <code>123 45</code> so Telegram doesn't block it</i></blockquote>")
                
                code_msg = await conv.get_response()
                code = code_msg.text.replace(" ", "").replace("-", "").strip()
                
                try:
                    await client.sign_in(phone=phone, code=code, phone_code_hash=send_code_info.phone_code_hash)
                except SessionPasswordNeededError:
                    await conv.send_message("<b>🔐 2FA is enabled.</b> Please enter your password:")
                    pw_msg = await conv.get_response()
                    await client.sign_in(password=pw_msg.text.strip())
            
            ub = UserBot(user_id, phone, user_name, client)
            active_users[user_id] = ub
            save_users_data() 
            
            handler = create_event_handler(ub)
            client.add_event_handler(handler, events.NewMessage(chats=TARGET_BOT_ID))
            client.add_event_handler(handler, events.MessageEdited(chats=TARGET_BOT_ID))
                
            await conv.send_message("<b>🎉 Login Successful!</b>\nSend <code>/settings</code> to configure your bot, then <code>/st_exp</code> to start.")
            
        except asyncio.TimeoutError:
            await conv.send_message("<b>⏳ Login timed out.</b> Send <code>/login</code> to try again.")
        except Exception as e:
            await conv.send_message(f"<b>❌ Login Error:</b> <code>{str(e)}</code>")

@bot.on(events.NewMessage(pattern=r'(?i)^/logout', func=lambda e: e.is_private))
async def logout_cmd(event):
    if not await is_subscribed(event.sender_id): return
    user_id = event.sender_id
    if user_id not in active_users:
        return await event.reply("<b>⚠️ You are not logged in!</b>")
        
    ub = active_users.pop(user_id)
    ub.is_running = False
    if ub.watchdog_task: ub.watchdog_task.cancel()
    
    save_users_data() 
    await ub.client.log_out()
    await event.reply("<b>🚪 Logged Out Successfully.</b>\n<blockquote>Your session has been securely erased from the server.</blockquote>")

@bot.on(events.NewMessage(pattern=r'(?i)^/st_exp'))
async def start_exp_cmd(event):
    if not await is_subscribed(event.sender_id):
        return await event.reply("<b>🚫 Access Denied!</b>\n<blockquote>You must join our group to use this bot.</blockquote>")
        
    ub = active_users.get(event.sender_id)
    if not ub: return await event.reply("<b>⚠️ Please <code>/login</code> first!</b>")
        
    if not ub.is_running:
        ub.is_running = True
        ub.watchdog_task = asyncio.create_task(ub.watchdog_worker())
        emoji = "🎗️" if ub.combat_mode == 'slay' else "⚔️" if ub.combat_mode == 'subjugate' else "👊"
        await event.reply(f"<b>🟢 Auto-script STARTED.</b>\n<blockquote>❖ <u>Current Mode:</u> <b>{ub.combat_mode.upper()} {emoji}</b></blockquote>\n<i>Initiating first <code>/explore</code>...</i>")
        await ub.trigger_explore()
    else:
        await event.reply("<b>⚠️ Script is already running!</b>")

@bot.on(events.NewMessage(pattern=r'(?i)^/stop'))
async def stop_exp_cmd(event):
    if not await is_subscribed(event.sender_id): return
    ub = active_users.get(event.sender_id)
    if not ub: return await event.reply("<b>⚠️ Please <code>/login</code> first!</b>")
    await ub.request_stop(event)

@bot.on(events.NewMessage(pattern=r'(?i)^/settings'))
async def settings_cmd(event):
    if not await is_subscribed(event.sender_id):
        return await event.reply("<b>🚫 Access Denied!</b>\n<blockquote>You must join our group to use this bot.</blockquote>")
        
    ub = active_users.get(event.sender_id)
    if not ub: return await event.reply("<b>⚠️ Please <code>/login</code> first!</b>")
    await event.reply(get_settings_text(ub), buttons=get_settings_keyboard(ub, event.sender_id))

@bot.on(events.NewMessage(pattern=r'(?i)^/stats'))
async def stats_cmd(event):
    if not await is_subscribed(event.sender_id):
        return await event.reply("<b>🚫 Access Denied!</b>\n<blockquote>You must join our group to use this bot.</blockquote>")
        
    target_id = event.sender_id
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        target_id = reply_msg.sender_id

    ub_target = active_users.get(target_id)
    if not ub_target:
        if target_id == event.sender_id: return await event.reply("<b>⚠️ Please <code>/login</code> first!</b>")
        else: return await event.reply("<b>⚠️ That user is not currently logged into the bot!</b>")

    cb_prefix = f"{event.sender_id}:"
    buttons = [
        [Button.inline("🌀 Refresh", f"{cb_prefix}stat_ref:{target_id}".encode()), 
         Button.inline("🈯 Reset", f"{cb_prefix}stat_res_req:{target_id}".encode())],
        [Button.inline("× Close", f"{cb_prefix}close_msg".encode())]
    ]
    await event.reply(get_stats_msg(ub_target), buttons=buttons)

# --- 👑 OWNER COMMANDS ---
@bot.on(events.NewMessage(pattern=r'(?i)^/slog'))
async def slog_cmd(event):
    if event.sender_id != OWNER_ID: return
    await event.reply(get_log_text(), buttons=get_slog_buttons())

@bot.on(events.NewMessage(pattern=r'(?i)^/duw'))
async def duw_cmd(event):
    if event.sender_id != OWNER_ID: return
    
    cleaned_files = 0
    for file in os.listdir('.'):
        if file.endswith(('.jpg', '.jpeg', '.png', '.webp', '.mp4')):
            try:
                os.remove(file)
                cleaned_files += 1
            except Exception:
                pass
                
    collected = gc.collect()
    
    msg = (
        "<b>🧹 System Cleanup Complete!</b>\n"
        "<blockquote>"
        f"🗑️ <b>Deleted Unwanted Media:</b> <code>{cleaned_files}</code> files\n"
        f"🧠 <b>Memory Freed (GC Objects):</b> <code>{collected}</code>\n"
        "<i>Server memory has been cooled. ❄️</i>"
        "</blockquote>"
    )
    await event.reply(msg)

@bot.on(events.NewMessage(pattern=r'(?i)^/wstats'))
async def wstats_cmd(event):
    if event.sender_id != OWNER_ID: return
    
    if not active_users:
        return await event.reply("<b>👑 World Stats:</b> <i>No active users logged in.</i>")
        
    msg = "<b>👑 WORLD STATS (Logged-in Users) 👑</b>\n\n"
    for uid, ub in active_users.items():
        status = "🟢 GRINDING" if ub.is_running else "🔴 STOPPED"
        msg += "<blockquote>"
        msg += f"<b>👤 User:</b> <a href='tg://user?id={uid}'>{ub.user_name}</a> | <b>ID:</b> <code>{uid}</code>\n"
        msg += f"└ <u>State:</u> <b>{status}</b> | <u>Mode:</u> <i>{ub.combat_mode.upper()}</i>\n"
        msg += f"└ <u>Kills:</u> <b>{sum([ub.stats.get('nl', 0), ub.stats.get('leg', 0), ub.stats.get('et', 0)])}</b> | <u>Seals:</u> <b>{ub.stats.get('monsters_sealed', 0)}</b>\n"
        msg += "</blockquote>\n"
        
    buttons = [[Button.inline("× Close", b"owner_close_wstats")]]
    await event.reply(msg, buttons=buttons)

@bot.on(events.NewMessage(pattern=r'(?i)^/flout (.*)'))
async def flout_cmd(event):
    if event.sender_id != OWNER_ID: return
    
    target = event.pattern_match.group(1).strip()
    target_ub = None
    
    for uid, ub in list(active_users.items()):
        if str(uid) == target or ub.phone == target:
            target_ub = ub
            break
            
    if not target_ub: return await event.reply(f"<b>⚠️ Could not find active session for:</b> <code>{target}</code>")
        
    target_ub.is_running = False
    if target_ub.watchdog_task: target_ub.watchdog_task.cancel()
    
    target_id = target_ub.user_id
    active_users.pop(target_id, None)
    save_users_data() 
    
    await target_ub.client.log_out()
    await target_ub.notify("<b>⚠️ You have been forcefully logged out by the Administrator.</b>")
    await event.reply(f"<b>👑 Success:</b> Forcefully logged out <code>{target}</code>.")

@bot.on(events.NewMessage(pattern=r'(?i)^/fwstop(?: |$)(.*)'))
async def fwstop_cmd(event):
    if event.sender_id != OWNER_ID: return
    
    target = event.pattern_match.group(1).strip().lower()
    
    if not target or target == "all":
        count = 0
        safe_count = 0
        for uid, ub in active_users.items():
            if ub.is_running:
                res = await ub.request_stop()
                if res == "safe_stop_pending":
                    safe_count += 1
                else:
                    count += 1
        await event.reply(
            f"<b>👑 Global Force Stop Complete:</b>\n"
            f"<blockquote>Stopped instantly: <code>{count}</code> users.\n"
            f"Safe-stop initiated (waiting on captcha completion): <code>{safe_count}</code> users.</blockquote>"
        )
    else:
        target_ub = None
        for uid, ub in list(active_users.items()):
            if str(uid) == target or ub.phone == target or (ub.user_name and ub.user_name.lower() == target):
                target_ub = ub
                break
                
        if not target_ub: return await event.reply(f"<b>⚠️ Could not find active session for:</b> <code>{target}</code>")
        await target_ub.request_stop(event)

@bot.on(events.NewMessage(pattern=r'(?i)^/esf'))
async def export_sessions_cmd(event):
    if event.sender_id != OWNER_ID: return
    
    backup_name = "backup_sessions.zip"
    status_msg = await event.reply("<b>⏳ Compressing session files and user data...</b>")
    
    try:
        with zipfile.ZipFile(backup_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            if os.path.exists(DATA_FILE):
                zipf.write(DATA_FILE)
            
            for file in os.listdir('.'):
                if file.startswith('session_') and file.endswith('.session'):
                    zipf.write(file)
        
        await event.reply(
            "<b>📦 Backup Complete!</b>\n<blockquote>Here are your session files and user data. Keep this safe! Reply to this file with <code>/wsf</code> on a new server to restore it.</blockquote>", 
            file=backup_name
        )
        await status_msg.delete()
        
    except Exception as e:
        await status_msg.edit(f"<b>❌ Error creating backup:</b> <code>{e}</code>")
    finally:
        if os.path.exists(backup_name):
            os.remove(backup_name)

@bot.on(events.NewMessage(pattern=r'(?i)^/wsf'))
async def import_sessions_cmd(event):
    if event.sender_id != OWNER_ID: return
    
    if not event.is_reply:
        return await event.reply("<b>⚠️ You must reply to a backup .zip file to restore it!</b>")
        
    reply_msg = await event.get_reply_message()
    if not reply_msg.file or not reply_msg.file.name.endswith('.zip'):
        return await event.reply("<b>⚠️ The replied message must be a valid .zip file!</b>")
        
    status_msg = await event.reply("<b>⏳ Disconnecting current sessions and downloading backup...</b>")
    
    try:
        for uid, ub in list(active_users.items()):
            ub.is_running = False
            if ub.watchdog_task: ub.watchdog_task.cancel()
            await ub.client.disconnect()
        active_users.clear()
        
        download_path = await reply_msg.download_media()
        
        with zipfile.ZipFile(download_path, 'r') as zipf:
            zipf.extractall('.')
            
        os.remove(download_path)
        
        await status_msg.edit("<b>✅ Extracted successfully. Reloading all sessions...</b>")
        await auto_start_sessions()
        await status_msg.edit(f"<b>🎉 Backup restored successfully!</b>\n<blockquote>Successfully reloaded <b>{len(active_users)}</b> user sessions into memory.</blockquote>")
        
    except Exception as e:
        await status_msg.edit(f"<b>❌ Error restoring backup:</b> <code>{e}</code>")

# --- INLINE BUTTON HANDLER ---
@bot.on(events.CallbackQuery())
async def callback_handler(event):
    data_full = event.data.decode('utf-8')
    
    if data_full == "owner_close_wstats":
        if event.sender_id != OWNER_ID: return await event.answer("🚫 Only the owner can close this.", alert=True)
        return await event.delete()
        
    if data_full in ["slog_refresh", "slog_reset"]:
        if event.sender_id != OWNER_ID: return await event.answer("🚫 Only the owner can use this.", alert=True)
        if data_full == "slog_reset":
            open(LOG_FILE, 'w').close()
            ans_msg = "Logs Reset! 🌩️"
        else:
            ans_msg = "Logs Refreshed! 🌀"
            
        try: await event.edit(get_log_text(), buttons=get_slog_buttons())
        except MessageNotModifiedError: pass
        return await event.answer(ans_msg, alert=(data_full == "slog_reset"))
    
    try:
        parts = data_full.split(':')
        target_user_id_str = parts[0]
        action = parts[1]
        target_id_for_stats = int(parts[2]) if len(parts) > 2 else event.sender_id
        
        if target_user_id_str == "none": return await event.answer("❖ This is a category header ❖", alert=False)
        if str(event.sender_id) != target_user_id_str: return await event.answer("🚫 You cannot click someone else's buttons!", alert=True)
    except ValueError:
        return await event.answer("Invalid button data.", alert=True)

    if not await is_subscribed(event.sender_id):
        return await event.answer("🚫 You must join the group to use this!", alert=True)
        
    if action == "close_msg": return await event.delete()
        
    is_stat_cmd = action.startswith("stat_")
    effective_target_id = target_id_for_stats if is_stat_cmd else event.sender_id
    
    ub = active_users.get(effective_target_id)
    if not ub: return await event.answer("Session not found.", alert=True)

    if action in ["stat_res_req", "stat_res_confirm"]:
        if effective_target_id != event.sender_id and event.sender_id != OWNER_ID:
            return await event.answer("🚫 You can only reset your own stats!", alert=True)

    ans = ""
    cb_prefix = f"{event.sender_id}:"
    
    if action == "stat_ref":
        buttons = [
            [Button.inline("🌀 Refresh", f"{cb_prefix}stat_ref:{effective_target_id}".encode()), 
             Button.inline("🈯 Reset", f"{cb_prefix}stat_res_req:{effective_target_id}".encode())],
            [Button.inline("× Close", f"{cb_prefix}close_msg".encode())]
        ]
        try: await event.edit(get_stats_msg(ub), buttons=buttons)
        except MessageNotModifiedError: pass
        return await event.answer("Stats Refreshed! 🌀", alert=False)
        
    if action == "stat_res_req":
        buttons = [
            [Button.inline("⚠️ Confirm Reset?", f"{cb_prefix}stat_res_confirm:{effective_target_id}".encode())],
            [Button.inline("× Cancel", f"{cb_prefix}stat_ref:{effective_target_id}".encode())],
            [Button.inline("× Close", f"{cb_prefix}close_msg".encode())]
        ]
        try: await event.edit(get_stats_msg(ub), buttons=buttons)
        except MessageNotModifiedError: pass
        return await event.answer("Confirmation Required!", alert=False)
        
    if action == "stat_res_confirm":
        ub.reset_stats()
        buttons = [
            [Button.inline("🌀 Refresh", f"{cb_prefix}stat_ref:{effective_target_id}".encode()), 
             Button.inline("🈯 Reset", f"{cb_prefix}stat_res_req:{effective_target_id}".encode())],
            [Button.inline("× Close", f"{cb_prefix}close_msg".encode())]
        ]
        try: await event.edit(get_stats_msg(ub), buttons=buttons)
        except MessageNotModifiedError: pass
        return await event.answer("Stats have been zeroed out! 🗑️", alert=True)
    
    if action == "toggle_mode":
        if ub.combat_mode == 'all': ub.combat_mode = 'slay'
        elif ub.combat_mode == 'slay': ub.combat_mode = 'subjugate'
        else: ub.combat_mode = 'all'
        ans = f"Mode set to {ub.combat_mode.upper()} ❖"
    elif action == "ign_nl": 
        ub.ignore_nl = not ub.ignore_nl
        ans = f"Ignore NL: {'ON' if ub.ignore_nl else 'OFF'} ✦"
    elif action == "ign_leg": 
        ub.ignore_leg = not ub.ignore_leg
        ans = f"Ignore Leg: {'ON' if ub.ignore_leg else 'OFF'} ✦"
    elif action == "ign_et": 
        ub.ignore_et = not ub.ignore_et
        ans = f"Ignore Et: {'ON' if ub.ignore_et else 'OFF'} ✦"
    elif action == "sub_nl": 
        ub.sub_slay_nl = not ub.sub_slay_nl
        ans = f"SubSlay NL: {'ON' if ub.sub_slay_nl else 'OFF'} ✠"
    elif action == "sub_leg": 
        ub.sub_slay_leg = not ub.sub_slay_leg
        ans = f"SubSlay Leg: {'ON' if ub.sub_slay_leg else 'OFF'} ✠"
    elif action == "sub_et": 
        ub.sub_slay_et = not ub.sub_slay_et
        ans = f"SubSlay Et: {'ON' if ub.sub_slay_et else 'OFF'} ✠"

    if ans: save_users_data() 
        
    try: await event.edit(get_settings_text(ub), buttons=get_settings_keyboard(ub, event.sender_id))
    except MessageNotModifiedError: pass

    if ans: await event.answer(ans, alert=False)
    else: await event.answer()

# --- ASYNC STARTUP ROUTINE ---
async def main():
    cprint("❖ ━━━━ Multi-User Controller Bot is starting ━━━━ ❖")
    await bot.start(bot_token=BOT_TOKEN)
    
    asyncio.create_task(auto_start_sessions())
    asyncio.create_task(midnight_reset_worker())
    
    cprint("✦ Bot is online! Users can now use commands securely in groups.")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
