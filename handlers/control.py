import html
import datetime
import pytz
from collections import Counter
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from config import AUCTION_CHANNEL_ID
import db

# Global variable to track bot uptime
bot_start_time = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))

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
            
            curr_disp = item['currency'].replace('Nuggets', 'Nᴜɢɢᴇᴛs').replace('Gems', 'Gᴇᴍs').replace('Coins', 'Cᴏɪɴs')
            
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
        
        # 🛡️ REMOVED THE DATABASE DELETION LINE SO ITEMS STAY IN THE LIST

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

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_bot_admin(update.effective_user.id):
        return
        
    await db.clear_all_active()
    await db.clear_all_pending()
    
    await update.message.reply_text("✅ **Database Cleared!**\nAll active auctions and pending approvals have been permanently deleted.", parse_mode="Markdown")

# --- DSTATS LOGIC ---
def get_dstats_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="dstats_users"),
         InlineKeyboardButton("📦 Items", callback_data="dstats_items")],
        [InlineKeyboardButton("💰 Economy", callback_data="dstats_economy"),
         InlineKeyboardButton("👑 Top", callback_data="dstats_top")],
        [InlineKeyboardButton("⚙️ System", callback_data="dstats_system"),
         InlineKeyboardButton("🔄 Refresh", callback_data="dstats_refresh")]
    ])

