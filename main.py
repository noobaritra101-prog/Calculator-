import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
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
from handlers.items import items_command, items_filter_callback, myadd_command 
from handlers.control import cauc_command, cauc_callback
from handlers.owner import pro_command, dem_command, prolist_command, broad_command

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

async def post_init(application):
    await db.init_db()
    print("🐘 Connected to Supabase via asyncpg!")

def main():
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    
    app.add_handler(CommandHandler("pro", pro_command))
    app.add_handler(CommandHandler("dem", dem_command))
    app.add_handler(CommandHandler("prolist", prolist_command))
    app.add_handler(CommandHandler("broad", broad_command))
    
    app.add_handler(CommandHandler(["cauc", "caua"], cauc_command))
    app.add_handler(CallbackQueryHandler(cauc_callback, pattern="^toggle_"))
    
    app.add_handler(CommandHandler("items", items_command))
    app.add_handler(CommandHandler("myadd", myadd_command))
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
