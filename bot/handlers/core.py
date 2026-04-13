from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import auth_check

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    await update.message.reply_text(
        "Trading bot online.\n"
        "Mode: PAPER (no real money)\n"
        "Type /help for commands."
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    await update.message.reply_text(
        "EMERGENCY\n"
        "/kill     — Stop all trading & close positions\n"
        "/resume   — Restart trading\n"
        "/risk     — Risk engine status\n\n"
        "SIGNALS & ANALYSIS\n"
        "/signals  — Recent signal history\n"
        "/stats    — Signal performance\n"
        "/analyze [ticker] — AI analysis\n"
        "/news [topic]     — Headlines\n\n"
        "ACCOUNT\n"
        "/balance    — Account balance\n"
        "/positions  — Open positions\n"
        "/checkstops — Stop loss alerts\n\n"
        "OTHER\n"
        "/status   — Bot status\n"
        "/watchlist — Manage watchlist\n"
        "/outcome [id] [win/loss] [pnl] — Log result"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    await update.message.reply_text(
        "Status: online\nMode: PAPER\nPositions: 0"
    )