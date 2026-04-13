import asyncio
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from bot.handlers.core import cmd_start, cmd_help, cmd_status
from bot.handlers.analysis import cmd_analyze, cmd_news
from bot.handlers.trading import cmd_buy, cmd_sell, cmd_positions, cmd_balance
from bot.handlers.image import handle_photo
from bot.handlers.risk import cmd_risk, cmd_kill, cmd_resume, cmd_checkstops
from bot.handlers.watchlist import cmd_watchlist
from bot.handlers.history import cmd_signals, cmd_stats, cmd_outcome
from workers.scanner import run_scanner
from workers.btc_scanner import run_btc_scanner
from workers.position_monitor import run_position_monitor
from config.settings import settings

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("kill",       "EMERGENCY — stop all trading & close positions"),
        BotCommand("resume",     "Restart trading after kill switch"),
        BotCommand("risk",       "Risk engine status"),
        BotCommand("signals",    "Recent signal history"),
        BotCommand("stats",      "Signal performance stats"),
        BotCommand("balance",    "Account balance"),
        BotCommand("positions",  "Open positions"),
        BotCommand("checkstops", "Stop loss alerts"),
        BotCommand("analyze",    "AI market analysis"),
        BotCommand("news",       "Market headlines"),
        BotCommand("watchlist",  "Manage watchlist"),
        BotCommand("status",     "Bot status"),
        BotCommand("help",       "All commands"),
    ])
    chat_id = settings.allowed_ids[0]
    asyncio.create_task(run_scanner(app.bot, chat_id))
    asyncio.create_task(run_btc_scanner(app.bot, chat_id))
    asyncio.create_task(run_position_monitor(app.bot, chat_id))
    print(f"Gold + BTC scanners + position monitor started for chat_id: {chat_id}")

def main():
    app = (
        Application.builder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .build()
    )
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
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("outcome", cmd_outcome))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_photo))
    print("Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()