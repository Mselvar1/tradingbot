# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Local development
python main.py

# Install dependencies (Railway uses requirements.txt automatically)
pip install -r requirements.txt
```

### Local Postgres + signal dashboard (Chrome)

1. Start Postgres: `docker compose up -d` (see `docker-compose.yml`; DB on `localhost:5432`).
2. Copy env: `cp .env.example .env` — set real secrets for the full bot; for **dashboard only** you still need dummy values for `TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`, `NEWSAPI_KEY` so `config.settings` loads.
3. Run dashboard: `chmod +x scripts/run_dashboard.sh && ./scripts/run_dashboard.sh`
4. Open **http://localhost:8080** in Chrome. Tables are created on first request via `init_db()` (same as the worker).

Deployed on Railway as a **Worker** service: root `Procfile` defines only `worker: python main.py` (no `web` line) so Railpack does not start the FastAPI dashboard instead of the bot. Python 3.11.9 (`runtime.txt`). No build step — Railway installs requirements and runs `main.py` directly.

## Environment variables (`.env`)

```
TELEGRAM_TOKEN=
ANTHROPIC_API_KEY=
NEWSAPI_KEY=
DATABASE_URL=                  # PostgreSQL on Railway
CAPITAL_API_KEY_DEMO=
CAPITAL_API_KEY_LIVE=
CAPITAL_EMAIL=
CAPITAL_PASSWORD=
CAPITAL_MODE=demo              # demo | live
ALLOWED_TELEGRAM_IDS=          # comma-separated chat IDs
PUBLIC_CHANNEL_ID=             # optional Telegram channel for signal forwarding
PAPER_MODE=true
BINANCE_ENABLED=true           # optional: Binance public spot metrics for BTC (no API key)
BINANCE_SKIP_LOW_VOLUME=false  # if true, skip BTC signals when Binance 1m vol << 20m avg
MIN_BINANCE_VOLUME_RATIO=0.12  # used when BINANCE_SKIP_LOW_VOLUME=true
SIGNAL_PLATFORM_ENABLED=true   # multi-TF candles, strategies, validation, circuit breaker
CIRCUIT_BREAKER_SL_STREAK=8    # consecutive SL outcomes before pause
CIRCUIT_BREAKER_PAUSE_HOURS=24 # pause duration for new entries (executor + scanners)
DASHBOARD_AUTH_TOKEN=          # optional Bearer token for POST /monte-carlo/run
```

## Architecture overview

`main.py` is the entry point. It builds the Telegram bot, awaits `init_db()` in `post_init`, then launches **10 concurrent asyncio tasks**:

| Task | File | Interval |
|---|---|---|
| Gold scanner | `workers/scanner.py` | 120s |
| BTC scanner | `workers/btc_scanner.py` | 300s |
| Position monitor | `workers/position_monitor.py` | 120s |
| Trade manager | `workers/trade_manager.py` | 60s |
| Price tracker | `services/price_tracker.py` | 30s |
| Binance flow | `services/data/binance_market.py` | 30s |
| Candle feed | `workers/candle_feed.py` | 900s (M15–D1 → `candles` table) |
| Signal platform | `workers/signal_platform_scheduler.py` | 4h (strategies + validation + Telegram digest) |
| Weekly report | `workers/weekly_report.py` | checks every 30 min |

**Dashboard (separate process):** `Procfile.dashboard` contains the `web:` uvicorn command. Deploy a **second** Railway **Web** service and set **Start Command** to that uvicorn line (same `DATABASE_URL` as the worker). Locally use `scripts/run_dashboard.sh` (port 8080).

All tasks share a single Capital.com session via `capital_client` (singleton in `services/data/capital.py`). Session tokens are refreshed lazily via `ensure_session()`. The **Binance** task only calls Binance public REST endpoints (no key); it feeds live 1m volume and book imbalance into the BTC Haiku prompt.

### Signal platform (`services/signal_platform/`)

- **`candles` table** — OHLCV for `GOLD` (Capital) and `BTC-USD` (Binance spot) across M15, H1, H4, D1.
- **Four strategies** — `strategies_core.py`: liquidity sweep, trend continuation, breakout expansion, EMA momentum; scores written to `strategy_scores`.
- **Validation** — `validation_engine.py`: simple momentum backtest proxy, walk-forward OOS flag, Monte Carlo shuffle on recent `outcomes`; snapshots in `validation_snapshots`.
- **Circuit breaker** — `circuit_breaker.py`: after `CIRCUIT_BREAKER_SL_STREAK` consecutive SL outcomes, pauses **new** entries until `paused_until` (persisted in `circuit_breaker` table). Scanners and `capital_executor.can_trade()` respect `is_paused()`.

## Signal pipeline (Gold and BTC)

Both scanners follow the same multi-stage filter chain before a trade is placed:

1. **Session window** — London open 07:00–08:30 UTC and NY open 13:30–15:00 UTC only
2. **Technical pre-filter** — `has_scalp_setup()` / `has_btc_setup()` (score-based, no API call)
3. **RSI/momentum pre-filter** — RSI extreme or significant price move, otherwise skip
4. **Rate limiter** — `claude_limiter.acquire()` (20 Claude calls/hour shared across both scanners + trade manager reviews)
5. **Claude analysis** — Sonnet for Gold (`analyse()`), Haiku for BTC (`analyse_btc()`)
6. **Confidence threshold** — 75 minimum
7. **MA trend filter** — BUY only when MA20 > MA50; SELL only when MA20 < MA50 (Gold); EMA stack alignment for BTC
8. **Confluence check** — minimum 2 SMC confluences required
9. **Execution validation** — `capital_executor.place_trade()` checks MIN_RR 2.0, stop ≤ 0.5%, TP ≥ 0.3%

After a successful trade, the signal is registered in both `services/learning.py` (`register_trade_signal`) and `services/trade_store.py` (`trade_store.register`) for downstream management.

## Three in-memory singletons that coordinate workers

- **`trade_store`** (`services/trade_store.py`) — maps `deal_id` → full signal + trade details. Populated by scanners, consumed by trade_manager. `trade_store.manager_closed` is a set that tells `position_monitor` to skip outcome recording for intentionally-closed deals.
- **`price_tracker`** (`services/price_tracker.py`) — rolling 20-snapshot history per ticker. Workers call `price_tracker.get_narrative(label)` to inject live market context into Claude prompts.
- **`claude_limiter`** (`services/rate_limiter.py`) — sliding-window deque, shared 20 calls/hour cap. All callers (`GOLD`, `BTC`, `TRADE_REVIEW`) pass a label for logging.

## Database (PostgreSQL via asyncpg)

`init_db()` in `services/memory.py` is idempotent (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). It must be awaited in `post_init()` before any task starts — this prevents the `rsi_at_entry` column-not-found error on Railway cold starts.

Tables:
- `signals` — every Claude signal above threshold
- `outcomes` — closed position results (written by `services/learning.py` via position_monitor)
- `trade_exits` — managed closes by trade_manager with euro P&L and `saved_vs_sl_pct`
- `position_updates` — every stop-loss movement (breakeven, trailing)
- `trade_insights` — pattern analysis snapshots (written every 5 new outcomes)

## Self-learning loop

`services/learning.py` owns the loop:
1. `register_trade_signal(deal_id, ...)` — called immediately after a trade is placed; stores metadata in `_deal_signal_map`
2. `record_closed_position(...)` — called by position_monitor when a deal disappears; writes to `outcomes`; triggers `run_pattern_analysis()` every 5 new outcomes
3. `run_pattern_analysis(ticker)` — computes win rates by session, confluence, RSI bucket; writes `trade_insights`
4. `get_dynamic_threshold(ticker, session)` — reads latest insight to adjust confidence threshold
5. `get_prompt_injection(ticker)` — formats top-3/worst-3 setups as text injected into Claude analysis prompts

## Capital.com API notes

- `place_order()` — market order via `POST /positions`. Handles `error.invalid.stoploss.maxvalue` / `minvalue` errors by parsing the boundary and retrying once with an adjusted stop.
- `close_position_partial(deal_id, size)` — attempts `DELETE /positions/{dealId}` with `{"size": X}` body. Demo accounts don't support partial close; falls back to full close automatically.
- `get_deal_confirmation(deal_reference)` — `GET /confirms/{ref}` to get the permanent `dealId` after placing an order (required for closing positions).
- BTC uses `resolve_btc_epic()` at startup to find the correct candle epic (`/prices/{epic}` responds differently from `/markets/{epic}`).

## Claude models in use

- **Sonnet** (`claude-sonnet-4-20250514`) — Gold signal analysis, signal review
- **Haiku** (`claude-haiku-4-5-20251001`) — BTC signal analysis, trade management reviews (every 5 min per open position)

## Position monitor vs trade manager — separation of concerns

- **`position_monitor`** — moves stop losses (breakeven at 50% to TP1, then trailing); sends breakeven Telegram; records outcomes for SL hits and external closes; runs every 120s
- **`trade_manager`** — makes exit decisions (TP hits, early exits, CHoCH, Claude review); calls `capital_client.close_position()` or `close_position_partial()`; sends management/closed Telegram messages; runs every 60s

Coordination: when trade_manager closes a position, it calls `trade_store.mark_closed(deal_id)`. Position_monitor checks `trade_store.manager_closed` before writing to `outcomes`, preventing double-recording.

## Telegram bot commands

Handlers live in `bot/handlers/`. Key ones: `cmd_kill` / `cmd_resume` toggle `risk.kill_switch` (all workers check this before acting), `cmd_signals` / `cmd_stats` read from in-memory `SignalHistory` (not DB), `cmd_positions` / `cmd_balance` query Capital.com live.

## Adding a new ticker

1. Add epic mapping to `services/data/capital_epics.py`
2. Create a new scanner in `workers/` following the Gold/BTC pattern (8-step filter chain)
3. Register a `asyncio.create_task()` in `main.py`'s `post_init`
4. Add price tracker polling in `services/price_tracker.py` `run_price_tracker()`
