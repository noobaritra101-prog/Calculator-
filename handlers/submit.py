import re
import uuid
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from config import ADMIN_GROUP_ID
import db
from handlers.admin_auction import generate_auction_text

CHOOSING_CATEGORY, WAITING_BASIC, WAITING_MORE, CHOOSING_CURRENCY, WAITING_PRICE = range(5)

def get_cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_add", api_kwargs={"style": "danger"})]])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        await db.register_user(update.effective_user.id)

    frames = [
        "▱▱▱▱▱▱▱▱▱▱ 0%\n⚙️ Initializing Shrane Auction System...",
        "▰▰▱▱▱▱▱▱▱▱ 20%\n🔐 Securing bidding servers...",
        "▰▰▰▰▱▱▱▱▱▱ 40%\n📦 Loading marketplace data...",
        "▰▰▰▰▰▰▱▱▱▱ 60%\n💰 Syncing auction rooms...",
        "▰▰▰▰▰▰▰▰▱▱ 80%\n🌀 Preparing live bidding...",
        "▰▰▰▰▰▰▰▰▰▰ 100%\n✨ System Ready!"
    ]
    
    message = await update.message.reply_text(frames[0])
    for frame in frames[1:]:
        await asyncio.sleep(0.6)
        try:
            await message.edit_text(frame)
        except Exception:
            pass 
            
    await asyncio.sleep(0.6)
    
    user_name = update.effective_user.first_name
    final_text = f"Hey {user_name},\nWᴇʟᴄᴏᴍᴇ Tᴏ sʜʀᴀɴᴇ Aᴜᴄᴛɪᴏɴ Bᴏᴛ"
    
    from config import CHANNEL_LINK, GROUP_LINK
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton("👥 Group", url=GROUP_LINK, api_kwargs={"style": "primary"}),
            InlineKeyboardButton("📢 Channel", url=CHANNEL_LINK, api_kwargs={"style": "primary"})
        ]
    ])
    await message.edit_text(final_text, reply_markup=keyboard)

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        bot_username = context.bot.username
        dm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("💬 Go to DMs", url=f"https://t.me/{bot_username}")]
        ])
        await update.message.reply_text(
            "⛔ Pʟᴇᴀsᴇ ᴜsᴇ ᴛʜᴇ `/ᴀᴅᴅ` ᴄᴏᴍᴍᴀɴᴅ ɪɴ ᴍʏ Dɪʀᴇᴄᴛ Mᴇssᴀɢᴇs (Dᴍs)!", 
            parse_mode="Markdown",
            reply_markup=dm_keyboard
        )
        return ConversationHandler.END

    if await db.get_setting("submissions") == "off":
        await update.message.reply_text("⛔ Aᴜᴄᴛɪᴏɴ sᴜʙᴍɪssɪᴏɴs ᴀʀᴇ ᴄᴜʀʀᴇɴᴛʟʏ ᴘᴀᴜsᴇᴅ.\nPʟᴇᴀsᴇ ᴄʜᴇᴄᴋ ʙᴀᴄᴋ ʟᴀᴛᴇʀ!")
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton("Slug", callback_data="add_slug", api_kwargs={"style": "primary"}),
            InlineKeyboardButton("Mods", callback_data="add_mods", api_kwargs={"style": "primary"})
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_add", api_kwargs={"style": "danger"})]
    ])
    await update.message.reply_text("Choose what to add:", reply_markup=keyboard)
    return CHOOSING_CATEGORY

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "add_mods":
        await query.edit_message_text("Mods are currently in maintenance. 🛠️")
        return ConversationHandler.END
    
    await query.edit_message_text("Pʟᴇᴀsᴇ ғᴏʀᴡᴀʀᴅ ᴛʜᴇ Bᴀsɪᴄ ɪɴғᴏ ᴘᴀɢᴇ ғʀᴏᴍ @Slugterra_robot", reply_markup=get_cancel_kb())
    context.user_data['item_id'] = str(uuid.uuid4())[:8]
    return WAITING_BASIC

