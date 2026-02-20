import asyncio
import io
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
        except Exception: pass
            
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
        except Exception: pass
            
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
    except Exception: pass
        
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
        await update.message.reply_text("⚠️ Usage:\n`/broad <id/all> <message>`", parse_mode="Markdown")
        return
        
    target = context.args[0].lower()
    replied_msg = update.message.reply_to_message
    
    text_to_send = None
    if not replied_msg:
        if len(context.args) < 2:
            return
        text_to_send = " ".join(context.args[1:])
        
    targets = await db.get_all_users() if target == "all" else [int(target)]
    if not targets: return
        
    status_msg = await update.message.reply_text(f"🚀 Broadcasting to {len(targets)} user(s)...")
    success, failed = 0, 0
    
    for user_id in targets:
        try:
            if replied_msg: await replied_msg.copy(chat_id=user_id)
            else: await context.bot.send_message(chat_id=user_id, text=text_to_send, parse_mode="HTML")
            success += 1
        except Exception: failed += 1
        await asyncio.sleep(0.05) 
        
    await status_msg.edit_text(f"✅ Broadcast complete!\n**Success:** {success}\n**Failed:** {failed}", parse_mode="Markdown")

async def fbroad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_bot_admin(update.effective_user.id):
        return
    replied_msg = update.message.reply_to_message
    if not replied_msg:
        await update.message.reply_text("⚠️ Please reply to a message with `/fbroad`", parse_mode="Markdown")
        return
        
    targets = await db.get_all_users()
    status_msg = await update.message.reply_text(f"🚀 Forwarding to {len(targets)} user(s)...")
    success, failed = 0, 0
    
    for user_id in targets:
        try:
            await context.bot.forward_message(chat_id=user_id, from_chat_id=update.effective_chat.id, message_id=replied_msg.message_id)
            success += 1
        except Exception: failed += 1
        await asyncio.sleep(0.05) 
        
    await status_msg.edit_text(f"✅ Forward Broadcast complete!\n**Success:** {success}\n**Failed:** {failed}", parse_mode="Markdown")

# --- 🗄️ DFILES COMMANDS (Owner Only) ---
async def dfiles_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
        
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="dfiles_sel_users"),
         InlineKeyboardButton("🟢 Active", callback_data="dfiles_sel_active")],
        [InlineKeyboardButton("⏳ Pending", callback_data="dfiles_sel_pending"),
         InlineKeyboardButton("🛡️ Admins", callback_data="dfiles_sel_bot_admins")]
    ])
    await update.message.reply_text("🗄️ **Database File Manager**\nSelect a database table to interact with:", reply_markup=kb, parse_mode="Markdown")

async def dfiles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("⛔ Strictly restricted to Bot Owner.", show_alert=True)
        return
    await query.answer()

    data = query.data.split('_')
    action = data[1]
    
    if action == "menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Users", callback_data="dfiles_sel_users"),
             InlineKeyboardButton("🟢 Active", callback_data="dfiles_sel_active")],
            [InlineKeyboardButton("⏳ Pending", callback_data="dfiles_sel_pending"),
             InlineKeyboardButton("🛡️ Admins", callback_data="dfiles_sel_bot_admins")]
        ])
        await query.edit_message_text("🗄️ **Database File Manager**\nSelect a database table:", reply_markup=kb, parse_mode="Markdown")
        
    elif action == "sel":
        table = data[2]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Download JSON", callback_data=f"dfiles_down_{table}")],
            [InlineKeyboardButton("🗑️ Clear Table", callback_data=f"dfiles_warn_{table}")],
            [InlineKeyboardButton("🔙 Back", callback_data="dfiles_menu")]
        ])
        await query.edit_message_text(f"📂 **{table.upper()} DB**\nSelect an action for this table:", reply_markup=kb, parse_mode="Markdown")
        
    elif action == "down":
        table = data[2]
        rows = await db.get_table_data(table)
        
        # Format the database rows into JSON
        file_content = json.dumps(rows, indent=4, default=str)
        file_io = io.BytesIO(file_content.encode('utf-8'))
        file_io.name = f"{table}_db_export.json"
        
        await context.bot.send_document(chat_id=update.effective_chat.id, document=file_io, caption=f"📥 `{table}` Database Export Data")
        
    elif action == "warn":
        table = data[2]
        if table == "bot_admins":
            await query.answer("⛔ You cannot wipe the Admin table from here.", show_alert=True)
            return
            
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, CLEAR IT", callback_data=f"dfiles_del_{table}", api_kwargs={"style": "danger"}),
             InlineKeyboardButton("❌ Cancel", callback_data=f"dfiles_sel_{table}")]
        ])
        await query.edit_message_text(f"⚠️ **WARNING!**\nAre you absolutely sure you want to completely clear ALL data in the **{table.upper()}** table?\n\nThis cannot be undone!", reply_markup=kb, parse_mode="Markdown")
        
    elif action == "del":
        table = data[2]
        await db.clear_table(table)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="dfiles_menu")]])
        await query.edit_message_text(f"✅ **{table.upper()} DB Cleared Successfully!**", reply_markup=kb, parse_mode="Markdown")
