from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import AUCTION_CHANNEL_ID
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
    # ADDED AWAIT
    all_active = await db.get_all_active()
    
    if filter_type in ["Nuggets", "Gems", "Coins"]:
        filtered_items = [i for i in all_active if i['currency'] == filter_type]
    else:
        filtered_items = all_active 
        
    if not filtered_items:
        text = f"📭 No active auctions currently for **{filter_type}**.\nSelect another category:"
        await query.edit_message_text(text, reply_markup=get_items_keyboard())
        return

    clean_channel_id = str(AUCTION_CHANNEL_ID).replace('-100', '')

    text = f"📋 **Live Auctions: {filter_type}**\n\n"
    for idx, item in enumerate(filtered_items, 1):
        highest_bid = f"{item['current_bid']} {item['currency']}" if item['current_bid'] > 0 else f"{item['base_price']} {item['currency']} (Base)"
        post_link = f"https://t.me/c/{clean_channel_id}/{item.get('channel_message_id', '')}"
        
        text += f"{idx}. ｢ {item['name']} 」 - {highest_bid}\n"
        text += f"└ [👉 Click here to go to Auction Post]({post_link})\n\n"
        
    await query.edit_message_text(text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=get_items_keyboard())

async def myadd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # ADDED AWAITS
    pending = await db.get_user_pending(user_id)
    active = await db.get_user_active(user_id)
    
    if not pending and not active:
        await update.message.reply_text("You haven't submitted any items yet! Use /add to get started.")
        return
        
    text = f"📦 **Your Items ({update.effective_user.full_name})**\n\n"
    
    if active:
        clean_channel_id = str(AUCTION_CHANNEL_ID).replace('-100', '')
        text += "🟢 **Active Live Auctions:**\n"
        for item in active:
            highest_bid = f"{item['current_bid']} {item['currency']}" if item['current_bid'] > 0 else "No bids yet"
            post_link = f"https://t.me/c/{clean_channel_id}/{item.get('channel_message_id', '')}"
            text += f"• ｢ {item['name']} 」- Highest Bid: {highest_bid} [🔗 Link]({post_link})\n"
        text += "\n"
        
    if pending:
        text += "⏳ **Pending Admin Approval:**\n"
        for item in pending:
            text += f"• ｢ {item['name']} 」- Base: {item['base_price']} {item['currency']}\n"
            
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