async def receive_basic_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.caption or update.message.text
    if not text or '｢' not in text:
        await update.message.reply_text("Please forward a valid basic info page.", reply_markup=get_cancel_kb())
        return WAITING_BASIC

    try:
        name = re.search(r'｢\s*(.*?)\s*」', text).group(1)
        slug_type = re.search(r'Type\s*:\s*(\[.*?\])', text).group(1)
        level = re.search(r'Level\s*:\s*(\d+)', text).group(1)
        photo_id = update.message.photo[-1].file_id if update.message.photo else None
        
        context.user_data.update({'name': name, 'type': slug_type, 'level': level, 'photo_id': photo_id})
        await update.message.reply_text("Gʀᴇᴀᴛ! Nᴏᴡ ғᴏʀᴡᴀʀᴅ ᴛʜᴇ Mᴏʀᴇ ɪɴғᴏ/Sᴛᴀᴛs ᴘᴀɢᴇ ғʀᴏᴍ @Slugterra_robot.", reply_markup=get_cancel_kb())
        return WAITING_MORE
        
    except AttributeError as e:
        await update.message.reply_text("Could not parse the info. Make sure the format matches.", reply_markup=get_cancel_kb())
        return WAITING_BASIC

async def receive_more_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.caption or update.message.text
    context.user_data['more_info'] = text 
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🥇 Nuggets", callback_data="curr_Nuggets", api_kwargs={"style": "primary"})],
        [InlineKeyboardButton("💎 Gems", callback_data="curr_Gems", api_kwargs={"style": "primary"})],
        [InlineKeyboardButton("💰 Coins", callback_data="curr_Coins", api_kwargs={"style": "primary"})],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_add", api_kwargs={"style": "danger"})]
    ])
    await update.message.reply_text("Select the currency for the auction:", reply_markup=keyboard)
    return CHOOSING_CURRENCY

async def currency_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    currency = query.data.split('_')[1]
    context.user_data['currency'] = currency
    
    curr_display = currency.replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
    
    await query.edit_message_text(f"Cᴜʀʀᴇɴᴄʏ sᴇᴛ ᴛᴏ {curr_display}.\nWʜᴀᴛ ɪs ʏᴏᴜʀ ʙᴀsᴇ ᴘʀɪᴄᴇ?", reply_markup=get_cancel_kb())
    return WAITING_PRICE

async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = update.message.text.strip()
    if not price.isdigit():
        await update.message.reply_text("Please enter a valid number.", reply_markup=get_cancel_kb())
        return WAITING_PRICE
    
    item_id = context.user_data['item_id']
    item_data = {
        'item_id': item_id, 
        'seller_name': update.effective_user.full_name, 
        'seller_username': update.effective_user.username or "None", 
        'seller_id': update.effective_user.id,
        'name': context.user_data['name'], 
        'type': context.user_data['type'], 
        'level': context.user_data['level'],
        'more_info': context.user_data['more_info'], 
        'currency': context.user_data['currency'], 
        'base_price': int(price),
        'photo_id': context.user_data.get('photo_id'), 
        'current_bid': 0, 
        'bidder_name': "None", 
        'bidder_username': "None", 
        'bidder_id': None
    }
    
    await db.add_pending(item_id, item_data)
    
    admin_review = {'status': 'pending'}
    full_auction_text = generate_auction_text(item_data, admin_review)
    admin_text = f"🚨 <b>NEW AUCTION APPROVAL</b> 🚨\n\n{full_auction_text}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ Accept", callback_data=f"admin_accept_{item_id}", api_kwargs={"style": "success"}),
         InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{item_id}", api_kwargs={"style": "danger"})]
    ])
    
    if item_data['photo_id']:
        await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=item_data['photo_id'], caption=admin_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=admin_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    await update.message.reply_text("Yᴏᴜʀ ᴀᴜᴄᴛɪᴏɴ ʜᴀs ʙᴇᴇɴ sᴜʙᴍɪᴛᴛᴇᴅ ғᴏʀ ᴀᴅᴍɪɴ ᴀᴘᴘʀᴏᴠᴀʟ!\nYᴏᴜ'ʟʟ ʙᴇ ɴᴏᴛɪғɪᴇᴅ sᴏᴏɴ ⚡")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Submission cancelled.")
    else:
        await update.message.reply_text("❌ Submission cancelled.")
    context.user_data.clear()
    return ConversationHandler.END
