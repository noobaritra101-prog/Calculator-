# keyboards.py
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def start_keyboard(bot_username):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Add To Group",
                    url=f"https://t.me/{bot_username}?startgroup=true",
                    style="success"   # 🟢 Green
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Support",
                    url="https://t.me/axbsupport",
                    style="primary"   # 🔵 Blue
                ),
                InlineKeyboardButton(
                    text="🏢 HQ",
                    url="https://t.me/your_channel",
                    style="primary"   # 🔵 Blue
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👑 Owner",
                    url="https://t.me/axbowners",
                    style="danger"    # 🔴 Red
                )
            ]
        ]
    )

def stats_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                 text="🔄 Refresh Stats", 
                 callback_data="refresh_stats",
                 style="primary"
                )
            ]
        ]
    )