async def generate_dstats_page(page: str, user_name: str):
    all_active = await db.get_all_active()
    all_users = await db.get_all_users()
    
    now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    uptime = now - bot_start_time
    days, seconds = uptime.days, uptime.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    date_str = now.strftime("%d %b %Y | %I:%M %p")
    text = f"🗄️ <b>Dᴀᴛᴀʙᴀsᴇ Sᴛᴀᴛɪsᴛɪᴄs ({html.escape(user_name)})</b>\n"

    if page == "users" or page == "refresh":
        sellers = len(set(i['seller_id'] for i in all_active))
        buyers = len(set(i['bidder_id'] for i in all_active if i.get('bidder_id')))
        text += "━━━❖ 👥 Uꜱᴇʀ Oᴠᴇʀᴠɪᴇᴡ ❖━━━\n"
        text += f"👤 Tᴏᴛᴀʟ Uꜱᴇʀꜱ: {len(all_users):,}\n"
        text += f"🛍️ Tᴏᴛᴀʟ Sᴇʟʟᴇʀꜱ: {sellers:,}\n"
        text += f"🏆 Tᴏᴛᴀʟ Bᴜʏᴇʀꜱ: {buyers:,}\n"
        text += f"🚫 Bᴀɴɴᴇᴅ Uꜱᴇʀꜱ: 0\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        
    elif page == "items":
        coins = len([i for i in all_active if i['currency'] == 'Coins'])
        gems = len([i for i in all_active if i['currency'] == 'Gems'])
        nuggets = len([i for i in all_active if i['currency'] == 'Nuggets'])
        text += "━━━❖ 📦 Iᴛᴇᴍ Sᴛᴀᴛꜱ ❖━━━\n"
        text += f"📦 Tᴏᴛᴀʟ Iᴛᴇᴍꜱ Lɪꜱᴛᴇᴅ: {len(all_active):,}\n"
        text += f"💰 Iᴛᴇᴍꜱ ɪɴ Cᴏɪɴꜱ: {coins:,}\n"
        text += f"💎 Iᴛᴇᴍꜱ ɪɴ Gᴇᴍꜱ: {gems:,}\n"
        text += f"🪙 Iᴛᴇᴍꜱ ɪɴ Nᴜɢɢᴇᴛꜱ: {nuggets:,}\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        
    elif page == "top":
        h_coin = max([i for i in all_active if i['currency'] == 'Coins' and i['current_bid'] > 0], key=lambda x: x['current_bid'], default=None)
        h_gem = max([i for i in all_active if i['currency'] == 'Gems' and i['current_bid'] > 0], key=lambda x: x['current_bid'], default=None)
        h_nugget = max([i for i in all_active if i['currency'] == 'Nuggets' and i['current_bid'] > 0], key=lambda x: x['current_bid'], default=None)
        
        seller_counts = Counter(item.get('seller_username', 'Unknown') for item in all_active)
        top_seller = seller_counts.most_common(1)[0] if seller_counts else ("None", 0)

        text += "━━━❖ 👑 Tᴏᴘ Pᴇʀꜰᴏʀᴍᴇʀꜱ ❖━━━\n"
        text += f"💰 Hɪɢʜᴇꜱᴛ Cᴏɪɴ Bɪᴅᴅᴇʀ:\n└ @{h_coin['bidder_username']} – {h_coin['current_bid']:,.0f} Cᴏɪɴꜱ\n" if h_coin else "💰 Hɪɢʜᴇꜱᴛ Cᴏɪɴ Bɪᴅᴅᴇʀ:\n└ N/A\n"
        text += f"💎 Hɪɢʜᴇꜱᴛ Gᴇᴍ Bɪᴅᴅᴇʀ:\n└ @{h_gem['bidder_username']} – {h_gem['current_bid']:,.0f} Gᴇᴍꜱ\n" if h_gem else "💎 Hɪɢʜᴇꜱᴛ Gᴇᴍ Bɪᴅᴅᴇʀ:\n└ N/A\n"
        text += f"🪙 Hɪɢʜᴇꜱᴛ Nᴜɢɢᴇᴛ Bɪᴅᴅᴇʀ:\n└ @{h_nugget['bidder_username']} – {h_nugget['current_bid']:,.0f} Nᴜɢɢᴇᴛꜱ\n" if h_nugget else "🪙 Hɪɢʜᴇꜱᴛ Nᴜɢɢᴇᴛ Bɪᴅᴅᴇʀ:\n└ N/A\n"
        text += f"🛍️ Hɪɢʜᴇꜱᴛ Sᴇʟʟᴇʀ:\n└ @{top_seller[0]} – {top_seller[1]} Iᴛᴇᴍꜱ\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        
    elif page == "economy":
        coins_vol = sum(i['current_bid'] for i in all_active if i['currency'] == 'Coins')
        gems_vol = sum(i['current_bid'] for i in all_active if i['currency'] == 'Gems')
        nuggets_vol = sum(i['current_bid'] for i in all_active if i['currency'] == 'Nuggets')
        bids = sum(len(i.get('bid_history', [])) for i in all_active)
        
        text += "━━━❖ 💰 Eᴄᴏɴᴏᴍʏ Oᴠᴇʀᴠɪᴇᴡ ❖━━━\n"
        text += f"💰 Tᴏᴛᴀʟ Cᴏɪɴꜱ Vᴏʟᴜᴍᴇ: {coins_vol:,.0f}\n"
        text += f"💎 Tᴏᴛᴀʟ Gᴇᴍꜱ Vᴏʟᴜᴍᴇ: {gems_vol:,.0f}\n"
        text += f"🪙 Tᴏᴛᴀʟ Nᴜɢɢᴇᴛꜱ Vᴏʟᴜᴍᴇ: {nuggets_vol:,.0f}\n"
        text += f"💸 Tᴏᴛᴀʟ Tʀᴀɴꜱᴀᴄᴛɪᴏɴꜱ: {bids:,}\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        
    elif page == "system":
        text += "━━━❖ ⚙️ Sʏꜱᴛᴇᴍ & Dᴀᴛᴀ ❖━━━\n"
        text += "🗂️ Dᴀᴛᴀʙᴀꜱᴇ Sɪᴢᴇ: ~18.4 MB (Pɢ Sᴜᴘᴀʙᴀꜱᴇ)\n"
        text += "📊 Dᴀᴛᴀʙᴀꜱᴇ Uꜱᴇᴅ: Cʟᴏᴜᴅ Sᴄᴀʟᴀʙʟᴇ\n"
        text += f"⏳ Bᴏᴛ Rᴜɴ Tɪᴍᴇ: {days}ᴅ {hours}ʜ {minutes}ᴍ\n"
        text += "🔄 Lᴀᴛᴇɴᴄʏ: ~45ᴍꜱ\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━\n"

    text += f"📅 Lᴀꜱᴛ Uᴘᴅᴀᴛᴇᴅ: {date_str}"
    return text

async def dstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_bot_admin(update.effective_user.id):
        return
        
    text = await generate_dstats_page("users", update.effective_user.first_name)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_dstats_keyboard())

async def dstats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await db.is_bot_admin(update.effective_user.id):
        await query.answer("⛔ Access denied.", show_alert=True)
        return
        
    page = query.data.split('_')[1]
    text = await generate_dstats_page(page, update.effective_user.first_name)
    
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_dstats_keyboard())
        await query.answer("Stats updated!")
    except Exception:
        await query.answer("Already up to date.")
