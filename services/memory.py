import asyncpg
import json
import time
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS limit_orders (
                id             SERIAL PRIMARY KEY,
                deal_id        VARCHAR(100),
                deal_reference VARCHAR(100),
                ticker         VARCHAR(20),
                epic           VARCHAR(50),
                direction      VARCHAR(10),
                size           FLOAT,
                level_price    FLOAT,
                level_type     VARCHAR(30),
                level_key      VARCHAR(120),
                stop_loss      FLOAT,
                take_profit    FLOAT,
                atr            FLOAT,
                status         VARCHAR(20) DEFAULT 'pending',
                placed_at      TIMESTAMP DEFAULT NOW(),
                expires_at     TIMESTAMP,
                filled_at      TIMESTAMP,
                cancelled_at   TIMESTAMP
            )
        """)
        # ── Extend outcomes table for self-learning ────────────────────────
        await conn.execute(
            "ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS rsi_at_entry FLOAT"
        )
        await conn.execute(
            "ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS trend_direction VARCHAR(20)"
        )
        await conn.execute(
            "ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS confluences TEXT"
        )
        # ── Pattern insights (written after every 5 new outcomes) ──────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_insights (
                id              SERIAL PRIMARY KEY,
                ticker          VARCHAR(20),
                trades_analysed INTEGER,
                overall_wr      FLOAT,
                session_wr      TEXT,
                confluence_wr   TEXT,
                rsi_bucket_wr   TEXT,
                top_setups      TEXT,
                losing_patterns TEXT,
                threshold_gold  INTEGER,
                threshold_btc   INTEGER,
                created_at      TIMESTAMP DEFAULT NOW()
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

async def save_outcome(signal_id: int, outcome: dict) -> int:
    """Save a trade outcome. Returns the new row id."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO outcomes (
                    signal_id, ticker, action, entry_price,
                    exit_price, stop_loss, take_profit,
                    result, pnl_pct, hold_minutes, session, notes,
                    rsi_at_entry, trend_direction, confluences
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                RETURNING id
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
                outcome.get("notes", ""),
                float(outcome["rsi_at_entry"]) if outcome.get("rsi_at_entry") else None,
                outcome.get("trend_direction"),
                json.dumps(outcome["confluences"]) if outcome.get("confluences") else None,
            )
            return row["id"] if row else 0
    except Exception as e:
        print(f"Save outcome error: {e}")
        return 0

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

async def save_limit_order(order: dict) -> int:
    """Persist a newly-placed limit order to the database."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO limit_orders (
                    deal_id, deal_reference, ticker, epic, direction, size,
                    level_price, level_type, level_key,
                    stop_loss, take_profit, atr, status,
                    placed_at, expires_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                RETURNING id
            """,
                order.get("deal_id", ""),
                order.get("deal_reference", ""),
                order.get("ticker", ""),
                order.get("epic", ""),
                order.get("direction", ""),
                float(order.get("size", 0)),
                float(order.get("level_price", 0)),
                order.get("level_type", ""),
                order.get("level_key", ""),
                float(order.get("stop_loss", 0)),
                float(order.get("take_profit", 0)),
                float(order.get("atr", 0)),
                "pending",
                datetime.datetime.utcfromtimestamp(order.get("placed_at", time.time())),
                datetime.datetime.utcfromtimestamp(order.get("expires_at", time.time())),
            )
            return row["id"]
    except Exception as e:
        print(f"Save limit order error: {e}")
        return 0


