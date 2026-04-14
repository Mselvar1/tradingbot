"""
Circuit breaker: after N consecutive stop-loss outcomes, pause automated
new entries for M hours (persisted in Postgres so restarts respect it).
"""

from __future__ import annotations

import datetime

from config.settings import settings
from services.memory import get_pool


async def get_state() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT consecutive_sl, paused_until, updated_at FROM circuit_breaker WHERE id = 1"
        )
    if not row:
        return {"consecutive_sl": 0, "paused_until": None, "updated_at": None}
    return {
        "consecutive_sl": row["consecutive_sl"],
        "paused_until": row["paused_until"],
        "updated_at": row["updated_at"],
    }


async def is_paused() -> bool:
    if not getattr(settings, "signal_platform_enabled", True):
        return False
    st = await get_state()
    pu = st.get("paused_until")
    if pu is None:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    if pu.tzinfo is None:
        pu = pu.replace(tzinfo=datetime.timezone.utc)
    return now < pu


async def on_trade_outcome(result: str, ticker: str) -> None:
    """
    Call after an outcome is recorded. result: sl | tp1 | tp2 | tp3 | manual_close
    """
    if not getattr(settings, "signal_platform_enabled", True):
        return
    pool = await get_pool()
    streak_limit = int(getattr(settings, "circuit_breaker_sl_streak", 8) or 8)
    pause_h = int(getattr(settings, "circuit_breaker_pause_hours", 24) or 24)

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT consecutive_sl FROM circuit_breaker WHERE id = 1 FOR UPDATE"
            )
            cur = int(row["consecutive_sl"]) if row else 0

            if result == "sl":
                cur += 1
            elif result in ("tp1", "tp2", "tp3"):
                cur = 0

            if result == "sl" and cur >= streak_limit:
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                    hours=pause_h
                )
                await conn.execute(
                    """
                    UPDATE circuit_breaker
                    SET consecutive_sl = 0, paused_until = $1, updated_at = NOW()
                    WHERE id = 1
                    """,
                    until,
                )
                print(
                    f"Circuit breaker: {streak_limit} consecutive SL on {ticker} — "
                    f"pausing new signals {pause_h}h until {until.isoformat()}"
                )
            else:
                await conn.execute(
                    """
                    UPDATE circuit_breaker
                    SET consecutive_sl = $1, updated_at = NOW()
                    WHERE id = 1
                    """,
                    cur,
                )


async def clear_pause_manual() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE circuit_breaker SET paused_until = NULL, updated_at = NOW() WHERE id = 1"
        )
