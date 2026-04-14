"""
Signal platform: re-evaluate strategies, run validation jobs, optionally Telegram digest.
"""

import asyncio
import json

from config.settings import settings
from services.signal_platform.candles_store import run_full_candle_ingest
from services.signal_platform.strategy_runner import evaluate_and_store, latest_scores_summary
from services.signal_platform.validation_engine import run_full_validation_cycle

INTERVAL_SEC = 4 * 3600

_INST_LABEL = {
    "GOLD": "🪙 GOLD",
    "BTC-USD": "₿ BTC",
}


def _dir_badge(direction: str | None) -> str:
    d = (direction or "").strip().lower()
    if d in ("long", "buy", "bullish"):
        return "📈 Long"
    if d in ("short", "sell", "bearish"):
        return "📉 Short"
    return f"↔️ {direction or 'n/a'}"


def _validation_one_line(payload) -> str:
    if not isinstance(payload, dict):
        return str(payload)[:140]
    if payload.get("error"):
        extra = f" (n={payload['n']})" if payload.get("n") is not None else ""
        return f"⏸ skipped — {payload['error']}{extra}"
    if "interpretation" in payload and "p_value_shuffle_ge_real" in payload:
        p = payload.get("p_value_shuffle_ge_real")
        n = payload.get("n_trades", "?")
        interp = payload.get("interpretation", "")
        luck = " (may be luck)" if payload.get("random_wins_flag") else ""
        return f"✓ n={n} · p={p} · {interp}{luck}"
    if "in_sample" in payload:
        ins = payload.get("in_sample") or {}
        oos = payload.get("out_sample") or {}
        flag = " ⚠️ OOS much weaker than IS" if payload.get("oos_collapse_flag") else ""
        return (
            f"✓ in-sample: {ins.get('trades', 0)} trades, {ins.get('win_rate', 0):.0%} WR"
            f" · out-of-sample: {oos.get('trades', 0)} trades, {oos.get('win_rate', 0):.0%} WR{flag}"
        )
    if "win_rate" in payload and "trades" in payload:
        return (
            f"✓ {payload.get('trades', 0)} trades · {payload.get('win_rate', 0):.0%} win rate"
            f" · avg {payload.get('avg_pct', 0):+.3f}%"
        )
    return str(payload)[:140]


def _format_digest(scores: dict, validation_summary: dict) -> str:
    lines = [
        "📊 Signal platform digest",
        "Every 4h: strategy scores + validation snapshot.",
        "────────────────────────────",
        "",
        "🏆 Top strategy per market",
    ]
    for inst, rows in scores.items():
        label = _INST_LABEL.get(inst, inst)
        if not rows:
            lines.append(f"{label}")
            lines.append("   ⏳ No scores yet — candle feed may still be warming up.")
            continue
        best = max(rows, key=lambda x: x.get("score") or 0)
        strat = best.get("strategy", "?")
        sc = float(best.get("score") or 0)
        badge = _dir_badge(best.get("direction"))
        lines.append(f"{label}")
        lines.append(f"   ⭐ {strat} · score {sc:.0f} · {badge}")

    lines.extend(
        [
            "",
            "🔬 Validation (this cycle)",
            "Simple momentum proxy on M15; Monte Carlo needs closed outcomes in DB.",
        ]
    )
    val_order: list[tuple[str, str]] = [
        ("GOLD_backtest", "🪙 GOLD — backtest"),
        ("GOLD_walkforward", "🪙 GOLD — walk-forward"),
        ("BTC-USD_backtest", "₿ BTC — backtest"),
        ("BTC-USD_walkforward", "₿ BTC — walk-forward"),
        ("mc_gold", "🪙 GOLD — Monte Carlo shuffle"),
        ("mc_btc", "₿ BTC — Monte Carlo shuffle"),
    ]
    for key, title in val_order:
        if key not in validation_summary:
            continue
        lines.append(f"• {title}")
        lines.append(f"  {_validation_one_line(validation_summary[key])}")
    for k, v in sorted(validation_summary.items()):
        if k.endswith("_error") or k == "mc_error":
            lines.append(f"• ⚠️ {k}: {v}")

    lines.extend(["", "✅ Cycle complete · next digest in ~4h."])
    return "\n".join(lines)


async def run_signal_platform_scheduler(bot, chat_id: int):
    await asyncio.sleep(90)
    print("Signal platform scheduler started (4-hour interval)...")
    while True:
        try:
            if not getattr(settings, "signal_platform_enabled", True):
                await asyncio.sleep(INTERVAL_SEC)
                continue
            if not settings.database_url:
                await asyncio.sleep(INTERVAL_SEC)
                continue

            await run_full_candle_ingest("GOLD")
            for inst in ("GOLD", "BTC-USD"):
                await evaluate_and_store(inst)
            val_summary = await run_full_validation_cycle()
            scores = await latest_scores_summary()

            try:
                msg = _format_digest(scores, val_summary)
                await bot.send_message(chat_id=chat_id, text=msg[:4000])
            except Exception as e:
                print(f"Signal platform digest Telegram error: {e}")

            print(f"Signal platform cycle done: {json.dumps({k: str(v)[:80] for k,v in val_summary.items()})[:500]}")

        except Exception as e:
            print(f"Signal platform scheduler error: {e}")

        await asyncio.sleep(INTERVAL_SEC)
