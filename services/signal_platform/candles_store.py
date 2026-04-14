"""
Persist OHLCV candles from Capital.com (Gold) and Binance (BTC) into Postgres.
"""

from __future__ import annotations

import datetime
from typing import Any

import aiohttp

from config.settings import settings
from services.data.capital import capital_client
from services.memory import get_pool


def _parse_ts(val: Any) -> datetime.datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        sec = val / 1000.0 if val > 1e12 else float(val)
        return datetime.datetime.utcfromtimestamp(sec).replace(tzinfo=datetime.timezone.utc)
    if isinstance(val, str):
        try:
            if val.endswith("Z"):
                val = val[:-1] + "+00:00"
            return datetime.datetime.fromisoformat(val)
        except Exception:
            return None
    return None


async def upsert_candle_rows(rows: list[dict]) -> int:
    """Each row: instrument, timeframe, open_time (datetime), o,h,l,c, volume, source."""
    if not rows:
        return 0
    pool = await get_pool()
    n = 0
    async with pool.acquire() as conn:
        for r in rows:
            await conn.execute(
                """
                INSERT INTO candles (
                    instrument, timeframe, open_time,
                    open_price, high_price, low_price, close_price, volume, source
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (instrument, timeframe, open_time, source) DO UPDATE SET
                    open_price = EXCLUDED.open_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume
                """,
                r["instrument"],
                r["timeframe"],
                r["open_time"],
                float(r["open_price"]),
                float(r["high_price"]),
                float(r["low_price"]),
                float(r["close_price"]),
                float(r.get("volume") or 0),
                r["source"],
            )
            n += 1
    return n


async def fetch_candles_from_db(
    instrument: str, timeframe: str, limit: int = 200, source: str | None = None
) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if source:
            rows = await conn.fetch(
                """
                SELECT open_time, open_price, high_price, low_price, close_price, volume, source
                FROM candles
                WHERE instrument = $1 AND timeframe = $2 AND source = $3
                ORDER BY open_time ASC
                LIMIT $4
                """,
                instrument,
                timeframe,
                source,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT open_time, open_price, high_price, low_price, close_price, volume, source
                FROM candles
                WHERE instrument = $1 AND timeframe = $2
                ORDER BY open_time ASC
                LIMIT $3
                """,
                instrument,
                timeframe,
                limit,
            )
    return [dict(r) for r in rows]


async def ingest_capital_epic(
    epic: str, instrument: str, resolution: str, tf_label: str, max_candles: int = 120
) -> int:
    await capital_client.ensure_session()
    raw = await capital_client.get_ohlcv(epic, resolution, max_candles)
    if not raw:
        return 0
    rows = []
    for c in raw:
        ts = _parse_ts(c.get("snapshot_time"))
        if ts is None:
            continue
        rows.append(
            {
                "instrument": instrument,
                "timeframe": tf_label,
                "open_time": ts,
                "open_price": c["open"],
                "high_price": c["high"],
                "low_price": c["low"],
                "close_price": c["close"],
                "volume": float(c.get("volume") or 0),
                "source": "capital.com",
            }
        )
    return await upsert_candle_rows(rows)


async def _fetch_binance_klines(interval: str, limit: int) -> list[list]:
    base = settings.binance_base_url.rstrip("/")
    sym = settings.binance_symbol
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(
                f"{base}/api/v3/klines",
                params={"symbol": sym, "interval": interval, "limit": limit},
            ) as r:
                if r.status != 200:
                    return []
                return await r.json()
    except Exception as e:
        print(f"Binance klines ingest error ({interval}): {e}")
        return []


async def ingest_binance_btc(timeframe_binance: str, tf_label: str, limit: int = 200) -> int:
    """timeframe_binance: 15m, 1h, 4h, 1d"""
    k = await _fetch_binance_klines(timeframe_binance, limit)
    if not k:
        return 0
    rows = []
    for bar in k:
        open_ms = int(bar[0])
        ts = datetime.datetime.utcfromtimestamp(open_ms / 1000.0).replace(
            tzinfo=datetime.timezone.utc
        )
        rows.append(
            {
                "instrument": "BTC-USD",
                "timeframe": tf_label,
                "open_time": ts,
                "open_price": float(bar[1]),
                "high_price": float(bar[2]),
                "low_price": float(bar[3]),
                "close_price": float(bar[4]),
                "volume": float(bar[5]),
                "source": "binance.spot",
            }
        )
    return await upsert_candle_rows(rows)


async def run_full_candle_ingest(gold_epic: str = "GOLD") -> dict[str, int]:
    """Pull all configured TFs for Gold (Capital) and BTC (Binance)."""
    counts: dict[str, int] = {}
    if not getattr(settings, "signal_platform_enabled", True):
        return counts
    # Gold — Capital resolutions (best-effort; epic may differ per account)
    m15_res = ["MINUTE_15", "MINUTE_30", "MINUTE_5"]
    counts["GOLD_M15"] = 0
    for res in m15_res:
        try:
            n = await ingest_capital_epic(gold_epic, "GOLD", res, "M15", 96)
            if n > 0:
                counts["GOLD_M15"] = n
                break
        except Exception as e:
            print(f"Candle ingest GOLD M15 ({res}): {e}")
    pairs = [
        ("HOUR", "H1", 168),
        ("HOUR_4", "H4", 120),
        ("DAY", "D1", 90),
    ]
    for res, label, mx in pairs:
        try:
            n = await ingest_capital_epic(gold_epic, "GOLD", res, label, mx)
            counts[f"GOLD_{label}"] = n
        except Exception as e:
            print(f"Candle ingest GOLD {label}: {e}")
            counts[f"GOLD_{label}"] = 0
    # BTC — Binance
    bmap = [("15m", "M15"), ("1h", "H1"), ("4h", "H4"), ("1d", "D1")]
    for bint, label in bmap:
        try:
            n = await ingest_binance_btc(bint, label, 200)
            counts[f"BTC_{label}"] = n
        except Exception as e:
            print(f"Candle ingest BTC {label}: {e}")
            counts[f"BTC_{label}"] = 0
    return counts
