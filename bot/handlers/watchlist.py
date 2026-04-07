from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import auth_check
from services.watchlist import watchlist

async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    if not ctx.args:
        tickers = watchlist.get()
        if not tickers:
            await update.message.reply_text("Watchlist is empty.")
            return
        await update.message.reply_text(
            "*Watchlist:*\n" + "\n".join(f"• {t}" for t in tickers),
            parse_mode="Markdown"
        )
        return
    action = ctx.args[0].lower()
    if action == "add" and len(ctx.args) > 1:
        ticker = ctx.args[1].upper()
        if watchlist.add(ticker):
            await update.message.reply_text(f"{ticker} added to watchlist.")
        else:
            await update.message.reply_text(f"{ticker} already in watchlist.")
    elif action == "remove" and len(ctx.args) > 1:
        ticker = ctx.args[1].upper()
        if watchlist.remove(ticker):
            await update.message.reply_text(f"{ticker} removed from watchlist.")
        else:
            await update.message.reply_text(f"{ticker} not in watchlist.")
    else:
        await update.message.reply_text(
            "Usage:\n"
            "/watchlist — show list\n"
            "/watchlist add AAPL\n"
            "/watchlist remove AAPL"
        )