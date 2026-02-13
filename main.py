import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# Replace with your actual Bot Token
TOKEN = "8368723938:AAGjgK3u0mkmcLw8D7Az511d29S1bXRm86Y"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    # Using Custom Emoji in text message [00:02:15]
    # Note: Replace '54321...' with a real Custom Emoji ID from @get_emoji_id_robot
    text = (
        "Welcome to the colorful bot! "
        "<tg-emoji emoji-id='5368324170671202286'>✨</tg-emoji>\n\n"
        "Check out these new button styles below:"
    )

    # 1. Reply Keyboard with Colors [00:04:58]
    reply_builder = ReplyKeyboardBuilder()
    reply_builder.row(
        types.KeyboardButton(text="Primary (Blue)", style="primary"),
        types.KeyboardButton(text="Success (Green)", style="success"),
        types.KeyboardButton(text="Danger (Red)", style="danger")
    )

    await message.answer(
        text, 
        parse_mode=ParseMode.HTML, 
        reply_markup=reply_builder.as_markup(resize_keyboard=True)
    )

@dp.message(F.text == "Primary (Blue)")
async def show_inline(message: types.Message):
    # 2. Inline Keyboard with Colors and Icons [00:01:27]
    inline_builder = InlineKeyboardBuilder()
    
    inline_builder.row(
        types.InlineKeyboardButton(
            text="Danger Style", 
            callback_data="btn_1", 
            style="danger"
        ),
        types.InlineKeyboardButton(
            text="Success + Icon", 
            callback_data="btn_2", 
            style="success",
            # Icon always appears at the beginning of the button text [00:04:05]
            icon_custom_emoji_id="54321" # Replace with real ID
        )
    )

    await message.answer(
        "Here are the inline button styles:", 
        reply_markup=inline_builder.as_markup()
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
