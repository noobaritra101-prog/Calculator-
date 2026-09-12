"""
PROFILE BANNER SYSTEM
======================
Admin-managed pool of banner template images. Exactly one banner is
"default" at a time, and /profile (in handlers.py) composites the viewing
user's own Telegram profile picture into the round placeholder cut into
that banner, using Pillow. There's no per-user banner choice — everyone's
/profile uses whichever banner an admin has set as default.

Admin commands:
  /ab <name>        — reply to a photo to add it as a banner template
  /rb <banner_id>   — remove a banner
  /lbanner          — list all banners
  /set_default <id> — set the global default banner for everyone's /profile

Requires Pillow, numpy, and scipy (`pip install Pillow numpy scipy`).
"""
import os
import math
import time
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
import numpy as np
from scipy import ndimage

from aiogram.types import Message
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode

from config import bot, main_router, ADMIN_IDS, load_db, save_db

# ==========================================
# STORAGE
# ==========================================
# Downloaded banner images live on disk here — the db only stores metadata
# (name, file path, detected circle geometry, who added it, when). If this
# runs somewhere without a persistent volume, a redeploy can wipe this
# folder while the db entries survive; build_profile_banner() checks the
# file still exists and quietly falls back to the plain profile photo/text
# rather than crashing /profile if it's gone — just re-run /ab to restore it.
BANNERS_DIR = "banners"
os.makedirs(BANNERS_DIR, exist_ok=True)

# How close to pure white (0-255 per channel) a pixel must be to count as
# part of the circular placeholder cutout. Tight enough that bright colors
# elsewhere in a busy banner (gold lightning, oranges, etc.) don't get
# mistaken for it.
_WHITE_THRESHOLD = 245


def _next_banner_id(db: dict) -> str:
    banners = db.get("banners", {})
    existing = [int(k) for k in banners.keys() if k.isdigit()]
    return str(max(existing, default=0) + 1)


def _detect_circle(img: Image.Image) -> Optional[dict]:
    """Finds the largest near-white *connected region* in a banner template
    (not just the bounding box of every whitish pixel anywhere in the image
    — busy banners have plenty of those: cloud highlights, bright text,
    lightning, sparkle effects) and returns its center + radius, but only
    if that region is actually shaped like a filled circle. Returns None if
    no such region exists."""
    rgb = img.convert("RGB")
    arr = np.asarray(rgb)
    r, g, b = arr[..., 0].astype(int), arr[..., 1].astype(int), arr[..., 2].astype(int)
    mask = (r > _WHITE_THRESHOLD) & (g > _WHITE_THRESHOLD) & (b > _WHITE_THRESHOLD)
    if not mask.any():
        return None

    labeled, num_components = ndimage.label(mask)
    if num_components == 0:
        return None

    sizes = ndimage.sum(mask, labeled, range(1, num_components + 1))
    biggest_label = int(np.argmax(sizes)) + 1
    biggest_size = float(sizes[biggest_label - 1])

    ys, xs = np.where(labeled == biggest_label)
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    width, height = x1 - x0, y1 - y0

    if width < 20 or height < 20:
        return None

    radius = min(width, height) / 2

    # A real filled circle's pixel count should land close to pi*r^2. This
    # is what actually rules out ovals, thin rings/borders, or an odd-shaped
    # white splash — the earlier width/height bounding-box check alone
    # can't tell those apart from a genuine circle.
    expected_area = math.pi * (radius ** 2)
    fill_ratio = biggest_size / expected_area if expected_area else 0
    if fill_ratio < 0.85 or fill_ratio > 1.15:
        return None

    return {
        "cx": (x0 + x1) / 2,
        "cy": (y0 + y1) / 2,
        "radius": radius,
    }


def _make_circular(img: Image.Image, size: int) -> Image.Image:
    """Center-crops to a square, resizes to `size`x`size`, and masks it
    into a circle (RGBA, transparent outside the circle)."""
    img = img.convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _placeholder_avatar(name: str, size: int) -> Image.Image:
    """Used when the user has no Telegram profile photo (or it couldn't be
    fetched) — a plain colored circle with their first initial, so the
    banner still shows something in the frame instead of a blank hole."""
    colors = ["#7C3AED", "#2563EB", "#DC2626", "#059669", "#D97706", "#DB2777"]
    safe_name = name or "?"
    initial = safe_name.strip()[:1].upper() or "?"
    color = colors[sum(map(ord, safe_name)) % len(colors)]

    img = Image.new("RGBA", (size, size), color)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(size * 0.5))
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), initial, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), initial, fill="white", font=font)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


async def build_profile_banner(user_id: int, first_name: str) -> Optional[BytesIO]:
    """Returns a ready-to-send JPEG (the default banner with the user's own
    Telegram profile picture composited into its circle), or None if there's
    no default banner set / its file is missing — callers should fall back
    to the plain profile photo/text in that case."""
    db = load_db()
    default_id = db.get("settings", {}).get("default_banner_id")
    if not default_id:
        return None

    banner_meta = db.get("banners", {}).get(default_id)
    if not banner_meta or not os.path.exists(banner_meta.get("file_path", "")):
        return None

    try:
        banner = Image.open(banner_meta["file_path"]).convert("RGBA")
    except Exception:
        return None

    circle = banner_meta["circle"]
    size = max(1, int(circle["radius"] * 2))

    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            buf = await bot.download(photos.photos[0][-1].file_id)
            circular_pfp = _make_circular(Image.open(buf), size)
        else:
            circular_pfp = _placeholder_avatar(first_name, size)
    except Exception:
        circular_pfp = _placeholder_avatar(first_name, size)

    paste_x = int(circle["cx"] - circle["radius"])
    paste_y = int(circle["cy"] - circle["radius"])
    banner.paste(circular_pfp, (paste_x, paste_y), circular_pfp)

    out = BytesIO()
    banner.convert("RGB").save(out, format="JPEG", quality=92)
    out.seek(0)
    return out


