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
        # ── Trade exits (managed closes by trade_manager) ─────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_exits (
                id               SERIAL PRIMARY KEY,
                deal_id          VARCHAR(100),
                ticker           VARCHAR(20),
                direction        VARCHAR(10),
                entry_price      FLOAT,
                exit_price       FLOAT,
                size             FLOAT,
                pnl_pct          FLOAT,
                pnl_euros        FLOAT,
                exit_reason      VARCHAR(50),
                hold_minutes     INTEGER,
                confluences      TEXT,
                session          VARCHAR(30),
                entry_narrative  TEXT,
                exit_narrative   TEXT,
                sl_loss_pct      FLOAT,
                saved_vs_sl_pct  FLOAT,
                created_at       TIMESTAMP DEFAULT NOW()
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
        # ── Multi-timeframe candles (signal generator / validation) ─────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                id BIGSERIAL PRIMARY KEY,
                instrument VARCHAR(32) NOT NULL,
                timeframe VARCHAR(16) NOT NULL,
                open_time TIMESTAMPTZ NOT NULL,
                open_price DOUBLE PRECISION NOT NULL,
                high_price DOUBLE PRECISION NOT NULL,
                low_price DOUBLE PRECISION NOT NULL,
                close_price DOUBLE PRECISION NOT NULL,
                volume DOUBLE PRECISION DEFAULT 0,
                source VARCHAR(24) NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (instrument, timeframe, open_time, source)
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candles_inst_tf ON candles "
            "(instrument, timeframe, open_time DESC)"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_scores (
                id SERIAL PRIMARY KEY,
                instrument VARCHAR(32) NOT NULL,
                strategy VARCHAR(64) NOT NULL,
                score DOUBLE PRECISION NOT NULL,
                direction VARCHAR(12),
                details JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_strategy_scores_inst "
            "ON strategy_scores (instrument, strategy, created_at DESC)"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS validation_snapshots (
                id SERIAL PRIMARY KEY,
                job_type VARCHAR(48) NOT NULL,
                instrument VARCHAR(32),
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS circuit_breaker (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                consecutive_sl INTEGER NOT NULL DEFAULT 0,
                paused_until TIMESTAMPTZ,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute(
            "INSERT INTO circuit_breaker (id, consecutive_sl) "
            "SELECT 1, 0 WHERE NOT EXISTS (SELECT 1 FROM circuit_breaker WHERE id = 1)"
        )
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
    """Save a trade outcome. Returns the new row id.
    signal_id=0 or None means the trade was opened without a tracked signal
    (e.g. manual trade or BTC position opened before DB was initialised) —
    in that case we insert with a NULL signal_id to avoid FK violations.
    """
    try:
        pool = await get_pool()
        # Treat 0 the same as None — no valid FK target in signals table
        safe_signal_id = signal_id if signal_id else None
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
                safe_signal_id,
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


async def fetch_performance_dashboard(limit_recent: int = 50) -> dict:
    """
    Aggregates for dashboard / performance page — counts only, no prices or PnL.
    Source: `outcomes` (closes recorded by the bot pipeline).
    """
    out: dict = {
        "overall": {"total": 0, "wins": 0, "losses": 0, "other": 0, "win_rate": 0.0},
        "by_ticker": [],
        "signals_count": 0,
        "recent": [],
    }
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE result IN ('tp1', 'tp2', 'tp3'))::int AS wins,
                    COUNT(*) FILTER (WHERE result = 'sl')::int AS losses,
                    COUNT(*) FILTER (
                        WHERE COALESCE(result, '') NOT IN ('tp1', 'tp2', 'tp3', 'sl')
                    )::int AS other
                FROM outcomes
                """
            )
            if row:
                total = int(row["total"] or 0)
                wins = int(row["wins"] or 0)
                losses = int(row["losses"] or 0)
                other = int(row["other"] or 0)
                wr = round(wins / total * 100, 1) if total else 0.0
                out["overall"] = {
                    "total": total,
                    "wins": wins,
                    "losses": losses,
                    "other": other,
                    "win_rate": wr,
                }

            per = await conn.fetch(
                """
                SELECT ticker,
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE result IN ('tp1', 'tp2', 'tp3'))::int AS wins,
                    COUNT(*) FILTER (WHERE result = 'sl')::int AS losses,
                    COUNT(*) FILTER (
                        WHERE COALESCE(result, '') NOT IN ('tp1', 'tp2', 'tp3', 'sl')
                    )::int AS other
                FROM outcomes
                GROUP BY ticker
                ORDER BY ticker NULLS LAST
                """
            )
            for r in per:
                t = int(r["total"] or 0)
                w = int(r["wins"] or 0)
                out["by_ticker"].append(
                    {
                        "ticker": r["ticker"] or "—",
                        "total": t,
                        "wins": w,
                        "losses": int(r["losses"] or 0),
                        "other": int(r["other"] or 0),
                        "win_rate": round(w / t * 100, 1) if t else 0.0,
                    }
                )

            out["signals_count"] = int(await conn.fetchval("SELECT COUNT(*)::int FROM signals") or 0)

            lim = max(1, min(limit_recent, 200))
            recent = await conn.fetch(
                """
                SELECT id, ticker, action, result, hold_minutes, session, created_at
                FROM outcomes
                ORDER BY created_at DESC
                LIMIT $1
                """,
                lim,
            )
            for r in recent:
                ca = r["created_at"]
                out["recent"].append(
                    {
                        "id": r["id"],
                        "ticker": r["ticker"],
                        "action": r["action"],
                        "result": r["result"],
                        "hold_minutes": r["hold_minutes"],
                        "session": r["session"],
                        "created_at": ca.isoformat() if ca and hasattr(ca, "isoformat") else str(ca),
                    }
                )
    except Exception as e:
        print(f"fetch_performance_dashboard error: {e}")
    return out


async def fetch_btc_window_performance(hours: int = 8) -> dict:
    """
    BTC-USD stats for the last `hours` hours only (not all-time).
    Uses outcomes (pipeline closes) + trade_exits (managed closes with € PnL).
    """
    out: dict = {
        "hours": hours,
        "outcomes_n": 0,
        "wins": 0,
        "losses": 0,
        "other": 0,
        "win_rate": 0.0,
        "avg_pnl_pct": None,
        "trade_exits_n": 0,
        "net_pnl_euros": None,
    }
    try:
        pool = await get_pool()
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)::int AS n,
                    COUNT(*) FILTER (WHERE result IN ('tp1', 'tp2', 'tp3'))::int AS wins,
                    COUNT(*) FILTER (WHERE result = 'sl')::int AS losses,
                    COUNT(*) FILTER (
                        WHERE COALESCE(result, '') NOT IN ('tp1', 'tp2', 'tp3', 'sl')
                    )::int AS other,
                    AVG(pnl_pct)::float AS avg_pnl_pct
                FROM outcomes
                WHERE (ticker ILIKE '%BTC%' OR ticker = 'BTC-USD')
                  AND created_at >= $1
                """,
                cutoff,
            )
            if row:
                n = int(row["n"] or 0)
                w = int(row["wins"] or 0)
                out["outcomes_n"] = n
                out["wins"] = w
                out["losses"] = int(row["losses"] or 0)
                out["other"] = int(row["other"] or 0)
                out["win_rate"] = round(w / n * 100, 1) if n else 0.0
                ap = row["avg_pnl_pct"]
                out["avg_pnl_pct"] = round(float(ap), 4) if ap is not None else None

            ex = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)::int AS n,
                    COALESCE(SUM(pnl_euros), 0)::float AS net_eur
                FROM trade_exits
                WHERE (ticker ILIKE '%BTC%' OR ticker = 'BTC-USD')
                  AND created_at >= $1
                """,
                cutoff,
            )
            if ex:
                out["trade_exits_n"] = int(ex["n"] or 0)
                ne = ex["net_eur"]
                out["net_pnl_euros"] = round(float(ne), 2) if ne is not None else None
    except Exception as e:
        print(f"fetch_btc_window_performance error: {e}")
    return out


async def save_trade_exit(exit_data: dict) -> int:
    """Save a managed trade exit to the trade_exits table."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO trade_exits (
                    deal_id, ticker, direction, entry_price, exit_price,
                    size, pnl_pct, pnl_euros, exit_reason, hold_minutes,
                    confluences, session, entry_narrative, exit_narrative,
                    sl_loss_pct, saved_vs_sl_pct
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                RETURNING id
            """,
                exit_data.get("deal_id", ""),
                exit_data.get("ticker", ""),
                exit_data.get("direction", ""),
                float(exit_data.get("entry_price", 0)),
                float(exit_data.get("exit_price", 0)),
                float(exit_data.get("size", 0)),
                float(exit_data.get("pnl_pct", 0)),
                float(exit_data.get("pnl_euros", 0)),
                exit_data.get("exit_reason", "unknown"),
                int(exit_data.get("hold_minutes", 0)),
                json.dumps(exit_data.get("confluences", [])),
                exit_data.get("session", "unknown"),
                exit_data.get("entry_narrative", ""),
                exit_data.get("exit_narrative", ""),
                float(exit_data.get("sl_loss_pct", 0)),
                float(exit_data.get("saved_vs_sl_pct", 0)),
            )
            return row["id"] if row else 0
    except Exception as e:
        print(f"Save trade exit error: {e}")
        return 0


async def get_weekly_exits(days: int = 7) -> list:
    """Return trade_exits rows from the last N days for the weekly report."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM trade_exits
                WHERE created_at >= NOW() - INTERVAL '1 day' * $1
                ORDER BY created_at DESC
            """, days)
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"Get weekly exits error: {e}")
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


async def count_all_candles() -> int:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            n = await conn.fetchval("SELECT COUNT(*)::bigint FROM candles")
        return int(n or 0)
    except Exception:
        return 0


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
