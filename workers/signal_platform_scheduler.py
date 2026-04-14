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


def _format_digest(scores: dict, validation_summary: dict) -> str:
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "SIGNAL PLATFORM DIGEST",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for inst, rows in scores.items():
        if not rows:
            lines.append(f"{inst}: (no scores yet — run candle feed)")
            continue
        best = max(rows, key=lambda x: x.get("score") or 0)
        lines.append(
            f"{inst}: best={best['strategy']} "
            f"score={best['score']:.0f} dir={best.get('direction')}"
        )
    lines.append("")
    lines.append("Validation cycle keys: " + ", ".join(validation_summary.keys())[:200])
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
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
