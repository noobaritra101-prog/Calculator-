import re
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from config import AUCTION_CHANNEL_ID, BID_LOG_GROUP_ID, PUBLIC_GROUP_ID, CHANNEL_LINK, GROUP_LINK
import db

async def check_force_join(bot, user_id):
    """Checks if the user has joined both the public group and the auction channel."""
    channels = [AUCTION_CHANNEL_ID, PUBLIC_GROUP_ID]
    for chat_id in channels:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception:
            pass 
    return True

def generate_auction_text(item, admin_review=None):
    seller_link = f"<a href='tg://user?id={item['seller_id']}'>{html.escape(item['seller_name'])}</a>"
    
    text = f"""<b>Name : - ｢ {html.escape(item['name'])} 」</b>

<blockquote>Type : {html.escape(item['type'])}
Level : {item['level']}</blockquote>

<blockquote>More info :-
{html.escape(item['more_info'])}</blockquote>

<blockquote>Seller Name - {seller_link}
Seller Id - <code>{item['seller_id']}</code>
Base price - {item['base_price']:,.1f} {item['currency']}
Item id - <code>{item['item_id']}</code></blockquote>
"""

    if admin_review:
        if admin_review['status'] == 'accepted':
            text += f"\n👑 Aᴘᴘʀᴏᴠᴇᴅ ʙʏ: {html.escape(admin_review['admin_name'])}"
        elif admin_review['status'] == 'rejected':
            text += f"\n⛔ Iᴛᴇᴍ Rᴇᴊᴇᴄᴛᴇᴅ\n👤 Rᴇᴊᴇᴄᴛᴇᴅ ʙʏ: {html.escape(admin_review['admin_name'])}\n📝 Rᴇᴀsᴏɴ: {admin_review['reason']}"
        elif admin_review['status'] == 'pending':
            text += "\n⏳ Pᴇɴᴅɪɴɢ Aᴘᴘʀᴏᴠᴀʟ..."
    else:
        current_bid_display = f"{item['current_bid']:,.1f} {item['currency']}" if item['current_bid'] > 0 else "None"
        bidder_link = f"<a href='tg://user?id={item['bidder_id']}'>{html.escape(item['bidder_name'])}</a>" if item.get('bidder_id') else "None"
        text += f"\nCurrent Bid - {current_bid_display}\nBy - {bidder_link}"
        
    return text

def extract_item_id_for_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        return context.args[0]
    if update.message.reply_to_message:
        text = update.message.reply_to_message.caption or update.message.reply_to_message.text
        if text:
            # Bulletproof regex that ignores hidden HTML tags
            match = re.search(r'Item id -.*?([\w-]+)', text.replace('<code>', '').replace('</code>', ''), re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return None

async def admin_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if not await db.is_bot_admin(update.effective_user.id):
        await query.answer("⛔ Access denied.", show_alert=True)
        return

    await query.answer()
    action, item_id = query.data.split('_')[1], query.data.split('_')[2]
    item = await db.get_pending(item_id)
    
    if not item:
        if query.message.photo:
            await query.edit_message_caption("⚠️ This item is no longer pending.")
        else:
            await query.edit_message_text("⚠️ This item is no longer pending.")
        return

    if action == "reject":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Bᴀsᴇ ᴘʀɪᴄᴇ ᴛᴏᴏ ʜɪɢʜ", callback_data=f"rej_price_{item_id}")],
            [InlineKeyboardButton("Wʀᴏɴɢ ɪɴғᴏʀᴍᴀᴛɪᴏɴ", callback_data=f"rej_info_{item_id}")],
            [InlineKeyboardButton("Nᴏᴛ ᴡᴏʀᴛʜʏ", callback_data=f"rej_unworthy_{item_id}")],
            [InlineKeyboardButton("Nᴏᴛ ɪɴ ᴅᴇᴍᴀɴᴅ", callback_data=f"rej_demand_{item_id}")],
            [InlineKeyboardButton("Oᴛʜᴇʀ", callback_data=f"rej_other_{item_id}")],
            [InlineKeyboardButton("🔙 Cancel", callback_data=f"rej_cancel_{item_id}", api_kwargs={"style": "danger"})]
        ])
        await query.edit_message_reply_markup(reply_markup=kb)
            
    elif action == "accept":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Accept", callback_data=f"confaccept_{item_id}", api_kwargs={"style": "success"}),
             InlineKeyboardButton("❌ Cancel", callback_data=f"cancelaccept_{item_id}", api_kwargs={"style": "danger"})]
        ])
        await query.edit_message_reply_markup(reply_markup=kb)

