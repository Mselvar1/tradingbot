"""
BTC rolling performance digest — Telegram every N hours (default 8).

Shows last-window stats only (not all-time): outcomes win rate + net € from
trade_exits for BTC-USD in that window.
"""

import asyncio

from config.settings import settings
from services.memory import fetch_btc_window_performance


def _format_digest(d: dict, cadence_hint: str) -> str:
    h = int(d.get("hours") or 8)
    n = int(d.get("outcomes_n") or 0)
    w = int(d.get("wins") or 0)
    losses = int(d.get("losses") or 0)
    other = int(d.get("other") or 0)
    wr = d.get("win_rate")
    avg_pct = d.get("avg_pnl_pct")
    te_n = int(d.get("trade_exits_n") or 0)
    net_eur = d.get("net_pnl_euros")

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"BTC · last {h}h performance (not all-time)",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Closed (outcomes): {n}  |  TP wins: {w}  |  SL: {losses}  |  Other: {other}",
        f"Win rate (TP): {wr}%",
    ]
    if avg_pct is not None:
        lines.append(f"Avg PnL % (outcomes): {avg_pct}%")
    lines.append("")
    lines.append(f"Managed exits (trade_exits): {te_n}")
    if net_eur is not None:
        sign = "+" if net_eur >= 0 else ""
        lines.append(f"Net P&L (€, managed exits): {sign}{net_eur:.2f} €")
    else:
        lines.append("Net P&L (€): —")
    lines.append("")
    lines.append(f"Cadence note: {cadence_hint}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


async def run_btc_performance_digest(bot, chat_id: int):
    hours = max(1, int(getattr(settings, "btc_performance_digest_hours", 8)))
    startup = max(30, int(getattr(settings, "btc_performance_digest_startup_delay_seconds", 300)))
    interval = hours * 3600

    gap = int(getattr(settings, "btc_min_signal_gap_seconds", 300))
    max_per_h = 3600 // gap if gap > 0 else 0
    cadence_hint = (
        f"up to ~{max_per_h} BTC entries/hour (gap {gap}s); "
        f"Claude BTC cap {getattr(settings, 'btc_claude_max_calls_per_hour', 120)}/h"
    )

    print(f"BTC performance digest: every {hours}h, first run after {startup}s")

    first = True
    while True:
        try:
            if first:
                await asyncio.sleep(startup)
                first = False
            else:
                await asyncio.sleep(interval)

            if not getattr(settings, "btc_performance_digest_enabled", True):
                continue

            d = await fetch_btc_window_performance(hours=hours)
            msg = _format_digest(d, cadence_hint)
            await bot.send_message(chat_id=chat_id, text=msg)
            if getattr(settings, "public_channel_id", None):
                try:
                    await bot.send_message(chat_id=settings.public_channel_id, text=msg)
                except Exception as e:
                    print(f"BTC digest channel post failed: {e}")
            print("BTC performance digest sent")
        except Exception as e:
            print(f"BTC performance digest error: {e}")
