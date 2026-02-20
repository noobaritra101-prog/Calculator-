import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler, TypeHandler, ApplicationHandlerStop
import config
import db 

from handlers.submit import (
    start_command, add_command, category_callback, receive_basic_info, 
    receive_more_info, currency_callback, receive_price, cancel_submission,
    CHOOSING_CATEGORY, WAITING_BASIC, WAITING_MORE, CHOOSING_CURRENCY, WAITING_PRICE
)
from handlers.admin_auction import (
    admin_decision_callback, accept_confirm_callback, reject_reason_callback, 
    bid_command, bid_action_callback, rollback_command, revoke_command
)
from handlers.items import items_command, items_filter_callback, myadd_command, mybids_command 
from handlers.control import cauc_command, cauc_callback, clear_command, dstats_command, dstats_callback
from handlers.owner import pro_command, dem_command, prolist_command, broad_command, fbroad_command

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

async def post_init(application):
    await db.init_db()
    print("🐘 Connected to Supabase via asyncpg!")

# 🛡️ GLOBAL INTERCEPTOR & REGISTRATION
async def global_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercepts every update. Forces DM registration before allowing commands."""
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    
    # 1. Always register users instantly if they are in DMs
    if update.effective_chat and update.effective_chat.type == 'private':
        await db.register_user(user_id)
        return

    # 2. Check registration status for Group actions
    is_registered = await db.is_user_registered(user_id)
    if not is_registered:
        bot_username = context.bot.username
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Start Bot in DM", url=f"https://t.me/{bot_username}")]])
        
        # Block group commands
        if update.message and update.message.text and update.message.text.startswith('/'):
            await update.message.reply_text("⛔ Yᴏᴜ ᴍᴜsᴛ sᴛᴀʀᴛ ᴍᴇ ɪɴ Dɪʀᴇᴄᴛ Mᴇssᴀɢᴇs (DMs) ғɪʀsᴛ ʙᴇғᴏʀᴇ ᴜsɪɴɢ ᴀɴʏ ᴄᴏᴍᴍᴀɴᴅs!", reply_markup=kb)
            raise ApplicationHandlerStop # Halts all other handlers
            
        # Block group inline buttons (like bidding)
        elif update.callback_query:
            await update.callback_query.answer("⛔ You must start the bot in DMs first!", show_alert=True)
            raise ApplicationHandlerStop # Halts all other handlers

def main():
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()

    # 🛡️ FIREWALL HANDLER (Group -1 executes first and intercepts unauthorized users)
    app.add_handler(TypeHandler(Update, global_middleware), group=-1)

    # 🟢 STANDARD HANDLERS (Group 0)
    app.add_handler(CommandHandler("start", start_command))
    
    app.add_handler(CommandHandler("pro", pro_command))
    app.add_handler(CommandHandler("dem", dem_command))
    app.add_handler(CommandHandler("prolist", prolist_command))
    app.add_handler(CommandHandler("broad", broad_command))
    app.add_handler(CommandHandler("fbroad", fbroad_command))
    
    app.add_handler(CommandHandler(["cauc", "caua"], cauc_command))
    app.add_handler(CommandHandler("clear", clear_command)) 
    app.add_handler(CommandHandler("dstats", dstats_command)) 
    app.add_handler(CallbackQueryHandler(cauc_callback, pattern="^toggle_"))
    app.add_handler(CallbackQueryHandler(dstats_callback, pattern="^dstats_")) 
    
    app.add_handler(CommandHandler("items", items_command))
    app.add_handler(CommandHandler("myadd", myadd_command))
    app.add_handler(CommandHandler("mybids", mybids_command))
    app.add_handler(CallbackQueryHandler(items_filter_callback, pattern="^filter_"))

    app.add_handler(CommandHandler("bid", bid_command))
    app.add_handler(CallbackQueryHandler(bid_action_callback, pattern="^(confirmbid_|cancelbid)"))

    app.add_handler(CommandHandler("rollback", rollback_command))
    app.add_handler(CommandHandler("revoke", revoke_command))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_command)],
        states={
            CHOOSING_CATEGORY: [
                CallbackQueryHandler(cancel_submission, pattern="^cancel_add$"),
                CallbackQueryHandler(category_callback, pattern="^add_")
            ],
            WAITING_BASIC: [
                CallbackQueryHandler(cancel_submission, pattern="^cancel_add$"),
                MessageHandler(filters.FORWARDED & (filters.TEXT | filters.PHOTO), receive_basic_info)
            ],
            WAITING_MORE: [
                CallbackQueryHandler(cancel_submission, pattern="^cancel_add$"),
                MessageHandler(filters.FORWARDED & (filters.TEXT | filters.PHOTO), receive_more_info)
            ],
            CHOOSING_CURRENCY: [
                CallbackQueryHandler(cancel_submission, pattern="^cancel_add$"),
                CallbackQueryHandler(currency_callback, pattern="^curr_")
            ],
            WAITING_PRICE: [
                CallbackQueryHandler(cancel_submission, pattern="^cancel_add$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price) 
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_submission)]
    )
    app.add_handler(conv_handler)
    
    app.add_handler(CallbackQueryHandler(accept_confirm_callback, pattern="^(confaccept|cancelaccept)_"))
    app.add_handler(CallbackQueryHandler(reject_reason_callback, pattern="^rej_"))
    app.add_handler(CallbackQueryHandler(admin_decision_callback, pattern="^admin_"))

    print("Shrane Auction Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()

