import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from config import OWNER_ID
import db

async def pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
        
    target_id = None
    target_name = "Admin"
    
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        target_name = update.message.reply_to_message.from_user.first_name
    elif context.args and context.args[0].isdigit():
        target_id = int(context.args[0])
        try:
            chat = await context.bot.get_chat(target_id)
            target_name = chat.first_name
        except Exception:
            pass
            
    if not target_id:
        await update.message.reply_text("⚠️ Uꜱᴀɢᴇ: Rᴇᴘʟʏ ᴛᴏ ᴀ ᴜꜱᴇʀ ᴡɪᴛʜ /Pʀᴏ, ᴏʀ ᴛʏᴘᴇ /Pʀᴏ <user_id>")
        return
        
    await db.add_admin(target_id, target_name)
    await update.message.reply_text(f"⚡ {target_name} ʜᴀs ʙᴇᴇɴ ᴘʀᴏᴍᴏᴛᴇᴅ ᴛᴏ Bᴏᴛ Aᴅᴍɪɴ!")

async def dem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
        
    target_id = None
    target_name = "User"
    
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        target_name = update.message.reply_to_message.from_user.first_name
    elif context.args and context.args[0].isdigit():
        target_id = int(context.args[0])
        try:
            chat = await context.bot.get_chat(target_id)
            target_name = chat.first_name
        except Exception:
            pass
            
    if not target_id:
        await update.message.reply_text("⚠️ Uꜱᴀɢᴇ: Rᴇᴘʟʏ ᴛᴏ ᴀ ᴜꜱᴇʀ ᴡɪᴛʜ /Dᴇᴍ, ᴏʀ ᴛʏᴘᴇ /Dᴇᴍ <user_id>")
        return
        
    await db.remove_admin(target_id)
    await update.message.reply_text(f"🔻 Aᴄᴄᴇss Uᴘᴅᴀᴛᴇ: {target_name} ɪs ɴᴏ ʟᴏɴɢᴇʀ ᴀ Bᴏᴛ Aᴅᴍɪɴ.")

async def prolist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
        
    admins = await db.get_all_admins()
    
    owner_name = "Owner"
    try:
        owner_chat = await context.bot.get_chat(OWNER_ID)
        owner_name = owner_chat.first_name
    except Exception:
        pass
        
    text = f"📋 Pʀᴏᴍᴏᴛᴇᴅ Bᴏᴛ Sᴛᴀғғ\n╭─❖ Oᴡɴᴇʀ ❖────\n👑 {owner_name} — <code>{OWNER_ID}</code>\n╰────────────\n"
    
    if admins:
        text += "─────❖ Aᴅᴍɪɴs ❖──────\n"
        for admin in admins:
            name = admin['name'] or "Admin"
            text += f"🔹 {name} — <code>{admin['id']}</code>\n"
        text += "───────────────────"
        
    await update.message.reply_text(text, parse_mode="HTML")

async def broad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_bot_admin(update.effective_user.id):
        return
        
    if not context.args:
        await update.message.reply_text("⚠️ Usage:\n`/broad <id/all> <message>`\nOR reply to a message with `/broad <id/all>`", parse_mode="Markdown")
        return
        
    target = context.args[0].lower()
    replied_msg = update.message.reply_to_message
    
    text_to_send = None
    if not replied_msg:
        if len(context.args) < 2:
            await update.message.reply_text("⚠️ Please provide a message or reply to a message to broadcast.")
            return
        text_to_send = " ".join(context.args[1:])
        
    targets = []
    if target == "all":
        targets = await db.get_all_users()
    else:
        if not target.isdigit():
            await update.message.reply_text("⚠️ Invalid target ID.")
            return
        targets = [int(target)]
        
    if not targets:
        await update.message.reply_text("⚠️ No users found in database.")
        return
        
    status_msg = await update.message.reply_text(f"🚀 Broadcasting to {len(targets)} user(s)...")
    success, failed = 0, 0
    
    for user_id in targets:
        try:
            if replied_msg:
                await replied_msg.copy(chat_id=user_id)
            else:
                await context.bot.send_message(chat_id=user_id, text=text_to_send, parse_mode="HTML")
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05) 
        
    await status_msg.edit_text(f"✅ Broadcast complete!\n\n**Success:** {success}\n**Failed:** {failed}", parse_mode="Markdown")
