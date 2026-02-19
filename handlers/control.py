import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
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
    active_items = await db.get_all_active()
    for item in active_items:
        if item['current_bid'] > 0:
            buyer_link = f"<a href='tg://user?id={item['bidder_id']}'>{html.escape(item['bidder_name'])}</a>"
            seller_link = f"<a href='tg://user?id={item['seller_id']}'>{html.escape(item['seller_name'])}</a>"
            
            # Map currency text for the Sold message
            curr_disp = item['currency'].replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
            
            # Rebuilding the full post with the customized bottom message
            sold_channel_text = f"""<b>Name : - ｢ {html.escape(item['name'])} ☣ ☣」</b>

<blockquote>Type : {html.escape(item['type'])}
Level : {item['level']}</blockquote>

<blockquote>More info :-
{html.escape(item['more_info'])}</blockquote>

<blockquote>Seller Name - {seller_link}
Seller Id - <code>{item['seller_id']}</code>
Base price - {item['base_price']:,.1f} {item['currency']}
Item id - <code>{item['item_id']}</code></blockquote>

🎉 Sᴏʟᴅ ᴛᴏ {buyer_link} ғᴏʀ {item['current_bid']:,.1f} {curr_disp}!"""

            try:
                if item.get('photo_id'):
                    await context.bot.edit_message_caption(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], caption=sold_channel_text, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.edit_message_text(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], text=sold_channel_text, parse_mode=ParseMode.HTML)
            except Exception: pass
            
            buyer_username = item.get('bidder_username', 'Unknown')
            seller_username = item.get('seller_username', 'Unknown')

            buyer_text = f"""<b>🎉 Auction Won!
Congratulations! You have successfully won the auction for</b>
<blockquote>✨ ｢ {html.escape(item['name'])} 」</blockquote>

<blockquote>💰 Winning Bid: {item['current_bid']:,.1f} {item['currency']}
━━━━━━━━━━━━━━━━
👤 Seller Details
• Name: {html.escape(item['seller_name'])}
• Username: @{seller_username}
• ID: <code>{item['seller_id']}</code>
━━━━━━━━━━━━━━━━</blockquote>

<b>Thank you for using Shrane Auction System ⚡</b>"""

            try:
                await context.bot.send_message(item['bidder_id'], text=buyer_text, parse_mode=ParseMode.HTML)
            except Exception: pass
            
            seller_text = f"""<b>📦 Item Sold Successfully!</b>
<blockquote>Congratulations! Your item has been sold in the auction 🎉</blockquote>
<blockquote>✨ Item: ｢ {html.escape(item['name'])} 」</blockquote>
<b>💰 Final Selling Price: {item['current_bid']:,.1f} {item['currency']}</b>
<blockquote>━━━━━━━━━━━━━━━━
🏆 Winning Buyer Details
• Name: {html.escape(item['bidder_name'])}
• Username: @{buyer_username}
• ID: <code>{item['bidder_id']}</code>
━━━━━━━━━━━━━━━━</blockquote>

<b>Thank you for trading on Shrane Auction System ⚡</b>"""

            try:
                await context.bot.send_message(item['seller_id'], text=seller_text, parse_mode=ParseMode.HTML)
            except Exception: pass
            
        else:
            ended_text = f"❌ **AUCTION ENDED (No Bids)**\n\nItem: ｢ {item['name']} 」"
            try:
                if item.get('photo_id'):
                    await context.bot.edit_message_caption(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], caption=ended_text, parse_mode="Markdown")
                else:
                    await context.bot.edit_message_text(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], text=ended_text, parse_mode="Markdown")
            except Exception: pass
            try:
                await context.bot.send_message(item['seller_id'], f"⚠️ Your auction for ｢ {item['name']} 」 ended, but unfortunately received no bids.")
            except Exception: pass
        
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
    
    if action == "auction" and new_status == "on":
        await db.set_setting("submissions", "on")
        
    if action == "auction" and new_status == "off":
        await query.answer("Closing all active auctions... This may take a moment.", show_alert=True)
        await close_all_auctions(context)
    
    kb = await get_control_kb()
    await query.edit_message_reply_markup(reply_markup=kb)
    await query.answer(f"{action.capitalize()} turned {new_status.upper()}")
