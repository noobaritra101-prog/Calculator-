"""
PROFILE BANNER SYSTEM (shared logic)
=====================================
Admin-managed pool of banner template images. Exactly one banner is
"default" at a time, and /profile (in handlers.py) composites the viewing
user's own Telegram profile picture into the round placeholder cut into
that banner, using Pillow. There's no per-user banner choice — everyone's
/profile uses whichever banner an admin has set as default.

This module holds the shared detection/compositing logic and
build_profile_banner() (called from handlers.py's /profile). The admin
commands themselves — /ab, /rb, /lbanner, /set_default — live in
a_handlers.py alongside the rest of the admin toolset, and import the
helpers they need from here rather than duplicating them.

Requires Pillow, numpy, and scipy (`pip install Pillow numpy scipy`).
"""
import os
import math
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
import numpy as np
from scipy import ndimage

from config import bot, load_db

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


def next_banner_id(db: dict) -> str:
    banners = db.get("banners", {})
    existing = [int(k) for k in banners.keys() if k.isdigit()]
    return str(max(existing, default=0) + 1)


def detect_circle(img: Image.Image) -> Optional[dict]:
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
