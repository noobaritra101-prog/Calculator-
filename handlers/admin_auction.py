import re
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from config import AUCTION_CHANNEL_ID, BID_LOG_GROUP_ID
import db

def generate_auction_text(item):
    current_bid_display = f"{item['current_bid']} {item['currency']}" if item['current_bid'] > 0 else "None"
    
    # Create clickable profile links
    seller_link = f"<a href='tg://user?id={item['seller_id']}'>{html.escape(item['seller_name'])}</a>"
    if item.get('bidder_id'):
        bidder_link = f"<a href='tg://user?id={item['bidder_id']}'>{html.escape(item['bidder_name'])}</a>"
    else:
        bidder_link = "None"

    return f"""Name : - ｢ {html.escape(item['name'])} ☣」

Type : {html.escape(item['type'])}
Level : {item['level']}

More info :-
{html.escape(item['more_info'])}

Seller Name - {seller_link}
Seller Id - <code>{item['seller_id']}</code>
Base price - {item['base_price']} {item['currency']}
Item id - <code>{item['item_id']}</code>

Current Bidder - {current_bid_display}
By - {bidder_link}
"""

async def admin_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # 🛡️ SECURITY 1: Only Bot Admins can Accept/Reject
    if not db.is_bot_admin(update.effective_user.id):
        await query.answer("⛔ Access denied. Only Bot Admins can review items.", show_alert=True)
        return

    await query.answer()
    
    action, item_id = query.data.split('_')[1], query.data.split('_')[2]
    item = db.get_pending(item_id)
    
    # Smart Check for Photos vs Text
    async def edit_msg(new_text):
        if query.message.photo:
            await query.edit_message_caption(caption=new_text)
        else:
            await query.edit_message_text(text=new_text)
    
    if not item:
        await edit_msg("⚠️ This item is no longer pending.")
        return

    try:
        if action == "reject":
            await context.bot.send_message(item['seller_id'], f"Sorry, your auction for ｢ {item['name']} 」 was rejected.")
            await edit_msg(f"❌ Rejected ｢ {item['name']} 」 by Admin.")
            db.delete_pending(item_id)
            
        elif action == "accept":
            auction_text = generate_auction_text(item)
            
            if item['photo_id']:
                msg = await context.bot.send_photo(chat_id=AUCTION_CHANNEL_ID, photo=item['photo_id'], caption=auction_text, parse_mode=ParseMode.HTML)
            else:
                msg = await context.bot.send_message(chat_id=AUCTION_CHANNEL_ID, text=auction_text, parse_mode=ParseMode.HTML)
            
            item['channel_message_id'] = msg.message_id
            db.add_active(item_id, item)
            db.delete_pending(item_id)
            
            await context.bot.send_message(item['seller_id'], f"Great news! Your auction for ｢ {item['name']} 」 was accepted and is live.")
            await edit_msg(f"✅ Accepted ｢ {item['name']} 」. Sent to channel.")
            
    except Exception as e:
        print(f"Error processing item: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ System Error trying to process this item: {e}")

async def bid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Global Bidding Pause Check
    if db.get_setting("bidding") == "off":
        await update.message.reply_text("⛔ Bidding is currently paused by the admins. Please try again later.")
        return
        
    if not update.message.reply_to_message:
        await update.message.reply_text("Please reply to an active auction post to bid.")
        return
    
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Format: /bid {amount}")
        return
        
    bid_amount = int(args[0])
    replied_text = update.message.reply_to_message.caption or update.message.reply_to_message.text
    
    if not replied_text:
        await update.message.reply_text("Could not read the post you replied to.")
        return

    # Extract ID (works whether it has HTML tags or not)
    match = re.search(r'Item id - ([\w-]+)', replied_text.replace('<code>', '').replace('</code>', ''))
    if not match:
        await update.message.reply_text("Could not find an Item ID in the post you replied to.")
        return
        
    item_id = match.group(1)
    item = db.get_active(item_id)
    
    if not item:
        await update.message.reply_text("This auction is no longer active.")
        return

    # 🛡️ SECURITY 2: Block users from bidding on their own item
    if update.effective_user.id == item['seller_id']:
        await update.message.reply_text("⛔ You cannot bid on your own item!")
        return
        
    if bid_amount <= item['current_bid'] or bid_amount < item['base_price']:
        await update.message.reply_text(f"Bid too low. Must be higher than {max(item['current_bid'], item['base_price'])} {item['currency']}.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ Confirm Bid", callback_data=f"confirmbid_{item_id}_{bid_amount}", api_kwargs={"style": "success"}),
         InlineKeyboardButton("❌ Cancel", callback_data="cancelbid", api_kwargs={"style": "danger"})]
    ])
    await update.message.reply_text(f"Are you sure you want to bid {bid_amount} {item['currency']} for ｢ {item['name']} 」?", reply_markup=keyboard)

async def bid_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancelbid":
        await query.edit_message_text("Bid cancelled.")
        return
        
    _, item_id, amount_str = query.data.split('_')
    bid_amount = int(amount_str)
    item = db.get_active(item_id)
    
    if not item:
        await query.edit_message_text("Bid failed. The auction might have ended.")
        return

    # 🛡️ SECURITY 2 (Double Check): Ensure self-bidder didn't click confirm somehow
    if update.effective_user.id == item['seller_id']:
        await query.edit_message_text("⛔ You cannot bid on your own item!")
        return

    if bid_amount <= item['current_bid']:
        await query.edit_message_text("Bid failed. A higher bid was already placed.")
        return

    item['current_bid'] = bid_amount
    item['bidder_name'] = update.effective_user.full_name
    item['bidder_id'] = update.effective_user.id
    
    db.update_active(item_id, item)
    
    new_text = generate_auction_text(item)
    try:
        await context.bot.edit_message_caption(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], caption=new_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"Error updating channel post: {e}")
        
    await query.edit_message_text(f"✅ Bid successful! You are currently the highest bidder with {bid_amount} {item['currency']}.")
    
    # Log the successful bid
    bidder_link = f"<a href='tg://user?id={item['bidder_id']}'>{html.escape(item['bidder_name'])}</a>"
    log_text = (
        f"📝 <b>New Bid Log</b>\n"
        f"Item: ｢ {html.escape(item['name'])} 」(ID: <code>{item_id}</code>)\n"
        f"Amount: {bid_amount} {item['currency']}\n"
        f"Bidder: {bidder_link} (<code>{item['bidder_id']}</code>)"
    )
    try:
        await context.bot.send_message(chat_id=BID_LOG_GROUP_ID, text=log_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"Could not send to Bid Log Group: {e}")
