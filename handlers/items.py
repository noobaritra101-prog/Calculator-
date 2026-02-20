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
    await update.message.reply_text("📋 Lɪᴠᴇ Aᴜᴄᴛɪᴏɴs\nSᴇʟᴇᴄᴛ ᴀ ᴄᴀᴛᴇɢᴏʀʏ ᴛᴏ ᴠɪᴇᴡ ɪᴛᴇᴍs:", reply_markup=get_items_keyboard())

async def items_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    filter_type = query.data.split('_')[1]
    all_active = await db.get_all_active()
    
    if filter_type in ["Nuggets", "Gems", "Coins"]:
        filtered_items = [i for i in all_active if i['currency'] == filter_type]
    else: filtered_items = all_active 
        
    cat_display = filter_type.replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs').replace('Slugs', 'Aʟʟ Sʟᴜɢs')

    if not filtered_items:
        text = f"📭 Nᴏ ᴀᴄᴛɪᴠᴇ ᴀᴜᴄᴛɪᴏɴs ᴄᴜʀʀᴇɴᴛʟʏ ғᴏʀ <b>{cat_display}</b>.\nSᴇʟᴇᴄᴛ ᴀɴᴏᴛʜᴇʀ ᴄᴀᴛᴇɢᴏʀʏ:"
        await query.edit_message_text(text, reply_markup=get_items_keyboard(), parse_mode=ParseMode.HTML)
        return

    clean_channel_id = str(AUCTION_CHANNEL_ID).replace('-100', '')
    text = f"<b>📋 Lɪᴠᴇ Aᴜᴄᴛɪᴏɴs</b>\nCᴀᴛᴀɢᴏʀʏ : {cat_display}\n\n"
    
    for idx, item in enumerate(filtered_items, 1):
        post_link = f"https://t.me/c/{clean_channel_id}/{item.get('channel_message_id', '')}"
        text += f"{idx}. <a href='{post_link}'>｢ {html.escape(item['name'])} 」</a> - {html.escape(item['type'])}\n"
        
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=get_items_keyboard())

