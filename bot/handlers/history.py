from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import auth_check
from services.signal_history import history

async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    recent = history.get_recent(5)
    if not recent:
        await update.message.reply_text("No signals sent yet.")
        return
    lines = []
    for s in reversed(recent):
        outcome = s["outcome"].upper()
        lines.append(
            f"#{s['id']} {s['ticker']} {s['action'].upper()}\n"
            f"Confidence: {s['confidence']}/100\n"
            f"Price: {s['price_at_signal']} | SL: {s['stop_loss']}\n"
            f"TP1: {s['tp1']} | R:R: {s['rr']}\n"
            f"Status: {outcome}\n"
            f"Sent: {s['sent_at'][:16]}"
        )
    await update.message.reply_text(
        "*Recent Signals:*\n\n" + "\n\n".join(lines),
        parse_mode="Markdown"
    )

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    stats = history.get_stats()
    if stats["total_signals"] == 0:
        await update.message.reply_text("No signals recorded yet.")
        return
    msg = (
        f"*Signal Statistics*\n\n"
        f"Total signals: {stats['total_signals']}\n"
        f"Closed: {stats['closed']}\n"
        f"Pending: {stats['pending']}\n"
        f"Wins: {stats.get('wins', 0)}\n"
        f"Losses: {stats.get('losses', 0)}\n"
        f"Win rate: {stats['win_rate']}%\n"
        f"Avg P&L: ${stats['avg_pnl']}\n"
        f"Total P&L: ${stats['total_pnl']}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_outcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage: /outcome [id] [win/loss] [pnl]\n"
            "Example: /outcome 1 win 45.50"
        )
        return
    try:
        signal_id = int(ctx.args[0])
        outcome = ctx.args[1].lower()
        pnl = float(ctx.args[2]) if len(ctx.args) > 2 else 0
        if outcome not in ["win", "loss"]:
            await update.message.reply_text("Outcome must be 'win' or 'loss'")
            return
        success = history.mark_outcome(signal_id, outcome, pnl)
        if success:
            await update.message.reply_text(
                f"Signal #{signal_id} marked as {outcome.upper()}\n"
                f"P&L: ${pnl}"
            )
        else:
            await update.message.reply_text(f"Signal #{signal_id} not found.")
    except ValueError:
        await update.message.reply_text("Invalid format. Use: /outcome 1 win 45.50")