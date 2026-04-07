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
        "/analyze [ticker] - AI analysis\n"
        "/news [topic]    - Headlines\n"
        "/buy [ticker] [amount] - Paper buy\n"
        "/sell [ticker]   - Paper sell\n"
        "/positions       - Open positions\n"
        "/balance         - Balance"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    await update.message.reply_text(
        "Status: online\nMode: PAPER\nPositions: 0"
    )