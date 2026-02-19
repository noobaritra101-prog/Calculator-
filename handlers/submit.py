import re
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from config import WELCOME_ANIMATION_URL, ADMIN_GROUP_ID
import db

CHOOSING_CATEGORY, WAITING_BASIC, WAITING_MORE, CHOOSING_CURRENCY, WAITING_PRICE = range(5)

def get_cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_add", api_kwargs={"style": "danger"})]])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_animation(
        chat_id=update.effective_chat.id,
        animation=WELCOME_ANIMATION_URL,
        caption="Welcome to the Slugterra Auction Bot!\nUse /add to submit an item, or /items to view live auctions."
    )

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    await query.edit_message_text("Please forward the **Basic info page** from @Slugterra_robot:", reply_markup=get_cancel_kb())
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
        await update.message.reply_text("Great! Now forward the **More info/Stats page**.", reply_markup=get_cancel_kb())
        return WAITING_MORE
        
    except AttributeError as e:
        print(f"Parsing Failed: {e}")
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
    await query.edit_message_text(f"Currency set to {currency}. What is your base price? (Send a number)", reply_markup=get_cancel_kb())
    return WAITING_PRICE

async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = update.message.text
    if not price.isdigit():
        await update.message.reply_text("Please enter a valid number.", reply_markup=get_cancel_kb())
        return WAITING_PRICE
    
    item_id = context.user_data['item_id']
    item_data = {
        'item_id': item_id, 'seller_name': update.effective_user.full_name, 'seller_id': update.effective_user.id,
        'name': context.user_data['name'], 'type': context.user_data['type'], 'level': context.user_data['level'],
        'more_info': context.user_data['more_info'], 'currency': context.user_data['currency'], 'base_price': int(price),
        'photo_id': context.user_data.get('photo_id'), 'current_bid': 0, 'bidder_name': "None", 'bidder_id': None
    }
    
    # Save to SQLite Database instead of dictionary
    db.add_pending(item_id, item_data)
    
    admin_text = f"**New Auction Request**\nItem: ｢ {item_data['name']} 」\nSeller: {item_data['seller_name']}\nBase: {price} {item_data['currency']}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ Accept", callback_data=f"admin_accept_{item_id}", api_kwargs={"style": "success"}),
         InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{item_id}", api_kwargs={"style": "danger"})]
    ])
    
    if item_data['photo_id']:
        await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=item_data['photo_id'], caption=admin_text, reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=admin_text, reply_markup=keyboard)

    await update.message.reply_text("Your auction has been submitted for admin approval! You'll be notified soon.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handles both /cancel text command and Inline Cancel button
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Submission cancelled.")
    else:
        await update.message.reply_text("❌ Submission cancelled.")
    context.user_data.clear()
    return ConversationHandler.END