async def update_limit_order_status(deal_id: str, status: str):
    """Update the status of a limit order (filled / cancelled)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if status == "filled":
                await conn.execute(
                    "UPDATE limit_orders SET status=$1, filled_at=NOW() WHERE deal_id=$2",
                    status, deal_id
                )
            elif status in ("cancelled", "expiry_cancel_failed"):
                await conn.execute(
                    "UPDATE limit_orders SET status=$1, cancelled_at=NOW() WHERE deal_id=$2",
                    status, deal_id
                )
            else:
                await conn.execute(
                    "UPDATE limit_orders SET status=$1 WHERE deal_id=$2",
                    status, deal_id
                )
    except Exception as e:
        print(f"Update limit order error: {e}")


async def get_pending_limit_orders() -> list:
    """Return all pending limit orders that have not yet expired (for restart recovery)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM limit_orders
                WHERE status = 'pending'
                  AND expires_at > NOW()
                ORDER BY placed_at DESC
            """)
            result = []
            for r in rows:
                d = dict(r)
                # Convert timestamps to unix floats for in-memory use
                d["placed_at"]  = d["placed_at"].timestamp()  if d.get("placed_at")  else time.time()
                d["expires_at"] = d["expires_at"].timestamp() if d.get("expires_at") else time.time()
                result.append(d)
            return result
    except Exception as e:
        print(f"Get pending limit orders error: {e}")
        return []


async def save_trade_insight(insight: dict) -> int:
    """Persist a pattern-analysis snapshot to trade_insights."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO trade_insights (
                    ticker, trades_analysed, overall_wr,
                    session_wr, confluence_wr, rsi_bucket_wr,
                    top_setups, losing_patterns,
                    threshold_gold, threshold_btc
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                RETURNING id
            """,
                insight.get("ticker", "ALL"),
                int(insight.get("trades_analysed", 0)),
                float(insight.get("overall_wr", 0)),
                json.dumps(insight.get("session_wr", {})),
                json.dumps(insight.get("confluence_wr", {})),
                json.dumps(insight.get("rsi_bucket_wr", {})),
                json.dumps(insight.get("top_setups", [])),
                json.dumps(insight.get("losing_patterns", [])),
                int(insight.get("threshold_gold", 60)),
                int(insight.get("threshold_btc", 58)),
            )
            return row["id"] if row else 0
    except Exception as e:
        print(f"Save trade insight error: {e}")
        return 0


async def get_latest_insight(ticker: str = "ALL") -> dict | None:
    """Return the most recent trade_insights row for this ticker."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM trade_insights
                WHERE ticker = $1
                ORDER BY created_at DESC
                LIMIT 1
            """, ticker)
            if not row:
                return None
            d = dict(row)
            # Deserialise JSON fields
            for field in ("session_wr", "confluence_wr", "rsi_bucket_wr",
                          "top_setups", "losing_patterns"):
                raw = d.get(field)
                if raw:
                    try:
                        d[field] = json.loads(raw)
                    except Exception:
                        d[field] = {} if field.endswith("_wr") else []
            return d
    except Exception as e:
        print(f"Get latest insight error: {e}")
        return None


async def get_outcomes_for_analysis(ticker: str, limit: int = 20,
                                     days: int = None) -> list:
    """
    Last `limit` outcomes for `ticker`, joined with signal metadata.
    If `days` is set, restricts to outcomes within the last N days.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            date_filter = ""
            params = [ticker, limit]
            if days:
                date_filter = "AND o.created_at >= NOW() - INTERVAL '1 day' * $3"
                params.append(days)

            rows = await conn.fetch(f"""
                SELECT
                    o.id, o.ticker, o.action, o.entry_price, o.exit_price,
                    o.pnl_pct, o.result, o.session, o.hold_minutes,
                    o.created_at, o.rsi_at_entry, o.trend_direction,
                    COALESCE(o.confluences, s.confluences) AS confluences,
                    s.market_structure, s.fvg_present,
                    s.bos_detected, s.choch_detected
                FROM outcomes o
                LEFT JOIN signals s ON o.signal_id = s.id
                WHERE o.ticker = $1
                  AND o.result IS NOT NULL
                  {date_filter}
                ORDER BY o.created_at DESC
                LIMIT $2
            """, *params)
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"Get outcomes for analysis error: {e}")
        return []


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
