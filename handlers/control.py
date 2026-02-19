from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import ADMIN_GROUP_ID
import db

async def is_admin(bot, user_id):
    """Checks if the user is an admin or creator in the ADMIN_GROUP."""
    try:
        member = await bot.get_chat_member(chat_id=ADMIN_GROUP_ID, user_id=user_id)
        return member.status in ['creator', 'administrator']
    except Exception as e:
        print(f"Admin check failed: {e}")
        return False

def get_control_kb():
    sub_status = db.get_setting("submissions")
    bid_status = db.get_setting("bidding")
    
    sub_text = "🟢 Submissions: ON" if sub_status == "on" else "🔴 Submissions: OFF"
    bid_text = "🟢 Bidding: ON" if bid_status == "on" else "🔴 Bidding: OFF"
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(sub_text, callback_data="toggle_submissions", api_kwargs={"style": "primary"})],
        [InlineKeyboardButton(bid_text, callback_data="toggle_bidding", api_kwargs={"style": "primary"})]
    ])

async def cauc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(context.bot, update.effective_user.id):
        await update.message.reply_text("⛔ You do not have admin permissions to use this command.")
        return
        
    await update.message.reply_text(
        "⚙️ **Auction Control Panel**\nToggle the bot's global settings below:", 
        reply_markup=get_control_kb(), 
        parse_mode="Markdown"
    )

async def cauc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if not await is_admin(context.bot, update.effective_user.id):
        await query.answer("⛔ Access denied. Only admins can toggle these.", show_alert=True)
        return
        
    # Determine which setting was clicked and toggle it
    action = query.data.split('_')[1] # Gets 'submissions' or 'bidding'
    current_status = db.get_setting(action)
    new_status = "off" if current_status == "on" else "on"
    
    db.set_setting(action, new_status)
    
    # Update the keyboard visually
    await query.edit_message_reply_markup(reply_markup=get_control_kb())
    await query.answer(f"{action.capitalize()} turned {new_status.upper()}")
