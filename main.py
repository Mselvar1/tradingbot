from telegram.ext import Application, CommandHandler, MessageHandler, filters
from bot.handlers.core import cmd_start, cmd_help, cmd_status
from bot.handlers.analysis import cmd_analyze, cmd_news
from bot.handlers.trading import cmd_buy, cmd_sell, cmd_positions, cmd_balance
from bot.handlers.image import handle_photo
from bot.handlers.risk import cmd_risk, cmd_kill, cmd_resume, cmd_checkstops
from config.settings import settings

def main():
    app = Application.builder().token(settings.telegram_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("sell", cmd_sell))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("checkstops", cmd_checkstops))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_photo))
    print("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()