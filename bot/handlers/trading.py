from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import auth_check
from services.execution.paper import broker

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /buy AAPL 500")
        return
    ticker = ctx.args[0].upper()
    try:
        amount = float(ctx.args[1])
    except ValueError:
        await update.message.reply_text("Amount must be a number.")
        return
    r = await broker.buy(ticker, amount)
    if r["status"] == "error":
        await update.message.reply_text(f"Buy failed: {r['reason']}")
    else:
        await update.message.reply_text(
            f"PAPER BUY filled\n"
            f"{ticker}: {r['qty']} shares @ ${r['price']:.2f}\n"
            f"Cost: ${amount:.2f}"
        )

async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: /sell AAPL")
        return
    ticker = ctx.args[0].upper()
    r = await broker.sell(ticker)
    if r["status"] == "error":
        await update.message.reply_text(f"Sell failed: {r['reason']}")
    else:
        sign = "+" if r["pnl"] >= 0 else ""
        await update.message.reply_text(
            f"PAPER SELL filled\n"
            f"{ticker} @ ${r['price']:.2f}\n"
            f"P&L: {sign}${r['pnl']:.2f}"
        )

async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    positions = await broker.get_positions()
    if not positions:
        await update.message.reply_text("No open positions.")
        return
    lines = []
    for p in positions:
        sign = "+" if p["pnl"] >= 0 else ""
        lines.append(
            f"{p['ticker']}: {round(p['qty'],4)} shares @ "
            f"${p['entry_price']:.2f} | "
            f"P&L: {sign}${p['pnl']:.2f}"
        )
    await update.message.reply_text(
        "*Positions:*\n" + "\n".join(lines),
        parse_mode="Markdown"
    )

async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    b = broker.get_balance()
    await update.message.reply_text(f"Paper balance: ${b:,.2f}")