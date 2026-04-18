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
from workers.trade_manager import run_trade_manager
from workers.weekly_report import run_weekly_report
from workers.btc_performance_digest import run_btc_performance_digest
from workers.candle_feed import run_candle_feed
from workers.signal_platform_scheduler import run_signal_platform_scheduler
from services.price_tracker import run_price_tracker
from services.data.binance_market import run_binance_flow_loop
from services.memory import init_db
from config.settings import settings

async def post_init(app):
    await init_db()
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
    if getattr(settings, "gold_scanner_enabled", True):
        asyncio.create_task(run_scanner(app.bot, chat_id))
    else:
        print("Gold scanner disabled (GOLD_SCANNER_ENABLED=false) — BTC-only mode")
    asyncio.create_task(run_btc_scanner(app.bot, chat_id))
    asyncio.create_task(run_position_monitor(app.bot, chat_id))
    asyncio.create_task(run_trade_manager(app.bot, chat_id))
    asyncio.create_task(run_price_tracker())
    asyncio.create_task(run_binance_flow_loop())
    asyncio.create_task(run_candle_feed())
    asyncio.create_task(run_signal_platform_scheduler(app.bot, chat_id))
    asyncio.create_task(run_weekly_report(app.bot, chat_id))
    if getattr(settings, "btc_performance_digest_enabled", True):
        asyncio.create_task(run_btc_performance_digest(app.bot, chat_id))
    print(
        "All workers started: "
        + ("gold scanner, " if getattr(settings, "gold_scanner_enabled", True) else "")
        + "position monitor, trade manager, price tracker, "
        "Binance flow, candle feed, signal platform, weekly report"
        + (", BTC digest" if getattr(settings, "btc_performance_digest_enabled", True) else "")
        + " — "
        f"chat_id: {chat_id}"
    )

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