import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import AUCTION_CHANNEL_ID
from db import pending_items, active_auctions

def generate_auction_text(item):
    current_bid_display = f"{item['current_bid']} {item['currency']}" if item['current_bid'] > 0 else "None"
    return f"""Name : - ｢ {item['name']} ☣」

Type : {item['type']}
Level : {item['level']}

More info :-
{item['more_info']}

Seller Name - {item['seller_name']}
Seller Id - {item['seller_id']}
Base price - {item['base_price']} {item['currency']}
Item id - {item['item_id']}

Current Bidder - {current_bid_display}
By - {item['bidder_name']}
"""

async def admin_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action, item_id = query.data.split('_')[1], query.data.split('_')[2]
    item = pending_items.get(item_id)
    
    if not item:
        await query.edit_message_caption("This item is no longer pending.")
        return

    if action == "reject":
        await context.bot.send_message(item['seller_id'], f"Sorry, your auction for ｢ {item['name']} 」 was rejected.")
        await query.edit_message_caption(caption=f"Rejected ｢ {item['name']} 」 by Admin.")
        del pending_items[item_id]
        
    elif action == "accept":
        auction_text = generate_auction_text(item)
        
        if item['photo_id']:
            msg = await context.bot.send_photo(chat_id=AUCTION_CHANNEL_ID, photo=item['photo_id'], caption=auction_text)
        else:
            msg = await context.bot.send_message(chat_id=AUCTION_CHANNEL_ID, text=auction_text)
        
        item['channel_message_id'] = msg.message_id
        active_auctions[item_id] = item
        del pending_items[item_id]
        
        await context.bot.send_message(item['seller_id'], f"Great news! Your auction for ｢ {item['name']} 」 was accepted and is live.")
        await query.edit_message_caption(caption=f"Accepted ｢ {item['name']} 」. Sent to channel.")

async def bid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    match = re.search(r'Item id - ([\w-]+)', replied_text)
    if not match:
        await update.message.reply_text("Could not find an Item ID in the post you replied to.")
        return
        
    item_id = match.group(1)
    item = active_auctions.get(item_id)
    
    if not item:
        await update.message.reply_text("This auction is no longer active.")
        return
        
    if bid_amount <= item['current_bid'] or bid_amount < item['base_price']:
        await update.message.reply_text(f"Bid too low. Must be higher than {max(item['current_bid'], item['base_price'])} {item['currency']}.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton("✅ Confirm Bid", callback_data=f"confirmbid_{item_id}_{bid_amount}", api_kwargs={"style": "success"}),
            InlineKeyboardButton("❌ Cancel", callback_data="cancelbid", api_kwargs={"style": "danger"})
        ]
    ])
    
    await update.message.reply_text(
        f"Are you sure you want to bid {bid_amount} {item['currency']} for ｢ {item['name']} 」?",
        reply_markup=keyboard
    )

async def bid_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancelbid":
        await query.edit_message_text("Bid cancelled.")
        return
        
    _, item_id, amount_str = query.data.split('_')
    bid_amount = int(amount_str)
    item = active_auctions.get(item_id)
    
    if not item or bid_amount <= item['current_bid']:
        await query.edit_message_text("Bid failed. The auction might have ended or a higher bid was placed.")
        return

    item['current_bid'] = bid_amount
    item['bidder_name'] = update.effective_user.full_name
    item['bidder_id'] = update.effective_user.id
    
    new_text = generate_auction_text(item)
    try:
        await context.bot.edit_message_caption(
            chat_id=AUCTION_CHANNEL_ID,
            message_id=item['channel_message_id'],
            caption=new_text
        )
    except Exception as e:
        print(f"Error updating channel post: {e}")
        
    await query.edit_message_text(f"✅ Bid successful! You are currently the highest bidder with {bid_amount} {item['currency']}.")
