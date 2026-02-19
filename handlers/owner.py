from telegram import Update
from telegram.ext import ContextTypes
from config import OWNER_ID
import db

async def pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
        
    target_id = None
    
    # Check if replying to a user
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    # Otherwise, check if an ID was typed
    elif context.args and context.args[0].isdigit():
        target_id = int(context.args[0])
        
    if not target_id:
        await update.message.reply_text("⚠️ Usage: Reply to a user with `/Pro`, or type `/Pro <user_id>`", parse_mode="Markdown")
        return
        
    db.add_admin(target_id)
    await update.message.reply_text(f"✅ User `{target_id}` has been **promoted** to Bot Admin.", parse_mode="Markdown")

async def dem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
        
    target_id = None
    
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args and context.args[0].isdigit():
        target_id = int(context.args[0])
        
    if not target_id:
        await update.message.reply_text("⚠️ Usage: Reply to a user with `/Dem`, or type `/Dem <user_id>`", parse_mode="Markdown")
        return
        
    db.remove_admin(target_id)
    await update.message.reply_text(f"❌ User `{target_id}` has been **demoted** from Bot Admin.", parse_mode="Markdown")

async def prolist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
        
    admins = db.get_all_admins()
    if not admins:
        await update.message.reply_text("📋 There are currently no promoted Bot Admins.")
        return
        
    text = "📋 **Promoted Bot Admins:**\n\n"
    for idx, admin_id in enumerate(admins, 1):
        text += f"{idx}. <code>{admin_id}</code>\n"
        
    await update.message.reply_text(text, parse_mode="HTML")
