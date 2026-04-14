"""
Ingest multi-timeframe candles into Postgres (Gold via Capital, BTC via Binance).
"""

import asyncio

from config.settings import settings
from services.signal_platform.candles_store import run_full_candle_ingest


async def run_candle_feed():
    await asyncio.sleep(45)
    print("Candle feed worker started (15-minute interval)...")
    while True:
        try:
            if not getattr(settings, "signal_platform_enabled", True):
                await asyncio.sleep(900)
                continue
            if not settings.database_url:
                await asyncio.sleep(900)
                continue
            counts = await run_full_candle_ingest("GOLD")
            if any(v > 0 for v in counts.values()):
                print(f"Candle feed upserted: {counts}")
        except Exception as e:
            print(f"Candle feed error: {e}")
        await asyncio.sleep(900)
