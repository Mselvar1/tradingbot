"""
Price Direction Tracker.

Polls Capital.com every 30 seconds during trading hours.
Maintains a rolling 20-snapshot history per ticker and builds a
human-readable "price narrative" that is injected into every Claude prompt.
"""

import asyncio
import datetime
from collections import deque

from services.data.capital import capital_client

HISTORY_SIZE  = 20
POLL_INTERVAL = 30   # seconds


# ─── Indicator helpers ─────────────────────────────────────────────────────────

def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 0.001
    return round(100 - 100 / (1 + ag / al), 1)


def _ema(prices: list, period: int) -> float:
    if not prices:
        return 0.0
    if len(prices) < period:
        return round(prices[-1], 4)
    k = 2.0 / (period + 1)
    v = sum(prices[:period]) / period
    for p in prices[period:]:
        v = p * k + v * (1 - k)
    return round(v, 4)


def _detect_fvg(candles: list, lookback: int = 20) -> list:
    fvgs = []
    for i in range(2, min(len(candles), lookback)):
        bull = candles[i]["low"] - candles[i - 2]["high"]
        if bull > 0:
            fvgs.append({"type": "bullish",
                         "low":  round(candles[i - 2]["high"], 2),
                         "high": round(candles[i]["low"],      2)})
        bear = candles[i - 2]["low"] - candles[i]["high"]
        if bear > 0:
            fvgs.append({"type": "bearish",
                         "low":  round(candles[i]["high"],     2),
                         "high": round(candles[i - 2]["low"],  2)})
    return fvgs[:4]


# ─── Core Tracker ─────────────────────────────────────────────────────────────

