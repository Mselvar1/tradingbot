from __future__ import annotations

import json

from services.memory import get_pool
from services.signal_platform.candles_store import fetch_candles_from_db
from services.signal_platform.strategies_core import run_all_strategies


async def _load_bundle(instrument: str) -> dict[str, list]:
    bundle = {}
    for tf in ("M15", "H1", "H4", "D1"):
        bundle[tf] = await fetch_candles_from_db(instrument, tf, limit=400)
    return bundle


async def persist_scores(instrument: str, results: list[dict]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for r in results:
            await conn.execute(
                """
                INSERT INTO strategy_scores (instrument, strategy, score, direction, details)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                instrument,
                r["strategy"],
                float(r["score"]),
                r.get("direction") or "neutral",
                json.dumps(r.get("details") or {}),
            )


async def evaluate_and_store(instrument: str) -> list[dict]:
    bundle = await _load_bundle(instrument)
    if len(bundle.get("M15") or []) < 20:
        return []
    results = run_all_strategies(bundle)
    await persist_scores(instrument, results)
    return results


async def latest_scores_summary() -> dict[str, list[dict]]:
    """Latest row per (instrument, strategy) for dashboard."""
    pool = await get_pool()
    out: dict[str, list[dict]] = {}
    async with pool.acquire() as conn:
        for inst in ("GOLD", "BTC-USD"):
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (strategy)
                    strategy, score, direction, details, created_at
                FROM strategy_scores
                WHERE instrument = $1
                ORDER BY strategy, created_at DESC
                """,
                inst,
            )
            out[inst] = [
                {
                    "strategy": r["strategy"],
                    "score": float(r["score"]),
                    "direction": r["direction"],
                    "details": r["details"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ]
    return out
