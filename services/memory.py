import asyncpg
import json
import datetime
from config.settings import settings

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=5
        )
    return _pool

async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id SERIAL PRIMARY KEY,
                ticker VARCHAR(20),
                action VARCHAR(10),
                confidence INTEGER,
                entry_price FLOAT,
                stop_loss FLOAT,
                take_profit FLOAT,
                rr FLOAT,
                session VARCHAR(30),
                market_structure VARCHAR(30),
                fvg_present BOOLEAN,
                liquidity_sweep BOOLEAN,
                bos_detected BOOLEAN,
                choch_detected BOOLEAN,
                confluences TEXT,
                verdict VARCHAR(30),
                summary TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS outcomes (
                id SERIAL PRIMARY KEY,
                signal_id INTEGER REFERENCES signals(id),
                ticker VARCHAR(20),
                action VARCHAR(10),
                entry_price FLOAT,
                exit_price FLOAT,
                stop_loss FLOAT,
                take_profit FLOAT,
                result VARCHAR(20),
                pnl_pct FLOAT,
                hold_minutes INTEGER,
                session VARCHAR(30),
                notes TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS position_updates (
                id            SERIAL PRIMARY KEY,
                deal_id       VARCHAR(100),
                ticker        VARCHAR(20),
                direction     VARCHAR(10),
                entry_price   FLOAT,
                old_stop      FLOAT,
                new_stop      FLOAT,
                current_price FLOAT,
                update_type   VARCHAR(20),
                created_at    TIMESTAMP DEFAULT NOW()
            )
        """)
    print("Database initialized")

async def save_signal(signal: dict) -> int:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO signals (
                    ticker, action, confidence, entry_price,
                    stop_loss, take_profit, rr, session,
                    market_structure, fvg_present, liquidity_sweep,
                    bos_detected, choch_detected, confluences, verdict, summary
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                RETURNING id
            """,
                signal.get("ticker", "GOLD"),
                signal.get("action", "buy"),
                int(signal.get("confidence", 0)),
                float(signal.get("price", 0)),
                float(signal.get("stop_loss", 0)) if signal.get("stop_loss") else 0.0,
                float(signal.get("tp1", 0)) if signal.get("tp1") else 0.0,
                float(signal.get("rr", 0)) if signal.get("rr") else 0.0,
                signal.get("session_context", "unknown"),
                signal.get("market_structure", "unknown"),
                bool(signal.get("fvg_present", False)),
                bool(signal.get("liquidity_sweep_detected", False)),
                bool(signal.get("bos_detected", False)),
                bool(signal.get("choch_detected", False)),
                json.dumps(signal.get("confluences", [])),
                signal.get("trading_verdict", "BUY"),
                signal.get("summary", "")
            )
            return row["id"]
    except Exception as e:
        print(f"Save signal error: {e}")
        return 0

async def save_outcome(signal_id: int, outcome: dict):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO outcomes (
                    signal_id, ticker, action, entry_price,
                    exit_price, stop_loss, take_profit,
                    result, pnl_pct, hold_minutes, session, notes
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """,
                signal_id,
                outcome.get("ticker", "GOLD"),
                outcome.get("action", "buy"),
                float(outcome.get("entry_price", 0)),
                float(outcome.get("exit_price", 0)),
                float(outcome.get("stop_loss", 0)),
                float(outcome.get("take_profit", 0)),
                outcome.get("result", "unknown"),
                float(outcome.get("pnl_pct", 0)),
                int(outcome.get("hold_minutes", 0)),
                outcome.get("session", "unknown"),
                outcome.get("notes", "")
            )
    except Exception as e:
        print(f"Save outcome error: {e}")

async def save_position_update(update: dict):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO position_updates (
                    deal_id, ticker, direction, entry_price,
                    old_stop, new_stop, current_price, update_type
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
                update.get("deal_id", ""),
                update.get("ticker", ""),
                update.get("direction", ""),
                float(update.get("entry_price", 0)),
                float(update.get("old_stop", 0)),
                float(update.get("new_stop", 0)),
                float(update.get("current_price", 0)),
                update.get("update_type", ""),
            )
    except Exception as e:
        print(f"Save position update error: {e}")

async def get_recent_outcomes(ticker: str = "GOLD", limit: int = 20) -> list:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT o.*, s.market_structure, s.fvg_present,
                       s.confluences, s.session
                FROM outcomes o
                JOIN signals s ON o.signal_id = s.id
                WHERE o.ticker = $1
                ORDER BY o.created_at DESC
                LIMIT $2
            """, ticker, limit)
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"Get outcomes error: {e}")
        return []

async def get_win_rate(ticker: str = "GOLD") -> dict:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT result, COUNT(*) as count
                FROM outcomes
                WHERE ticker = $1
                GROUP BY result
            """, ticker)
            stats = {r["result"]: r["count"] for r in rows}
            total = sum(stats.values())
            wins = stats.get("tp1", 0) + stats.get("tp2", 0) + stats.get("tp3", 0)
            win_rate = round(wins / total * 100, 1) if total > 0 else 0
            return {
                "total": total,
                "wins": wins,
                "losses": stats.get("sl", 0),
                "win_rate": win_rate,
                "stats": stats
            }
    except Exception as e:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0}

async def get_memory_context(ticker: str = "GOLD") -> str:
    try:
        outcomes = await get_recent_outcomes(ticker, 10)
        win_rate = await get_win_rate(ticker)
        if not outcomes:
            return "No historical trade data available yet."
        outcome_lines = []
        for o in outcomes[:5]:
            outcome_lines.append(
                f"- {o['action'].upper()} {o['result']} "
                f"PnL:{o['pnl_pct']}% "
                f"session:{o['session']} "
                f"held:{o['hold_minutes']}min"
            )
        return (
            f"Win rate: {win_rate['win_rate']}% "
            f"({win_rate['wins']}W/{win_rate['losses']}L "
            f"from {win_rate['total']} trades)\n"
            f"Recent: " + "\n".join(outcome_lines)
        )
    except Exception as e:
        return "Memory unavailable."