async def myadd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pending = await db.get_user_pending(user_id)
    active = await db.get_user_active(user_id)
    
    if not pending and not active:
        await update.message.reply_text("You haven't submitted any items yet! Use /add to get started.")
        return
        
    text = f"📦 <b>Yᴏᴜʀ Iᴛᴇᴍs ({html.escape(update.effective_user.full_name)})</b>\n\n"
    
    if active:
        clean_channel_id = str(AUCTION_CHANNEL_ID).replace('-100', '')
        text += "━━❖ 🟢 Aᴄᴛɪᴠᴇ Lɪᴠᴇ Aᴜᴄᴛɪᴏɴs ❖━━\n"
        for item in active:
            curr_disp = item['currency'].replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
            highest_bid = f"{item['current_bid']:,.1f} {curr_disp}" if item['current_bid'] > 0 else "Nᴏ ʙɪᴅs ʏᴇᴛ"
            post_link = f"https://t.me/c/{clean_channel_id}/{item.get('channel_message_id', '')}"
            text += f" • <a href='{post_link}'>｢ {html.escape(item['name'])} 」</a>\n  💰 Hɪɢʜᴇsᴛ Bɪᴅ: {highest_bid}\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
    if pending:
        text += "━━━❖ ⏳ Pᴇɴᴅɪɴɢ Aᴘᴘʀᴏᴠᴀʟ ❖━━━\n"
        for item in pending:
            curr_disp = item['currency'].replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
            icon = "💰" if item['currency'] == "Coins" else "💎" if item['currency'] == "Gems" else "🪙"
            text += f" • ｢ {html.escape(item['name'])} 」\n  {icon} Bᴀsᴇ Pʀɪᴄᴇ: {item['base_price']:,.1f} {curr_disp}\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━\n"
            
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def mybids_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name
    
    all_active = await db.get_all_active()
    user_bids = [item for item in all_active if item.get('bidder_id') == user_id]
    
    if not user_bids:
        await update.message.reply_text("📭 Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴀɴʏ ᴀᴄᴛɪᴠᴇ ʙɪᴅs ᴄᴜʀʀᴇɴᴛʟʏ.", parse_mode=ParseMode.HTML)
        return

    total_coins, total_gems, total_nuggets = 0, 0, 0
    bids_text = "<blockquote>━━━❖ 🟢 Cᴜʀʀᴇɴᴛ Bɪᴅs ❖━━━\n"
    
    for item in user_bids:
        curr, amt = item['currency'], item['current_bid']
        if curr == "Coins": total_coins += amt; icon = "💰"
        elif curr == "Gems": total_gems += amt; icon = "💎"
        elif curr == "Nuggets": total_nuggets += amt; icon = "🪙"
        else: icon = "💰"
            
        curr_disp = curr.replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
        bids_text += f"• 🆔 <code>{item['item_id']}</code>\n⚔️ ｢ {html.escape(item['name'])} 」\n{icon} Yᴏᴜʀ Bɪᴅ: {amt:,.0f} {curr_disp}\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        
    bids_text += "</blockquote>\n"
    
    totals_text = "<blockquote>━━━❖ 💰 Tᴏᴛᴀʟ Sᴘᴇɴᴅ ❖━━━\n"
    if total_coins > 0: totals_text += f"💰 Tᴏᴛᴀʟ Cᴏɪɴs: {total_coins:,.0f}\n"
    if total_gems > 0: totals_text += f"💎 Tᴏᴛᴀʟ Gᴇᴍs: {total_gems:,.0f}\n"
    if total_nuggets > 0: totals_text += f"🪙 Tᴏᴛᴀʟ Nᴜɢɢᴇᴛs: {total_nuggets:,.0f}\n"
    totals_text += "━━━━━━━━━━━━━━━━━━━━━━━</blockquote>\n"
    
    count_text = f"<blockquote>📦 Tᴏᴛᴀʟ Aᴄᴛɪᴠᴇ Bɪᴅs: {len(user_bids)}</blockquote>\n"
    footer = "<b>⚡ Sʜʀᴀɴᴇ Aᴜᴄᴛɪᴏɴ Sʏsᴛᴇᴍ</b>"
    
    await update.message.reply_text(f"<b>📊 Yᴏᴜʀ Aᴄᴛɪᴠᴇ Bɪᴅs ({html.escape(user_name)})</b>\n\n{bids_text}{totals_text}{count_text}{footer}", parse_mode=ParseMode.HTML)

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.full_name
    username = update.effective_user.username or "None"

    db_user = await db.get_user(user_id)
    join_date = db_user['join_date'].strftime("%d %b %Y") if db_user and db_user.get('join_date') else "Unknown"

    total_won = db_user.get('total_won', 0) if db_user else 0
    total_sold = db_user.get('total_sold', 0) if db_user else 0
    total_coin_spent = db_user.get('total_coin_spent', 0) if db_user else 0
    total_gem_spent = db_user.get('total_gem_spent', 0) if db_user else 0
    total_nugget_spent = db_user.get('total_nugget_spent', 0) if db_user else 0

    all_active = await db.get_all_active()
    for item in all_active:
        if item.get('bidder_id') == user_id and item.get('current_bid', 0) > 0:
            total_won += 1
            curr, amt = item.get('currency', ''), item.get('current_bid', 0)
            if curr == 'Coins': total_coin_spent += amt
            elif curr == 'Gems': total_gem_spent += amt
            elif curr == 'Nuggets': total_nugget_spent += amt

        if item.get('seller_id') == user_id:
            total_sold += 1

    text = f"""<b>👤 Yᴏᴜʀ Pʀᴏғɪʟᴇ ({html.escape(name)})</b>
━━━━━━━━━━━━━━━━━━━━━━━
<blockquote>🆔 Uꜱᴇʀ ID: <code>{user_id}</code>
🏷️ Uꜱᴇʀɴᴀᴍᴇ: @{username}
📅 Jᴏɪɴᴇᴅ: {join_date}</blockquote>
━━━━━━━━━━━━━━━━━━━━━━━
<blockquote>━━━❖ 📊 Tᴏᴛᴀʟ Aᴜᴄᴛɪᴏɴ Sᴛᴀᴛꜱ ❖━━━
🏆 Tᴏᴛᴀʟ Iᴛᴇᴍꜱ Wᴏɴ: {total_won:,}
🛍️ Tᴏᴛᴀʟ Iᴛᴇᴍꜱ Sᴏʟᴅ: {total_sold:,}</blockquote>
━━━━━━━━━━━━━━━━━━━━━━━
<blockquote>━━━❖ 💸 Tᴏᴛᴀʟ Sᴘᴇɴᴅ ❖━━━
💰 Cᴏɪɴꜱ: {total_coin_spent:,.0f}
💎 Gᴇᴍꜱ: {total_gem_spent:,.0f}
🪙 Nᴜɢɢᴇᴛꜱ: {total_nugget_spent:,.0f}
━━━━━━━━━━━━━━━━━━━━━━━</blockquote>"""

    photos = await context.bot.get_user_profile_photos(user_id, limit=1)
    if photos.photos:
        await update.message.reply_photo(photo=photos.photos[0][-1].file_id, caption=text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
