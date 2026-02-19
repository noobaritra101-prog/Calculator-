import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
import config

from handlers.submit import (
    start_command, add_command, category_callback, receive_basic_info, 
    receive_more_info, currency_callback, receive_price, cancel_submission,
    CHOOSING_CATEGORY, WAITING_BASIC, WAITING_MORE, CHOOSING_CURRENCY, WAITING_PRICE
)
from handlers.admin_auction import admin_decision_callback, bid_command, bid_action_callback

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def main():
    app = Application.builder().token(config.BOT_TOKEN).build()

    # Basic Commands
    app.add_handler(CommandHandler("start", start_command))
    
    # Bidding Handlers
    app.add_handler(CommandHandler("bid", bid_command))
    app.add_handler(CallbackQueryHandler(bid_action_callback, pattern="^(confirmbid_|cancelbid)"))

    # Submission FSM
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_command)],
        states={
            CHOOSING_CATEGORY: [CallbackQueryHandler(category_callback, pattern="^add_")],
            WAITING_BASIC: [MessageHandler(filters.FORWARDED & (filters.TEXT | filters.PHOTO), receive_basic_info)],
            WAITING_MORE: [MessageHandler(filters.FORWARDED & (filters.TEXT | filters.PHOTO), receive_more_info)],
            CHOOSING_CURRENCY: [CallbackQueryHandler(currency_callback, pattern="^curr_")],
            WAITING_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price)]
        },
        fallbacks=[CommandHandler("cancel", cancel_submission)]
    )
    app.add_handler(conv_handler)
    
    # Admin Approval Callbacks
    app.add_handler(CallbackQueryHandler(admin_decision_callback, pattern="^admin_"))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
