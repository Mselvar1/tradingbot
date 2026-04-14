"""
Binance public spot API — reference liquidity for BTC/USDT.

No API key required. Used to enrich BTC scanner with:
- Real 1m traded volume (base asset) vs 20m average
- Top-of-book bid/ask imbalance (aggressive flow proxy)

Data is cached and refreshed by run_binance_flow_loop() every 30s.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from config.settings import settings

POLL_INTERVAL_SEC = 30


class BinanceFlow:
    """In-memory snapshot of last successful Binance fetch."""

    def __init__(self) -> None:
        self._snapshot: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._last_ts: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return dict(self._snapshot)

    async def refresh(self) -> None:
        if not getattr(settings, "binance_enabled", True):
            return
        async with self._lock:
            snap = await _fetch_binance_spot_metrics()
            self._snapshot = snap
            self._last_ts = time.time() if snap.get("ok") else self._last_ts

    def age_sec(self) -> float:
        if not self._snapshot.get("ok"):
            return 1e9
        return max(0.0, time.time() - self._last_ts)


binance_flow = BinanceFlow()


async def _fetch_binance_spot_metrics() -> dict[str, Any]:
    """
    Pulls BTCUSDT 1m klines + bookTicker. Returns dict with ok/error.
    """
    base = getattr(settings, "binance_base_url", "https://api.binance.com").rstrip("/")
    symbol = getattr(settings, "binance_symbol", "BTCUSDT")
    out: dict[str, Any] = {
        "ok": False,
        "symbol": symbol,
        "price_usdt": 0.0,
        "volume_1m_btc": 0.0,
        "volume_20m_avg_btc": 0.0,
        "volume_ratio": 0.0,
        "book_bid_qty": 0.0,
        "book_ask_qty": 0.0,
        "book_imbalance": 0.0,
        "book_imbalance_label": "n/a",
        "error": None,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{base}/api/v3/klines",
                params={"symbol": symbol, "interval": "1m", "limit": 25},
            ) as r:
                if r.status != 200:
                    txt = await r.text()
                    out["error"] = f"klines {r.status}: {txt[:120]}"
                    return out
                klines = await r.json()

            async with session.get(
                f"{base}/api/v3/ticker/bookTicker",
                params={"symbol": symbol},
            ) as r2:
                if r2.status != 200:
                    txt = await r.text()
                    out["error"] = f"bookTicker {r2.status}: {txt[:120]}"
                    return out
                book = await r2.json()

        if not klines or len(klines) < 3:
            out["error"] = "empty klines"
            return out

        # Last row is the current (possibly incomplete) candle; use previous as last closed 1m.
        last_closed = klines[-2]
        vol_1m = float(last_closed[5])
        closes = [float(k[5]) for k in klines[-22:-2]]
        avg20 = sum(closes) / len(closes) if closes else vol_1m
        ratio = vol_1m / avg20 if avg20 > 0 else 1.0

        bid_qty = float(book.get("bidQty", 0) or 0)
        ask_qty = float(book.get("askQty", 0) or 0)
        denom = bid_qty + ask_qty
        if denom > 0:
            imbalance = (bid_qty - ask_qty) / denom
        else:
            imbalance = 0.0

        if imbalance > 0.12:
            imb_label = "bid-heavy (buy pressure)"
        elif imbalance < -0.12:
            imb_label = "ask-heavy (sell pressure)"
        else:
            imb_label = "balanced"

        bp = float(book.get("bidPrice", 0) or 0)
        ap = float(book.get("askPrice", 0) or 0)
        mid = (bp + ap) / 2 if bp and ap else float(last_closed[4])
        out.update(
            ok=True,
            price_usdt=mid,
            volume_1m_btc=round(vol_1m, 8),
            volume_20m_avg_btc=round(avg20, 8),
            volume_ratio=round(ratio, 3),
            book_bid_qty=round(bid_qty, 6),
            book_ask_qty=round(ask_qty, 6),
            book_imbalance=round(imbalance, 4),
            book_imbalance_label=imb_label,
        )
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


async def run_binance_flow_loop() -> None:
    """Background task: keep Binance snapshot fresh for BTC scanner."""
    await asyncio.sleep(8)
    print("Binance spot flow loop started (30s poll, public API)...")
    while True:
        try:
            if getattr(settings, "binance_enabled", True):
                await binance_flow.refresh()
        except Exception as e:
            print(f"Binance flow loop error: {e}")
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def binance_snapshot_for_scan() -> dict[str, Any]:
    """
    Returns the freshest snapshot for get_btc_data / scan_btc.
    If cache is stale or empty, refreshes once.
    """
    if not getattr(settings, "binance_enabled", True):
        return {"ok": False, "disabled": True}
    snap = binance_flow.snapshot()
    if snap.get("ok") and binance_flow.age_sec() < 60:
        return snap
    await binance_flow.refresh()
    return binance_flow.snapshot()
