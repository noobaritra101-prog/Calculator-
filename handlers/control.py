from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import AUCTION_CHANNEL_ID
import db

async def get_control_kb():
    sub_status = await db.get_setting("submissions")
    auc_status = await db.get_setting("auction")
    
    sub_text = "🟢 Submissions: ON" if sub_status == "on" else "🔴 Submissions: OFF"
    auc_text = "🟢 Auction: ON" if auc_status == "on" else "🔴 Auction: OFF"
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(sub_text, callback_data="toggle_submissions", api_kwargs={"style": "primary"})],
        [InlineKeyboardButton(auc_text, callback_data="toggle_auction", api_kwargs={"style": "primary"})]
    ])

async def cauc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_bot_admin(update.effective_user.id):
        await update.message.reply_text("⛔ You do not have admin permissions to use this command.")
        return
        
    kb = await get_control_kb()
    await update.message.reply_text("⚙️ **Auction Control Panel**", reply_markup=kb, parse_mode="Markdown")

async def close_all_auctions(context: ContextTypes.DEFAULT_TYPE):
    """Marks all active auctions as sold, notifies users, and removes from DB."""
    active_items = await db.get_all_active()
    for item in active_items:
        if item['current_bid'] > 0:
            # 1. Update Channel Post
            sold_text = f"🎉 **SOLD to {item['bidder_name']} for {item['current_bid']} {item['currency']}!**\n\nItem: ｢ {item['name']} 」"
            try:
                if item.get('photo_id'):
                    await context.bot.edit_message_caption(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], caption=sold_text, parse_mode="Markdown")
                else:
                    await context.bot.edit_message_text(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], text=sold_text, parse_mode="Markdown")
            except Exception: pass
            
            # 2. Notify Buyer
            try:
                await context.bot.send_message(item['bidder_id'], f"🎉 **Congratulations!** You won the auction for ｢ {item['name']} 」 with a bid of {item['current_bid']} {item['currency']}!")
            except Exception: pass
            
            # 3. Notify Seller
            try:
                await context.bot.send_message(item['seller_id'], f"💰 **Item Sold!** Your auction for ｢ {item['name']} 」 just sold to {item['bidder_name']} for {item['current_bid']} {item['currency']}!")
            except Exception: pass
        else:
            # Ended with no bids
            ended_text = f"❌ **AUCTION ENDED (No Bids)**\n\nItem: ｢ {item['name']} 」"
            try:
                if item.get('photo_id'):
                    await context.bot.edit_message_caption(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], caption=ended_text, parse_mode="Markdown")
            except Exception: pass
            try:
                await context.bot.send_message(item['seller_id'], f"⚠️ Your auction for ｢ {item['name']} 」 ended, but unfortunately received no bids.")
            except Exception: pass
        
        # Remove from Active DB
        await db.delete_active(item['item_id'])

async def cauc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if not await db.is_bot_admin(update.effective_user.id):
        await query.answer("⛔ Access denied.", show_alert=True)
        return
        
    action = query.data.split('_')[1] 
    current_status = await db.get_setting(action)
    new_status = "off" if current_status == "on" else "on"
    
    await db.set_setting(action, new_status)
    
    # Custom Logic: If Auction turns ON, Submission turns ON automatically
    if action == "auction" and new_status == "on":
        await db.set_setting("submissions", "on")
        
    # Custom Logic: If Auction turns OFF, process all active items as SOLD
    if action == "auction" and new_status == "off":
        await query.answer("Closing all active auctions... This may take a moment.", show_alert=True)
        await close_all_auctions(context)
    
    kb = await get_control_kb()
    await query.edit_message_reply_markup(reply_markup=kb)
    await query.answer(f"{action.capitalize()} turned {new_status.upper()}")
