"""
Scheduled validation: simple backtest proxy, walk-forward split, Monte Carlo shuffle
on recent outcome PnL%, persisted as JSON snapshots for the dashboard.
"""

from __future__ import annotations

import json
import random
from statistics import mean, pstdev

from services.memory import get_pool, get_outcomes_for_analysis
from services.signal_platform.candles_store import fetch_candles_from_db


async def _save_snapshot(job_type: str, instrument: str | None, payload: dict) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO validation_snapshots (job_type, instrument, payload)
            VALUES ($1, $2, $3::jsonb)
            """,
            job_type,
            instrument,
            json.dumps(payload),
        )


def _simple_momentum_backtest(closes: list[float], hold: int = 3) -> dict:
    """Long when close > ema21 and prev was below; short symmetric. Forward hold bars."""
    if len(closes) < 60:
        return {"error": "insufficient_bars", "trades": 0}
    trades = []
    i = 40
    while i < len(closes) - hold - 1:
        window = closes[i - 21 : i]
        ema = sum(window[-21:]) / 21
        sig = 0
        if closes[i] > ema and closes[i - 1] <= ema:
            sig = 1
        elif closes[i] < ema and closes[i - 1] >= ema:
            sig = -1
        if sig != 0:
            ret = (closes[i + hold] - closes[i]) / closes[i] * 100 * sig
            trades.append(ret)
            i += hold + 1
        else:
            i += 1
    if not trades:
        return {"trades": 0, "avg_pct": 0.0, "win_rate": 0.0}
    wins = sum(1 for t in trades if t > 0)
    return {
        "trades": len(trades),
        "avg_pct": round(mean(trades), 4),
        "win_rate": round(wins / len(trades), 3),
        "std_pct": round(pstdev(trades), 4) if len(trades) > 1 else 0.0,
    }


async def run_backtest_job(instrument: str) -> dict:
    rows = await fetch_candles_from_db(instrument, "M15", limit=400)
    closes = [float(r["close_price"]) for r in rows]
    res = _simple_momentum_backtest(closes)
    res["instrument"] = instrument
    res["window"] = "M15_momentum_proxy"
    await _save_snapshot("backtest", instrument, res)
    return res


async def run_walk_forward_job(instrument: str) -> dict:
    rows = await fetch_candles_from_db(instrument, "M15", limit=400)
    closes = [float(r["close_price"]) for r in rows]
    if len(closes) < 80:
        out = {"error": "insufficient_bars"}
        await _save_snapshot("walk_forward", instrument, out)
        return out
    split = int(len(closes) * 0.8)
    in_sample = _simple_momentum_backtest(closes[:split])
    out_sample = _simple_momentum_backtest(closes[split:])
    collapse = False
    if (
        in_sample.get("trades", 0) > 3
        and out_sample.get("trades", 0) > 0
        and out_sample.get("avg_pct", 0) < in_sample.get("avg_pct", 0) * 0.35
    ):
        collapse = True
    out = {
        "instrument": instrument,
        "in_sample": in_sample,
        "out_sample": out_sample,
        "oos_collapse_flag": collapse,
    }
    await _save_snapshot("walk_forward", instrument, out)
    return out


async def run_monte_carlo_job(ticker: str = "BTC-USD", iterations: int = 500) -> dict:
    outcomes = await get_outcomes_for_analysis(ticker, limit=80)
    pnl = [float(o.get("pnl_pct") or 0) for o in outcomes if o.get("pnl_pct") is not None]
    if len(pnl) < 8:
        out = {"error": "insufficient_outcomes", "n": len(pnl)}
        await _save_snapshot("monte_carlo", ticker, out)
        return out
    real_sum = sum(pnl)
    shuffled_better = 0
    for _ in range(iterations):
        sh = pnl[:]
        random.shuffle(sh)
        if sum(sh) >= real_sum:
            shuffled_better += 1
    p_value = shuffled_better / iterations
    out = {
        "ticker": ticker,
        "iterations": iterations,
        "n_trades": len(pnl),
        "realized_sum_pnl_pct": round(real_sum, 4),
        "p_value_shuffle_ge_real": round(p_value, 4),
        "random_wins_flag": p_value > 0.35,
        "interpretation": (
            "likely_luck" if p_value > 0.35 else "structure_better_than_random_shuffle"
        ),
    }
    await _save_snapshot("monte_carlo", ticker, out)
    return out


async def run_full_validation_cycle() -> dict:
    """Invoked by scheduler — runs jobs for GOLD and BTC-USD."""
    summary = {}
    for inst in ("GOLD", "BTC-USD"):
        try:
            summary[f"{inst}_backtest"] = await run_backtest_job(inst)
            summary[f"{inst}_walkforward"] = await run_walk_forward_job(inst)
        except Exception as e:
            summary[f"{inst}_error"] = str(e)
    try:
        summary["mc_btc"] = await run_monte_carlo_job("BTC-USD", 400)
        summary["mc_gold"] = await run_monte_carlo_job("GOLD", 400)
    except Exception as e:
        summary["mc_error"] = str(e)
    await _save_snapshot("full_cycle", None, {"summary_keys": list(summary.keys())})
    return summary


async def fetch_latest_snapshots(limit: int = 30) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, job_type, instrument, payload, created_at
            FROM validation_snapshots
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [
        {
            "id": r["id"],
            "job_type": r["job_type"],
            "instrument": r["instrument"],
            "payload": r["payload"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
