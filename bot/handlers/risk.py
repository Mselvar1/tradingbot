from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import auth_check
from services.risk import risk
from services.data.capital import capital_client

async def cmd_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    status = risk.get_status()
    kill = "ACTIVE — trading halted" if status["kill_switch"] else "inactive"
    msg = (
        f"*Risk Engine Status*\n"
        f"Kill switch: {kill}\n"
        f"Daily loss: ${status['daily_loss']:.2f} / ${status['daily_loss_limit']:.2f}\n"
        f"Max position size: ${status['max_position_size']:.2f}\n"
        f"Max open positions: {status['max_open_positions']}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    risk.activate_kill_switch()
    await update.message.reply_text(
        "KILL SWITCH ACTIVATED\n"
        "Scanner and executor halted — no new trades.\n\n"
        "Closing all open Capital.com positions..."
    )

    try:
        await capital_client.ensure_session()
        positions = await capital_client.get_positions()
        if not positions:
            await update.message.reply_text("No open positions found.")
        else:
            closed, failed = [], []
            for p in positions:
                deal_id = p.get("deal_id")
                name = p.get("name") or p.get("epic", "unknown")
                if not deal_id:
                    continue
                result = await capital_client.close_position(deal_id)
                if result.get("status") == "success":
                    pnl = p.get("pnl", 0)
                    closed.append(f"{name} — P&L: {pnl:+.2f}")
                else:
                    failed.append(f"{name} (deal: {deal_id})")

            lines = []
            if closed:
                lines.append("Closed:\n" + "\n".join(f"  {c}" for c in closed))
            if failed:
                lines.append("Failed to close:\n" + "\n".join(f"  {f}" for f in failed))
            await update.message.reply_text("\n\n".join(lines) if lines else "Done.")
    except Exception as e:
        await update.message.reply_text(f"Position close error: {e}")

    await update.message.reply_text("Send /resume when you want to restart trading.")

async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    risk.deactivate_kill_switch()

    account_line = ""
    try:
        await capital_client.ensure_session()
        account = await capital_client.get_account_balance()
        balance = account.get("balance", 0)
        available = account.get("available", 0)
        account_line = f"\nAccount: ${balance:.2f} (available: ${available:.2f})"
    except Exception:
        pass

    await update.message.reply_text(
        f"Kill switch deactivated. Trading resumed.{account_line}\n"
        "Scanner will pick up at the next interval."
    )

async def cmd_checkstops(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await auth_check(update): return
    try:
        await capital_client.ensure_session()
        positions = await capital_client.get_positions()
    except Exception as e:
        await update.message.reply_text(f"Error fetching positions: {e}")
        return
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
