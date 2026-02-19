from telegram import Update
from telegram.ext import ContextTypes
from config import OWNER_ID
import db

async def pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only the Owner can promote
    if update.effective_user.id != OWNER_ID:
        return
        
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Usage: `/Pro <user_id>`\nExample: `/Pro 123456789`", parse_mode="Markdown")
        return
        
    target_id = int(context.args[0])
    db.add_admin(target_id)
    await update.message.reply_text(f"✅ User `{target_id}` has been **promoted** to Bot Admin.", parse_mode="Markdown")

async def dem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only the Owner can demote
    if update.effective_user.id != OWNER_ID:
        return
        
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Usage: `/Dem <user_id>`\nExample: `/Dem 123456789`", parse_mode="Markdown")
        return
        
    target_id = int(context.args[0])
    db.remove_admin(target_id)
    await update.message.reply_text(f"❌ User `{target_id}` has been **demoted** from Bot Admin.", parse_mode="Markdown")

async def prolist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only the Owner can view the list
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