async def accept_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await db.is_bot_admin(update.effective_user.id):
        return
    await query.answer()
    action, item_id = query.data.split('_')[0], query.data.split('_')[1]
    
    if action == "cancelaccept":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Accept", callback_data=f"admin_accept_{item_id}", api_kwargs={"style": "success"}),
             InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{item_id}", api_kwargs={"style": "danger"})]
        ])
        await query.edit_message_reply_markup(reply_markup=kb)
        return

    item = await db.get_pending(item_id)
    if not item:
        return
        
    auction_text = generate_auction_text(item)
    if item['photo_id']:
        msg = await context.bot.send_photo(chat_id=AUCTION_CHANNEL_ID, photo=item['photo_id'], caption=auction_text, parse_mode=ParseMode.HTML)
    else:
        msg = await context.bot.send_message(chat_id=AUCTION_CHANNEL_ID, text=auction_text, parse_mode=ParseMode.HTML)
    
    item['channel_message_id'] = msg.message_id
    item['bid_history'] = [] 
    await db.add_active(item_id, item)
    await db.delete_pending(item_id)
    
    clean_channel_id = str(AUCTION_CHANNEL_ID).replace('-100', '')
    post_link = f"https://t.me/c/{clean_channel_id}/{msg.message_id}"
    
    accept_msg = f"🎉 Yᴏᴜʀ ᴀᴜᴄᴛɪᴏɴ ғᴏʀ ｢ {html.escape(item['name'])} 」 ʜᴀs ʙᴇᴇɴ ᴀᴄᴄᴇᴘᴛᴇᴅ ᴀɴᴅ ɪs ɴᴏᴡ ʟɪᴠᴇ!\nLɪɴᴋ — <a href='{post_link}'>Cʟɪᴄᴋ ʜᴇʀᴇ 🔗</a>"
    try:
        await context.bot.send_message(item['seller_id'], text=accept_msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception: pass
    
    admin_review = {'status': 'accepted', 'admin_name': update.effective_user.first_name}
    review_text = generate_auction_text(item, admin_review=admin_review)
    
    if query.message.photo:
        await query.edit_message_caption(caption=review_text, parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(text=review_text, parse_mode=ParseMode.HTML)

async def reject_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await db.is_bot_admin(update.effective_user.id):
        return
    await query.answer()
    
    reason_key = query.data.split('_')[1]
    item_id = query.data.split('_')[2]
    
    if reason_key == "cancel":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Accept", callback_data=f"admin_accept_{item_id}", api_kwargs={"style": "success"}),
             InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{item_id}", api_kwargs={"style": "danger"})]
        ])
        await query.edit_message_reply_markup(reply_markup=kb)
        return
    
    reason_map = {
        "price": "Bᴀsᴇ ᴘʀɪᴄᴇ ᴛᴏᴏ ʜɪɢʜ",
        "info": "Wʀᴏɴɢ ɪɴғᴏʀᴍᴀᴛɪᴏɴ",
        "unworthy": "Nᴏᴛ ᴡᴏʀᴛʜʏ",
        "demand": "Nᴏᴛ ɪɴ ᴅᴇᴍᴀɴᴅ",
        "other": "Oᴛʜᴇʀ"
    }
    reason_text = reason_map.get(reason_key, "Oᴛʜᴇʀ")
    
    item = await db.get_pending(item_id)
    if not item:
        return
        
    reject_msg = f"⛔ Yᴏᴜʀ ᴀᴜᴄᴛɪᴏɴ ғᴏʀ ｢ {html.escape(item['name'])} 」 ʜᴀs ʙᴇᴇɴ ʀᴇᴊᴇᴄᴛᴇᴅ.\n📝 Rᴇᴀsᴏɴ: {reason_text}"
    try:
        await context.bot.send_message(item['seller_id'], text=reject_msg)
    except Exception: pass
    
    await db.delete_pending(item_id)
    
    admin_review = {'status': 'rejected', 'admin_name': update.effective_user.first_name, 'reason': reason_text}
    review_text = generate_auction_text(item, admin_review=admin_review)
    
    if query.message.photo:
        await query.edit_message_caption(caption=review_text, parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(text=review_text, parse_mode=ParseMode.HTML)

async def bid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await db.get_setting("auction") == "off":
        await update.message.reply_text("⛔ The auction is currently closed. No bidding is allowed at this time.")
        return
        
    if not await check_force_join(context.bot, update.effective_user.id):
        join_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
            [InlineKeyboardButton("👥 Join Group", url=GROUP_LINK)]
        ])
        await update.message.reply_text("⛔ You must join our Channel and Group to place a bid!", reply_markup=join_kb)
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("Please reply to an active auction post to bid.")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("Format: /bid {amount}")
        return
        
    # FIX 1: Allow users to type commas in their bid (e.g., /bid 5,000)
    clean_amount = args[0].replace(',', '').strip()
    if not clean_amount.isdigit():
        await update.message.reply_text("Please enter a valid number (e.g. /bid 5000)")
        return
        
    bid_amount = int(clean_amount)
    replied_text = update.message.reply_to_message.caption or update.message.reply_to_message.text
    if not replied_text:
        return

    # FIX 2: Bulletproof Regex
    match = re.search(r'Item id -.*?([\w-]+)', replied_text.replace('<code>', '').replace('</code>', ''), re.IGNORECASE)
    if not match:
        await update.message.reply_text("Could not find the Item ID. Make sure you replied to the correct post.")
        return
        
    item_id = match.group(1).strip()
    item = await db.get_active(item_id)
    
    if not item:
        await update.message.reply_text("This auction is no longer active.")
        return

    if update.effective_user.id == item['seller_id']:
        await update.message.reply_text("⛔ You cannot bid on your own item!")
        return
        
    if bid_amount <= item['current_bid'] or bid_amount < item['base_price']:
        await update.message.reply_text(f"Bid too low. Must be higher than {max(item['current_bid'], item['base_price']):,.1f} {item['currency']}.")
        return

    curr_disp = item['currency'].replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
    confirm_text = f"Aʀᴇ ʏᴏᴜ sᴜʀᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴘʟᴀᴄᴇ ᴀ ʙɪᴅ ᴏғ {bid_amount:,.1f} {curr_disp} ғᴏʀ {item['name']} ⁉️"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ Confirm Bid", callback_data=f"confirmbid_{item_id}_{bid_amount}", api_kwargs={"style": "success"}),
         InlineKeyboardButton("❌ Cancel", callback_data="cancelbid", api_kwargs={"style": "danger"})]
    ])
    await update.message.reply_text(confirm_text, reply_markup=keyboard)

