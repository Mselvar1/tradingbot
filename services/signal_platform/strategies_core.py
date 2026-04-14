"""
Four strategy 'personalities' evaluated on stored candles (M15 primary, higher TF filter).
Scores are 0–100 heuristics for ranking and dashboard display — not guaranteed edge.
"""

from __future__ import annotations

import math
from typing import Any


def _ema(closes: list[float], period: int) -> float:
    if not closes:
        return 0.0
    if len(closes) < period:
        return closes[-1]
    k = 2.0 / (period + 1)
    v = sum(closes[:period]) / period
    for p in closes[period:]:
        v = p * k + v * (1 - k)
    return v


def _closes(rows: list[dict]) -> list[float]:
    return [float(r["close_price"]) for r in rows]


def _highs(rows: list[dict]) -> list[float]:
    return [float(r["high_price"]) for r in rows]


def _lows(rows: list[dict]) -> list[float]:
    return [float(r["low_price"]) for r in rows]


def eval_liquidity_sweep(m15: list[dict]) -> dict[str, Any]:
    """Equal-high / sweep rejection heuristic on M15."""
    if len(m15) < 12:
        return {"strategy": "liquidity_sweep", "score": 0, "direction": "neutral", "details": {}}
    h, l, c = _highs(m15), _lows(m15), _closes(m15)
    recent_high = max(h[-8:-1])
    recent_low = min(l[-8:-1])
    bull_sweep = l[-1] < recent_low * 1.0002 and c[-1] > (h[-2] + l[-2]) / 2
    bear_sweep = h[-1] > recent_high * 0.9998 and c[-1] < (h[-2] + l[-2]) / 2
    if bull_sweep:
        return {
            "strategy": "liquidity_sweep",
            "score": 72.0,
            "direction": "long",
            "details": {"pattern": "sell-side_liquidity_sweep_reclaim"},
        }
    if bear_sweep:
        return {
            "strategy": "liquidity_sweep",
            "score": 72.0,
            "direction": "short",
            "details": {"pattern": "buy-side_liquidity_sweep_reject"},
        }
    return {"strategy": "liquidity_sweep", "score": 35.0, "direction": "neutral", "details": {}}


def eval_trend_continuation(m15: list[dict], h1: list[dict]) -> dict[str, Any]:
    """EMA pullback in direction of H1 trend."""
    if len(m15) < 30 or len(h1) < 25:
        return {"strategy": "trend_continuation", "score": 0, "direction": "neutral", "details": {}}
    c15, c1 = _closes(m15), _closes(h1)
    ema21_15 = _ema(c15, 21)
    ema21_1 = _ema(c1, 21)
    price = c15[-1]
    h1_up = c1[-1] > ema21_1 and c1[-5] < c1[-1]
    h1_dn = c1[-1] < ema21_1 and c1[-5] > c1[-1]
    if h1_up and price <= ema21_15 * 1.002 and price >= ema21_15 * 0.995:
        return {
            "strategy": "trend_continuation",
            "score": 68.0,
            "direction": "long",
            "details": {"h1_bias": "up", "m15": "pullback_to_ema21"},
        }
    if h1_dn and price >= ema21_15 * 0.998 and price <= ema21_15 * 1.005:
        return {
            "strategy": "trend_continuation",
            "score": 68.0,
            "direction": "short",
            "details": {"h1_bias": "down", "m15": "pullback_to_ema21"},
        }
    return {"strategy": "trend_continuation", "score": 40.0, "direction": "neutral", "details": {}}


def eval_breakout_expansion(m15: list[dict]) -> dict[str, Any]:
    """Tight range then expansion bar."""
    if len(m15) < 25:
        return {"strategy": "breakout_expansion", "score": 0, "direction": "neutral", "details": {}}
    h, l, c = _highs(m15), _lows(m15), _closes(m15)
    ranges = [h[i] - l[i] for i in range(-20, 0)]
    avg_r = sum(ranges) / len(ranges) or 1e-9
    last_r = h[-1] - l[-1]
    hi20, lo20 = max(h[-21:-1]), min(l[-21:-1])
    inside = (hi20 - lo20) / (c[-2] or 1) * 100 < 0.45
    expand = last_r > avg_r * 2.0
    if inside and expand and c[-1] > hi20:
        return {
            "strategy": "breakout_expansion",
            "score": 70.0,
            "direction": "long",
            "details": {"compression": True, "range_mult": round(last_r / avg_r, 2)},
        }
    if inside and expand and c[-1] < lo20:
        return {
            "strategy": "breakout_expansion",
            "score": 70.0,
            "direction": "short",
            "details": {"compression": True, "range_mult": round(last_r / avg_r, 2)},
        }
    return {"strategy": "breakout_expansion", "score": 38.0, "direction": "neutral", "details": {}}


def eval_ema_momentum(m15: list[dict]) -> dict[str, Any]:
    """EMA8 > EMA21 > EMA50 stack on M15."""
    if len(m15) < 55:
        return {"strategy": "ema_momentum", "score": 0, "direction": "neutral", "details": {}}
    c = _closes(m15)
    e8, e21, e50 = _ema(c, 8), _ema(c, 21), _ema(c, 50)
    price = c[-1]
    if e8 > e21 > e50 and price > e8:
        return {
            "strategy": "ema_momentum",
            "score": 75.0,
            "direction": "long",
            "details": {"stack": "bullish", "ema8": e8, "ema21": e21},
        }
    if e8 < e21 < e50 and price < e8:
        return {
            "strategy": "ema_momentum",
            "score": 75.0,
            "direction": "short",
            "details": {"stack": "bearish", "ema8": e8, "ema21": e21},
        }
    return {"strategy": "ema_momentum", "score": 42.0, "direction": "neutral", "details": {}}


def run_all_strategies(bundle: dict[str, list[dict]]) -> list[dict[str, Any]]:
    m15 = bundle.get("M15") or []
    h1 = bundle.get("H1") or []
    return [
        eval_liquidity_sweep(m15),
        eval_trend_continuation(m15, h1),
        eval_breakout_expansion(m15),
        eval_ema_momentum(m15),
    ]
