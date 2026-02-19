from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import db

def get_items_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🐌 All Slugs", callback_data="filter_Slugs", api_kwargs={"style": "primary"})],
        [InlineKeyboardButton("🥇 Nuggets", callback_data="filter_Nuggets", api_kwargs={"style": "primary"}),
         InlineKeyboardButton("💎 Gems", callback_data="filter_Gems", api_kwargs={"style": "primary"}),
         InlineKeyboardButton("💰 Coins", callback_data="filter_Coins", api_kwargs={"style": "primary"})]
    ])

async def items_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📋 **Live Auctions**\nSelect a category to view items:", reply_markup=get_items_keyboard())

async def items_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    filter_type = query.data.split('_')[1]
    all_active = db.get_all_active()
    
    # Filter items
    if filter_type in ["Nuggets", "Gems", "Coins"]:
        filtered_items = [i for i in all_active if i['currency'] == filter_type]
    else:
        filtered_items = all_active # Default to all Slugs
        
    if not filtered_items:
        text = f"📭 No active auctions currently for **{filter_type}**.\nSelect another category:"
        await query.edit_message_text(text, reply_markup=get_items_keyboard())
        return

    # Generate list text
    text = f"📋 **Live Auctions: {filter_type}**\n\n"
    for idx, item in enumerate(filtered_items, 1):
        highest_bid = f"{item['current_bid']} {item['currency']}" if item['current_bid'] > 0 else f"{item['base_price']} {item['currency']} (Base)"
        text += f"{idx}. ｢ {item['name']} 」 - {highest_bid}\n"
        text += f"└ *ID:* `{item['item_id']}`\n\n"
        
    text += "Use `/bid <amount>` while replying to the item's post in the channel to place a bid!"
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_items_keyboard())