# ==========================================
# /ab <name> — ADD BANNER (ADMIN ONLY, reply to a photo)
# ==========================================
@main_router.message(Command("ab"))
async def add_banner_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply(
            "<b>Usage:</b> reply to a photo with <code>/ab &lt;name&gt;</code>\n"
            "The photo needs one plain white circular area — that's where each "
            "user's own profile picture gets composited in.",
            parse_mode=ParseMode.HTML
        )
        return

    name = (command.args or "").strip()
    if not name:
        await message.reply("<b>Usage:</b> reply to a photo with <code>/ab &lt;name&gt;</code>", parse_mode=ParseMode.HTML)
        return

    db = load_db()
    banner_id = _next_banner_id(db)
    file_path = os.path.join(BANNERS_DIR, f"{banner_id}.png")

    file_id = message.reply_to_message.photo[-1].file_id
    await bot.download(file_id, destination=file_path)

    try:
        circle = _detect_circle(Image.open(file_path))
    except Exception:
        circle = None

    if not circle:
        try:
            os.remove(file_path)
        except Exception:
            pass
        await message.reply(
            "Couldn't find a white circle placeholder in that image.\n"
            "Make sure it has one solid, roughly-circular white area for the profile picture to go into.",
            parse_mode=ParseMode.HTML
        )
        return

    banners = db.setdefault("banners", {})
    banners[banner_id] = {
        "name": name,
        "file_path": file_path,
        "circle": circle,
        "added_by": str(message.from_user.id),
        "added_at": int(time.time()),
    }
    save_db()

    await message.reply(
        f"<b>Banner added</b>\n"
        f"ID: <code>{banner_id}</code>\n"
        f"Name: {name}\n"
        f"Detected circle: ~{int(circle['radius'] * 2)}px diameter\n\n"
        f"Use <code>/set_default {banner_id}</code> to make it active for everyone's /profile.",
        parse_mode=ParseMode.HTML
    )


# ==========================================
# /rb <banner_id> — REMOVE BANNER (ADMIN ONLY)
# ==========================================
@main_router.message(Command("rb"))
async def remove_banner_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return

    if not command.args or not command.args.strip():
        await message.reply("<b>Usage:</b> <code>/rb &lt;banner_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    banner_id = command.args.strip()
    db = load_db()
    banners = db.get("banners", {})

    if banner_id not in banners:
        await message.reply("No banner with that ID. Use /lbanner to see available IDs.", parse_mode=ParseMode.HTML)
        return

    file_path = banners[banner_id].get("file_path")
    name = banners[banner_id].get("name", "Unnamed")
    del banners[banner_id]

    was_default = db.get("settings", {}).get("default_banner_id") == banner_id
    if was_default:
        db.setdefault("settings", {})["default_banner_id"] = None

    save_db()

    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

    note = ""
    if was_default:
        note = "\n\n<i>This was the default banner — /profile will show plain profile photos again until a new default is set.</i>"
    await message.reply(f"Removed banner <b>{name}</b> (<code>{banner_id}</code>).{note}", parse_mode=ParseMode.HTML)


# ==========================================
# /lbanner — LIST ALL BANNERS (ADMIN ONLY)
# ==========================================
@main_router.message(Command("lbanner"))
async def list_banners_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    db = load_db()
    banners = db.get("banners", {})
    default_id = db.get("settings", {}).get("default_banner_id")

    if not banners:
        await message.reply(
            "No banners added yet. Reply to a photo with <code>/ab &lt;name&gt;</code> to add one.",
            parse_mode=ParseMode.HTML
        )
        return

    lines = ["<b>「 BANNER LIST 」</b>", "━━━━━━━━━━━━━━━━━"]
    for bid, meta in sorted(banners.items(), key=lambda x: int(x[0])):
        marker = " — <b>default</b>" if bid == default_id else ""
        lines.append(f"<code>{bid}</code> ┊ {meta.get('name', 'Unnamed')}{marker}")
    await message.reply("\n".join(lines), parse_mode=ParseMode.HTML)


# ==========================================
# /set_default <banner_id> — SET GLOBAL DEFAULT BANNER (ADMIN ONLY)
# ==========================================
@main_router.message(Command("set_default"))
async def set_default_banner_cmd(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return

    if not command.args or not command.args.strip():
        await message.reply("<b>Usage:</b> <code>/set_default &lt;banner_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    banner_id = command.args.strip()
    db = load_db()
    banners = db.get("banners", {})

    if banner_id not in banners:
        await message.reply("No banner with that ID. Use /lbanner to see available IDs.", parse_mode=ParseMode.HTML)
        return

    db.setdefault("settings", {})["default_banner_id"] = banner_id
    save_db()

    await message.reply(
        f"Default banner set to <b>{banners[banner_id].get('name', 'Unnamed')}</b> (<code>{banner_id}</code>) for all users.",
        parse_mode=ParseMode.HTML
    )