async def bid_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if await db.get_setting("auction") == "off":
        await query.edit_message_text("⛔ The auction is closed.")
        return

    if query.data == "cancelbid":
        await query.edit_message_text("Bid cancelled.")
        return
        
    if not await check_force_join(context.bot, update.effective_user.id):
        await query.edit_message_text("⛔ You must join our Channel and Group to place a bid! Use the /bid command again to get the links.")
        return
        
    _, item_id, amount_str = query.data.split('_')
    bid_amount = int(amount_str)
    
    item = await db.get_active(item_id)
    if not item:
        await query.edit_message_text("Bid failed. The auction might have ended.")
        return

    if update.effective_user.id == item['seller_id']:
        await query.edit_message_text("⛔ You cannot bid on your own item!")
        return

    # FIX 3: Catch accidental double-clicks smoothly
    if bid_amount <= item['current_bid']:
        curr_disp = item['currency'].replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
        if item['bidder_id'] == update.effective_user.id and item['current_bid'] == bid_amount:
            await query.edit_message_text(f"✅ Bɪᴅ ᴏғ {bid_amount:,.1f} {curr_disp} ᴡᴀs ᴀʟʀᴇᴀᴅʏ ᴘʟᴀᴄᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!")
        else:
            await query.edit_message_text("⛔ Bid failed. A higher bid was already placed.")
        return

    previous_bidder_id = item.get('bidder_id')
    previous_bidder_username = item.get('bidder_username', 'Unknown')
    previous_bidder_name = item.get('bidder_name')

    if item['current_bid'] > 0:
        history = item.get('bid_history', [])
        history.append({
            'bid_amount': item['current_bid'],
            'bidder_name': item['bidder_name'],
            'bidder_id': item['bidder_id']
        })
        item['bid_history'] = history

    item['current_bid'] = bid_amount
    item['bidder_name'] = update.effective_user.full_name
    item['bidder_id'] = update.effective_user.id
    item['bidder_username'] = update.effective_user.username or "None" 
    
    await db.update_active(item_id, item)
    
    new_text = generate_auction_text(item)
    try:
        if item.get('photo_id'):
            await context.bot.edit_message_caption(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], caption=new_text, parse_mode=ParseMode.HTML)
        else:
            await context.bot.edit_message_text(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], text=new_text, parse_mode=ParseMode.HTML)
    except Exception:
        pass
        
    formatted_bid = f"{bid_amount:,.1f}"
    curr_disp = item['currency'].replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
    
    success_text = f"✅ Bɪᴅ ᴏғ {formatted_bid} {curr_disp} ᴘʟᴀᴄᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ!"
    
    if previous_bidder_id and previous_bidder_id != update.effective_user.id:
        prev_display = f"@{previous_bidder_username}" if previous_bidder_username not in ['Unknown', 'None'] else previous_bidder_name
        success_text += f"\n🔔 {prev_display}, ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ᴏᴜᴛʙɪᴅ!"
        
        clean_channel_id = str(AUCTION_CHANNEL_ID).replace('-100', '')
        post_link = f"https://t.me/c/{clean_channel_id}/{item.get('channel_message_id', '')}"
        dm_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 View the item", url=post_link)]])
        dm_text = f"🔔 Yᴏᴜʀ ʙɪᴅ ʜᴀs ʙᴇᴇɴ ᴏᴜᴛʙɪᴅ!\nA ɴᴇᴡ ʜɪɢʜᴇʀ ʙɪᴅ ʜᴀs ʙᴇᴇɴ ᴘʟᴀᴄᴇᴅ ᴏɴ ｢ {item['name']} 」.\n💰 Cʜᴇᴄᴋ ᴛʜᴇ ᴀᴜᴄᴛɪᴏɴ ᴛᴏ ᴘʟᴀᴄᴇ ᴀ ɴᴇᴡ ʙɪᴅ!"
        
        try:
            await context.bot.send_message(chat_id=previous_bidder_id, text=dm_text, reply_markup=dm_kb)
        except Exception:
            pass 

    await query.edit_message_text(success_text)
    
    bidder_link = f"<a href='tg://user?id={item['bidder_id']}'>{html.escape(item['bidder_name'])}</a>"
    log_text = (
        f"📝 <b>New Bid Log</b>\n"
        f"Item: ｢ {html.escape(item['name'])} 」(ID: <code>{item_id}</code>)\n"
        f"Amount: {formatted_bid} {item['currency']}\n"
        f"Bidder: {bidder_link} (<code>{item['bidder_id']}</code>)"
    )
    try:
        await context.bot.send_message(chat_id=BID_LOG_GROUP_ID, text=log_text, parse_mode=ParseMode.HTML)
    except Exception:
        pass

