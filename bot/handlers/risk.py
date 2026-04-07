from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import auth_check
from services.risk import risk
from services.execution.paper import broker

async def cmd_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    status = risk.get_status()
    kill = "ACTIVE - trading halted" if status["kill_switch"] else "inactive"
    msg = (
        f"*Risk Engine Status*\n"
        f"Kill switch: {kill}\n"
        f"Daily loss: ${status['daily_loss']:.2f} "
        f"/ ${status['daily_loss_limit']:.2f}\n"
        f"Max position size: ${status['max_position_size']:.2f}\n"
        f"Max open positions: {status['max_open_positions']}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    risk.activate_kill_switch()
    await update.message.reply_text(
        "KILL SWITCH ACTIVATED\n"
        "All trading halted.\n"
        "Send /resume to reactivate."
    )

async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    risk.deactivate_kill_switch()
    await update.message.reply_text(
        "Kill switch deactivated.\n"
        "Trading resumed."
    )

async def cmd_checkstops(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    positions = await broker.get_positions()
    alerts = risk.check_stop_loss(positions)
    if not alerts:
        await update.message.reply_text("No stop loss alerts.")
        return
    lines = []
    for a in alerts:
        lines.append(
            f"STOP LOSS HIT: {a['ticker']}\n"
            f"P&L: {a['pnl_pct']}% (${a['pnl_usd']:.2f})"
        )
    await update.message.reply_text(
        "*Stop Loss Alerts*\n" + "\n\n".join(lines),
        parse_mode="Markdown"
    )