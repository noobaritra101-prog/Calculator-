import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
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
    await update.message.reply_text(
        "📋 Lɪᴠᴇ Aᴜᴄᴛɪᴏɴs\nSᴇʟᴇᴄᴛ ᴀ ᴄᴀᴛᴇɢᴏʀʏ ᴛᴏ ᴠɪᴇᴡ ɪᴛᴇᴍs:", 
        reply_markup=get_items_keyboard()
    )

async def items_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    filter_type = query.data.split('_')[1]
    all_active = await db.get_all_active()
    
    if filter_type in ["Nuggets", "Gems", "Coins"]:
        filtered_items = [i for i in all_active if i['currency'] == filter_type]
    else:
        filtered_items = all_active 
        
    # Map regular text to your custom small-caps font
    cat_display = filter_type.replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs').replace('Slugs', 'Aʟʟ Sʟᴜɢs')

    if not filtered_items:
        text = f"📭 No active auctions currently for <b>{cat_display}</b>.\nSelect another category:"
        await query.edit_message_text(text, reply_markup=get_items_keyboard(), parse_mode=ParseMode.HTML)
        return

    # Clean the channel ID for deep linking
    clean_channel_id = str(AUCTION_CHANNEL_ID).replace('-100', '')

    text = f"<b>📋 Lɪᴠᴇ Aᴜᴄᴛɪᴏɴs</b>\nCᴀᴛᴀɢᴏʀʏ : {cat_display}\n\n"
    
    # Format the list with the embedded URL inside the Item Name
    for idx, item in enumerate(filtered_items, 1):
        post_link = f"https://t.me/c/{clean_channel_id}/{item.get('channel_message_id', '')}"
        
        # New layout: ｢ Name + URL ｣ - Type
        text += f"{idx}. <a href='{post_link}'>｢ {html.escape(item['name'])} 」</a> - {html.escape(item['type'])}\n"
        
    await query.edit_message_text(
        text, 
        parse_mode=ParseMode.HTML, 
        disable_web_page_preview=True, 
        reply_markup=get_items_keyboard()
    )

async def myadd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    pending = await db.get_user_pending(user_id)
    active = await db.get_user_active(user_id)
    
    if not pending and not active:
        await update.message.reply_text("You haven't submitted any items yet! Use /add to get started.")
        return
        
    text = f"📦 <b>Your Items ({html.escape(update.effective_user.full_name)})</b>\n\n"
    
    if active:
        clean_channel_id = str(AUCTION_CHANNEL_ID).replace('-100', '')
        text += "🟢 <b>Active Live Auctions:</b>\n"
        for item in active:
            highest_bid = f"{item['current_bid']:,.1f} {item['currency']}" if item['current_bid'] > 0 else "No bids yet"
            post_link = f"https://t.me/c/{clean_channel_id}/{item.get('channel_message_id', '')}"
            text += f"• ｢ {html.escape(item['name'])} 」- Highest Bid: {highest_bid} <a href='{post_link}'>[🔗 Link]</a>\n"
        text += "\n"
        
    if pending:
        text += "⏳ <b>Pending Admin Approval:</b>\n"
        for item in pending:
            text += f"• ｢ {html.escape(item['name'])} 」- Base: {item['base_price']:,.1f} {item['currency']}\n"
            
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