async def rollback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_bot_admin(update.effective_user.id):
        return
        
    item_id = extract_item_id_for_admin(update, context)
    if not item_id:
        await update.message.reply_text("⚠️ Please reply to an auction post or provide an item ID.\nExample: `/rollback ID`", parse_mode="Markdown")
        return
        
    item = await db.get_active(item_id)
    if not item:
        await update.message.reply_text("❌ Item not found in active auctions.")
        return
        
    history = item.get('bid_history', [])
    if not history:
        if item['current_bid'] == 0:
            await update.message.reply_text("⚠️ There are no previous bids to rollback to.")
            return
            
        item['current_bid'] = 0
        item['bidder_name'] = "None"
        item['bidder_id'] = None
    else:
        prev = history.pop()
        item['current_bid'] = prev['bid_amount']
        item['bidder_name'] = prev['bidder_name']
        item['bidder_id'] = prev['bidder_id']
        item['bid_history'] = history

    await db.update_active(item_id, item)
    
    new_text = generate_auction_text(item)
    try:
        if item.get('photo_id'):
            await context.bot.edit_message_caption(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], caption=new_text, parse_mode=ParseMode.HTML)
        else:
            await context.bot.edit_message_text(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'], text=new_text, parse_mode=ParseMode.HTML)
    except Exception:
        pass
        
    rollback_display = f"{item['current_bid']:,.1f}" if item['current_bid'] > 0 else "0"
    await update.message.reply_text(f"⏪ **Rollback successful!**\nItem ｢ {item['name']} 」 reverted to previous bid:\n{rollback_display} {item['currency']} by {item['bidder_name']}", parse_mode="Markdown")

async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_bot_admin(update.effective_user.id):
        return
        
    item_id = extract_item_id_for_admin(update, context)
    if not item_id:
        return
        
    item = await db.get_active(item_id)
    if not item:
        return
        
    try:
        await context.bot.delete_message(chat_id=AUCTION_CHANNEL_ID, message_id=item['channel_message_id'])
    except Exception:
        pass

    await db.delete_active(item_id)
    await update.message.reply_text(f"🗑️ Auction for ｢ {item['name']} 」 has been successfully revoked and deleted.")

async def bidhistory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_bot_admin(update.effective_user.id):
        return
        
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/bidhistory <Item ID or User ID>`\nCheck history for a specific item, or audit all bids made by a specific user.", parse_mode="Markdown")
        return
        
    target = context.args[0].strip()
    
    # Check if input is a User ID (Digits only)
    if target.isdigit():
        user_id = int(target)
        all_active = await db.get_all_active()
        found_bids = []
        
        for item in all_active:
            hist = item.get('bid_history', [])
            # Find past bids
            for b in hist:
                if b['bidder_id'] == user_id:
                    found_bids.append((item['name'], item['currency'], b['bid_amount']))
            # Find current winning bids
            if item.get('bidder_id') == user_id:
                found_bids.append((item['name'], item['currency'], item['current_bid']))
        
        if not found_bids:
            await update.message.reply_text(f"📭 Nᴏ ʙɪᴅ ʜɪsᴛᴏʀʏ ғᴏᴜɴᴅ ғᴏʀ Usᴇʀ ID: <code>{user_id}</code>.", parse_mode=ParseMode.HTML)
            return
            
        text = f"📜 <b>Bɪᴅ Hɪsᴛᴏʀʏ Aᴜᴅɪᴛ: Usᴇʀ <code>{user_id}</code></b>\n\n"
        for name, curr, amt in found_bids:
            curr_disp = curr.replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
            text += f"• ｢ {html.escape(name)} 」 - {amt:,.0f} {curr_disp}\n"
            
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        
    # Check if input is an Item ID (Alphanumeric)
    else:
        item = await db.get_active(target)
        if not item:
            await update.message.reply_text(f"❌ Iᴛᴇᴍ <code>{target}</code> ɴᴏᴛ ғᴏᴜɴᴅ ɪɴ ᴀᴄᴛɪᴠᴇ ᴀᴜᴄᴛɪᴏɴs.", parse_mode=ParseMode.HTML)
            return
            
        text = f"📜 <b>Bɪᴅ Hɪsᴛᴏʀʏ ғᴏʀ ｢ {html.escape(item['name'])} 」</b>\nID: <code>{target}</code>\n\n"
        
        hist = item.get('bid_history', [])
        if not hist and item.get('current_bid', 0) == 0:
            text += "📭 Nᴏ ʙɪᴅs ᴘʟᴀᴄᴇᴅ ʏᴇᴛ."
        else:
            idx = 1
            for b in hist:
                curr_disp = item['currency'].replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
                name = html.escape(b.get('bidder_name', 'Unknown'))
                text += f"{idx}. {name} - {b['bid_amount']:,.0f} {curr_disp}\n"
                idx += 1
            
            if item.get('current_bid', 0) > 0:
                curr_disp = item['currency'].replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
                name = html.escape(item.get('bidder_name', 'Unknown'))
                text += f"\n🏆 <b>Cᴜʀʀᴇɴᴛ Wɪɴɴɪɴɢ Bɪᴅ:</b>\n{name} - {item['current_bid']:,.0f} {curr_disp}"
                
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