class PriceTracker:

    def __init__(self):
        self._history: dict[str, deque] = {}
        self._candles: dict[str, list]  = {}

    def _hist(self, label: str) -> deque:
        if label not in self._history:
            self._history[label] = deque(maxlen=HISTORY_SIZE)
        return self._history[label]

    def _build_snapshot(self, label: str, candles: list, price: float) -> dict:
        closes = [c["close"] for c in candles]
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]

        rsi_now  = _rsi(closes)
        rsi_prev = _rsi(closes[:-8]) if len(closes) > 22 else rsi_now
        ema8     = _ema(closes,  8)
        ema21    = _ema(closes, 21)
        ema50    = _ema(closes, 50)

        # Trend via EMA stack
        if ema8 > ema21 > ema50:
            trend = "bullish"
        elif ema8 < ema21 < ema50:
            trend = "bearish"
        else:
            trend = "ranging"

        # Momentum: 5-candle rate of change
        mom_pct = 0.0
        if len(closes) >= 6 and closes[-6]:
            mom_pct = (closes[-1] - closes[-6]) / closes[-6] * 100
        if abs(mom_pct) > 0.35:
            momentum = "strong_bullish" if mom_pct > 0 else "strong_bearish"
        elif abs(mom_pct) < 0.08:
            momentum = "weak"
        else:
            momentum = "moderate_bullish" if mom_pct > 0 else "moderate_bearish"

        # RSI trend and divergence
        rsi_trend = (
            "rising"  if rsi_now > rsi_prev + 2 else
            "falling" if rsi_now < rsi_prev - 2 else
            "flat"
        )
        rsi_divergence = "none"
        if len(closes) >= 8:
            price_dir = closes[-1] - closes[-5]
            rsi_dir   = rsi_now - rsi_prev
            if price_dir > 0 and rsi_dir < -2:
                rsi_divergence = "bearish_divergence"
            elif price_dir < 0 and rsi_dir > 2:
                rsi_divergence = "bullish_divergence"

        # Consecutive candle direction (+ = bullish, - = bearish)
        consec = 0
        for c in reversed(candles[-5:]):
            is_bull = c["close"] > c["open"]
            if consec == 0:
                consec = 1 if is_bull else -1
            elif (consec > 0) == is_bull:
                consec += (1 if is_bull else -1)
            else:
                break

        r_high = round(max(highs[-20:]) if len(highs) >= 20 else max(highs), 2)
        r_low  = round(min(lows[-20:])  if len(lows)  >= 20 else min(lows),  2)
        fvgs   = _detect_fvg(candles[-20:] if len(candles) >= 20 else candles)
        nearest_fvg = (
            min(fvgs, key=lambda f: abs(price - (f["low"] + f["high"]) / 2))
            if fvgs else None
        )

        return {
            "label":          label,
            "price":          price,
            "rsi":            rsi_now,
            "rsi_prev":       rsi_prev,
            "rsi_trend":      rsi_trend,
            "rsi_divergence": rsi_divergence,
            "trend":          trend,
            "momentum":       momentum,
            "ema8":           ema8,
            "ema21":          ema21,
            "ema50":          ema50,
            "recent_high":    r_high,
            "recent_low":     r_low,
            "fvg_zones":      fvgs,
            "nearest_fvg":    nearest_fvg,
            "consec_candles": consec,
            "ts":             datetime.datetime.utcnow().isoformat(),
        }

    # ── Public API ────────────────────────────────────────────────────────────

    async def update(self, epic: str, label: str) -> dict | None:
        try:
            await capital_client.ensure_session()
            candles = await capital_client.get_ohlcv(epic, "MINUTE", 50)
            if not candles:
                return None
            price_data = await capital_client.get_price(epic)
            price = price_data.get("price") or candles[-1]["close"]
            snap = self._build_snapshot(label, candles, price)
            self._hist(label).append(snap)
            self._candles[label] = candles
            return snap
        except Exception as e:
            print(f"PriceTracker.update({label}): {e}")
            return None

    def get_latest(self, label: str) -> dict | None:
        h = self._history.get(label)
        return h[-1] if h else None

    def get_candles(self, label: str) -> list:
        return self._candles.get(label, [])

    def get_narrative(self, label: str) -> str:
        snap = self.get_latest(label)
        if not snap:
            return ""

        div_part = ""
        if snap["rsi_divergence"] != "none":
            div_part = f", {snap['rsi_divergence'].replace('_', ' ')}"

        fvg_part = ""
        if snap["nearest_fvg"]:
            f = snap["nearest_fvg"]
            fvg_part = f", {f['type']} FVG {f['low']:.2f}–{f['high']:.2f}"

        consec_part = ""
        c = snap["consec_candles"]
        if abs(c) >= 3:
            consec_part = f", {abs(c)} consec {'bullish' if c > 0 else 'bearish'} candles"

        return (
            f"LIVE ({label}): {snap['trend'].upper()} trend, "
            f"{snap['momentum'].replace('_', ' ')} momentum, "
            f"RSI {snap['rsi']:.0f} ({snap['rsi_trend']})"
            f"{div_part}{fvg_part}{consec_part}. "
            f"Price {snap['price']:.2f} | "
            f"Range: {snap['recent_low']:.2f}–{snap['recent_high']:.2f}"
        )


price_tracker = PriceTracker()


# ─── Background Task ──────────────────────────────────────────────────────────

async def run_price_tracker():
    await asyncio.sleep(5)
    print("Price tracker started (30-second polling)...")
    while True:
        try:
            now = datetime.datetime.utcnow()
            t   = now.hour * 60 + now.minute
            # Gold: extended window around London and NY sessions
            if 6 * 60 + 30 <= t <= 16 * 60:
                await price_tracker.update("GOLD", "GOLD")
            # BTC: skip dead zone 21:00-02:00 UTC
            if not (21 * 60 <= t or t < 2 * 60):
                await price_tracker.update("BTCUSD", "BTC-USD")
        except Exception as e:
            print(f"Price tracker loop error: {e}")
        await asyncio.sleep(POLL_INTERVAL)
