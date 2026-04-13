"""
Price Level Monitor — 15-second heartbeat during trading sessions.

Lifecycle per instrument (GOLD: London/NY | BTC-USD: 24/7 minus dead zone):

  1. Every 120s  — re-detect key levels from fresh 1-minute candles:
                     • Fair Value Gaps (3-candle imbalance)
                     • Order Blocks   (institutional supply/demand zones)
                     • Liquidity pools (equal highs / equal lows)

  2. Every 15s   — fetch current price.
                   When price approaches a stored level from the correct side:
                     → place LIMIT order via POST /workingorders/otc
                     → Telegram: "Limit order placed at 4756 — waiting for fill"

  3. Every 15s   — poll GET /workingorders.
                   If an order disappears before expiry → it filled:
                     → Telegram: "Order filled at 4756 — SL 4762  TP 4738"
                   If 2 hours elapsed without fill:
                     → DELETE /workingorders/otc/{deal_id}  (cancel)
                     → Telegram: "Limit order expired — cancelled"

State is kept in memory and persisted to the limit_orders database table
so pending orders survive a bot restart.
"""

import asyncio
import time

from services.data.capital import capital_client
from services.execution.capital_executor import executor
from services.memory import (
    save_limit_order,
    update_limit_order_status,
    get_pending_limit_orders,
)
from services.risk import risk
from workers.scanner import is_trading_session as _is_gold_session
from workers.btc_scanner import should_scan_btc as _is_btc_active, resolve_btc_epic

# ─── Configuration ─────────────────────────────────────────────────────────────

MONITOR_INTERVAL    = 15      # seconds between price checks
LEVEL_REFRESH_SECS  = 120     # re-detect levels from candles every 2 minutes
TOUCH_ZONE_PCT      = 0.002   # 0.2% proximity triggers limit order placement
SL_ATR_MULT         = 1.5     # stop loss = entry ± 1.5 × ATR
TP_ATR_MULT         = 3.0     # take profit = entry ± 3.0 × ATR  (2:1 R:R)
ORDER_EXPIRY_SECS   = 7200    # cancel unfilled orders after 2 hours
LEVEL_COOLDOWN_SECS = 14400   # 4-hour cooldown before re-using same level key
BALANCE_CACHE_SECS  = 300     # refresh account balance every 5 minutes

# ─── In-Memory State ────────────────────────────────────────────────────────────

_levels:       dict[str, list[dict]] = {}   # ticker → [level_dict, ...]
_pending:      dict[str, dict]       = {}   # deal_id → order_info
_used_levels:  dict[str, float]      = {}   # level_key → timestamp placed
_last_refresh: dict[str, float]      = {}   # ticker → last candle-fetch timestamp
_balance:      float                 = 0.0
_balance_ts:   float                 = 0.0


# ─── Indicator Helpers ─────────────────────────────────────────────────────────
# (self-contained — no dependency on other scanner modules)

def _calc_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    if len(highs) < 2:
        return 0.0
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]))
        for i in range(1, len(highs))
    ]
    return round(sum(trs[-period:]) / min(period, len(trs)), 4)


def _detect_fvg(highs: list, lows: list, lookback: int = 40) -> list:
    """3-candle imbalance zones (Fair Value Gaps)."""
    fvgs = []
    n = min(len(highs), lookback)
    for i in range(2, n):
        bull_gap = lows[i] - highs[i - 2]
        if bull_gap > 0:
            fvgs.append({
                "type": "bullish",
                "low":  round(highs[i - 2], 4),
                "high": round(lows[i], 4),
            })
        bear_gap = lows[i - 2] - highs[i]
        if bear_gap > 0:
            fvgs.append({
                "type": "bearish",
                "low":  round(highs[i], 4),
                "high": round(lows[i - 2], 4),
            })
    return fvgs[:5]


def _detect_order_blocks(highs: list, lows: list,
                          opens: list, closes: list, lookback: int = 30) -> list:
    """
    Bullish OB: last bearish candle immediately before a strong bullish impulse.
    Bearish OB: last bullish candle immediately before a strong bearish impulse.
    Impulse threshold: next-2-candle move ≥ 1.5× the candle body.
    """
    obs = []
    n = min(len(closes) - 2, lookback)
    for i in range(1, n):
        fwd  = closes[i + 2] - closes[i]
        body = abs(closes[i] - opens[i])
        if body == 0:
            continue
        if closes[i] < opens[i] and fwd > body * 1.5:
            obs.append({"type": "bullish",
                        "low": round(lows[i], 4), "high": round(highs[i], 4)})
        elif closes[i] > opens[i] and fwd < -body * 1.5:
            obs.append({"type": "bearish",
                        "low": round(lows[i], 4), "high": round(highs[i], 4)})
    return obs[:3]


def _detect_equal_levels(highs: list, lows: list, tol: float = 0.0006) -> dict:
    """Liquidity pools: equal highs (buy-side) and equal lows (sell-side)."""
    if not highs or not lows:
        return {}
    rh, rl = highs[-30:], lows[-30:]
    max_h, min_l = max(rh), min(rl)
    eq_h = [h for h in rh if abs(h - max_h) / max_h < tol]
    eq_l = [l for l in rl if abs(l - min_l) / min_l < tol]
    return {
        "equal_highs": round(sum(eq_h) / len(eq_h), 4) if len(eq_h) >= 2 else None,
        "equal_lows":  round(sum(eq_l) / len(eq_l),  4) if len(eq_l)  >= 2 else None,
    }


# ─── Level Detection ────────────────────────────────────────────────────────────

def _build_levels(ticker: str, epic: str, candles: list) -> list:
    """
    Compute all key levels from raw OHLCV candles.
    Returns a list of normalised level dicts.
    """
    if len(candles) < 20:
        return []

    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    opens  = [c["open"]  for c in candles]
    atr    = _calc_atr(highs, lows, closes) or closes[-1] * 0.001

    levels: list[dict] = []

    # ── Fair Value Gaps ──────────────────────────────────────────────────────
    for fvg in _detect_fvg(highs, lows):
        zone_lo = fvg["low"]
        zone_hi = fvg["high"]
        entry   = round((zone_lo + zone_hi) / 2, 4)
        key     = f"{ticker}_fvg_{fvg['type']}_{round(entry, 0):.0f}"
        levels.append({
            "key":        key,
            "ticker":     ticker,
            "epic":       epic,
            "type":       "fvg",
            "direction":  fvg["type"],
            "zone_low":   zone_lo,
            "zone_high":  zone_hi,
            "entry_price": entry,
            "atr":        atr,
        })

    # ── Order Blocks ─────────────────────────────────────────────────────────
    for ob in _detect_order_blocks(highs, lows, opens, closes):
        zone_lo = ob["low"]
        zone_hi = ob["high"]
        # For a bullish OB we buy at the top of the block (price returns and holds).
        # For a bearish OB we sell at the bottom of the block.
        entry = round(zone_hi if ob["type"] == "bullish" else zone_lo, 4)
        key   = f"{ticker}_ob_{ob['type']}_{round(entry, 0):.0f}"
        levels.append({
            "key":        key,
            "ticker":     ticker,
            "epic":       epic,
            "type":       "order_block",
            "direction":  ob["type"],
            "zone_low":   zone_lo,
            "zone_high":  zone_hi,
            "entry_price": entry,
            "atr":        atr,
        })

    # ── Liquidity Pools ───────────────────────────────────────────────────────
    liq = _detect_equal_levels(highs, lows)
    # Equal highs → expect rejection → sell limit
    if liq.get("equal_highs"):
        lv  = liq["equal_highs"]
        key = f"{ticker}_liq_bearish_{round(lv, 0):.0f}"
        levels.append({
            "key":        key,
            "ticker":     ticker,
            "epic":       epic,
            "type":       "liquidity",
            "direction":  "bearish",
            "zone_low":   lv * 0.9995,
            "zone_high":  lv * 1.0005,
            "entry_price": lv,
            "atr":        atr,
        })
    # Equal lows → expect bounce → buy limit
    if liq.get("equal_lows"):
        lv  = liq["equal_lows"]
        key = f"{ticker}_liq_bullish_{round(lv, 0):.0f}"
        levels.append({
            "key":        key,
            "ticker":     ticker,
            "epic":       epic,
            "type":       "liquidity",
            "direction":  "bullish",
            "zone_low":   lv * 0.9995,
            "zone_high":  lv * 1.0005,
            "entry_price": lv,
            "atr":        atr,
        })

    return levels


def _is_triggered(current_price: float, level: dict) -> bool:
    """
    Returns True when price is close enough to place a limit order.

    BUY LIMIT  (bullish direction): price must be ABOVE entry — approaching
                                    from above. Capital.com requires limit
                                    price < current bid for buy limits.
    SELL LIMIT (bearish direction): price must be BELOW entry — approaching
                                    from below.

    Trigger band: within TOUCH_ZONE_PCT (0.2%) of entry.
    """
    entry     = level["entry_price"]
    direction = level["direction"]
    proximity = abs(current_price - entry) / entry

    if proximity > TOUCH_ZONE_PCT:
        return False

    if direction == "bullish":
        return current_price > entry   # above entry → valid BUY LIMIT
    else:
        return current_price < entry   # below entry → valid SELL LIMIT


# ─── Order Lifecycle ────────────────────────────────────────────────────────────

async def _refresh_balance() -> float:
    """Account balance with a 5-minute in-memory cache."""
    global _balance, _balance_ts
    if time.time() - _balance_ts < BALANCE_CACHE_SECS and _balance > 0:
        return _balance
    acct     = await capital_client.get_account_balance()
    _balance = float(acct.get("balance", 0))
    _balance_ts = time.time()
    return _balance


def _calc_sl_tp(entry: float, direction: str,
                atr: float) -> tuple[float, float]:
    """ATR-based stop loss and take profit levels."""
    sl_dist = round(atr * SL_ATR_MULT, 4)
    tp_dist = round(atr * TP_ATR_MULT, 4)
    if direction == "bullish":
        return round(entry - sl_dist, 4), round(entry + tp_dist, 4)
    else:
        return round(entry + sl_dist, 4), round(entry - tp_dist, 4)


def _fmt(v) -> str:
    """Format a price value as a dollar string."""
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return str(v)


async def _place_limit_order(level: dict, bot, chat_id: int):
    """
    Place a limit order at the given level, save to DB, and notify Telegram.
    """
    entry     = level["entry_price"]
    direction = "BUY" if level["direction"] == "bullish" else "SELL"
    atr       = level["atr"]

    balance = await _refresh_balance()
    if balance <= 0:
        print(f"LevelMon: skipping {level['key']} — zero balance")
        return

    sl, tp = _calc_sl_tp(entry, level["direction"], atr)
    stop_dist_pct = abs(entry - sl) / entry * 100
    size = executor.calculate_size(balance, entry, stop_dist_pct)

    result = await capital_client.place_limit_order(
        epic=level["epic"],
        direction=direction,
        size=size,
        level=entry,
        stop_loss=sl,
        take_profit=tp,
    )

    if result["status"] != "success":
        print(
            f"LevelMon: limit order API error — {level['key']}: "
            f"{result.get('reason')}"
        )
        return

    deal_reference = result["deal_reference"]

    # Small pause so Capital.com has processed the order before we confirm
    await asyncio.sleep(0.6)
    confirm  = await capital_client.get_deal_confirmation(deal_reference)
    deal_id  = confirm.get("deal_id") or deal_reference

    if confirm.get("status") == "REJECTED":
        print(
            f"LevelMon: order rejected — {level['key']}: "
            f"{confirm.get('reason')}"
        )
        return

    now = time.time()
    order_info = {
        "deal_id":        deal_id,
        "deal_reference": deal_reference,
        "ticker":         level["ticker"],
        "epic":           level["epic"],
        "direction":      direction,
        "size":           size,
        "level_price":    entry,
        "level_type":     level["type"],
        "level_key":      level["key"],
        "stop_loss":      sl,
        "take_profit":    tp,
        "atr":            atr,
        "placed_at":      now,
        "expires_at":     now + ORDER_EXPIRY_SECS,
    }

    _pending[deal_id]              = order_info
    _used_levels[level["key"]]     = now

    await save_limit_order(order_info)

    ltype = level["type"].upper().replace("_", " ")
    msg = (
        f"───────────────────\n"
        f"LIMIT ORDER PLACED\n"
        f"───────────────────\n"
        f"{level['ticker']} {direction} — {ltype}\n\n"
        f"Limit:   {_fmt(entry)}\n"
        f"Stop:    {_fmt(sl)}\n"
        f"Target:  {_fmt(tp)}\n"
        f"Size:    {size}\n"
        f"ATR:     {_fmt(atr)}\n\n"
        f"Waiting for fill (expires 2h)\n"
        f"───────────────────"
    )
    await bot.send_message(chat_id=chat_id, text=msg)
    print(
        f"LevelMon: {level['ticker']} {direction} limit @ {entry}  "
        f"SL={sl}  TP={tp}  deal={deal_id}"
    )


async def _check_fills_and_expiry(bot, chat_id: int):
    """
    Compares _pending orders against live working orders.
    - Order gone before expiry  → filled notification
    - Order present past expiry → cancel + expired notification
    """
    if not _pending:
        return

    working = await capital_client.get_working_orders()
    live_ids = {w["deal_id"] for w in working if w.get("deal_id")}
    now = time.time()

    for deal_id, order in list(_pending.items()):

        # ── Expiry ──────────────────────────────────────────────────────────
        if now >= order["expires_at"]:
            cancel = await capital_client.cancel_working_order(deal_id)
            status = "cancelled" if cancel.get("status") == "success" \
                     else "expiry_cancel_failed"
            await update_limit_order_status(deal_id, status)
            del _pending[deal_id]

            msg = (
                f"LIMIT ORDER EXPIRED\n"
                f"{order['ticker']} {order['direction']} "
                f"@ {_fmt(order['level_price'])}\n"
                f"Unfilled after 2h — cancelled"
            )
            await bot.send_message(chat_id=chat_id, text=msg)
            print(f"LevelMon: expired & cancelled — {deal_id}")
            continue

        # ── Fill detected (disappeared from working orders) ─────────────────
        if deal_id not in live_ids:
            await update_limit_order_status(deal_id, "filled")
            del _pending[deal_id]

            msg = (
                f"───────────────────\n"
                f"ORDER FILLED\n"
                f"───────────────────\n"
                f"{order['ticker']} {order['direction']}\n"
                f"Filled at {_fmt(order['level_price'])}\n"
                f"SL {_fmt(order['stop_loss'])}  "
                f"TP {_fmt(order['take_profit'])}\n"
                f"Size: {order['size']}\n"
                f"───────────────────"
            )
            await bot.send_message(chat_id=chat_id, text=msg)
            print(
                f"LevelMon: FILLED — {order['ticker']} {order['direction']} "
                f"@ {order['level_price']}  deal={deal_id}"
            )


# ─── Level Refresh ──────────────────────────────────────────────────────────────

async def _refresh_levels(ticker: str, epic: str, candle_epic: str):
    """Fetch fresh candles and recompute all key levels for one instrument."""
    now = time.time()
    if now - _last_refresh.get(ticker, 0) < LEVEL_REFRESH_SECS:
        return  # still fresh

    candles = await capital_client.get_ohlcv(candle_epic, "MINUTE", 100)
    if not candles:
        print(f"LevelMon: no candles for {ticker} — level refresh skipped")
        return

    new_levels = _build_levels(ticker, epic, candles)
    _levels[ticker] = new_levels
    _last_refresh[ticker] = now

    n_fvg = sum(1 for l in new_levels if l["type"] == "fvg")
    n_ob  = sum(1 for l in new_levels if l["type"] == "order_block")
    n_liq = sum(1 for l in new_levels if l["type"] == "liquidity")
    print(
        f"LevelMon: {ticker} levels refreshed — "
        f"FVG:{n_fvg}  OB:{n_ob}  LIQ:{n_liq}  "
        f"total:{len(new_levels)}"
    )


# ─── Main Loop ──────────────────────────────────────────────────────────────────

async def run_level_monitor(bot, chat_id: int):
    print("Level monitor started (15-second interval)...")
    await asyncio.sleep(20)    # let scanners and session settle first

    # Restore any pending orders that survived a restart
    try:
        restored = await get_pending_limit_orders()
        for order in restored:
            deal_id = order.get("deal_id", "")
            if deal_id:
                _pending[deal_id] = order
                if order.get("level_key"):
                    _used_levels[order["level_key"]] = order["placed_at"]
        if restored:
            print(f"LevelMon: restored {len(restored)} pending order(s) from DB")
    except Exception as e:
        print(f"LevelMon: DB restore error — {e}")

    while True:
        try:
            await asyncio.sleep(MONITOR_INTERVAL)

            if risk.kill_switch:
                continue

            await capital_client.ensure_session()
            btc_candle_epic = await resolve_btc_epic()

            # ── Refresh levels (candles, every 2 min) ──────────────────────
            if _is_gold_session():
                await _refresh_levels("GOLD", "GOLD", "GOLD")

            if _is_btc_active():
                await _refresh_levels("BTC-USD", "BTCUSD", btc_candle_epic)

            # ── Price check + level touch detection ────────────────────────
            instruments = []
            if _is_gold_session() and _levels.get("GOLD"):
                instruments.append(("GOLD", "GOLD"))
            if _is_btc_active() and _levels.get("BTC-USD"):
                instruments.append(("BTC-USD", btc_candle_epic))

            for ticker, price_epic in instruments:
                price_data = await capital_client.get_price(price_epic)
                current    = price_data.get("price", 0)
                if not current:
                    continue

                for level in _levels.get(ticker, []):
                    key = level["key"]

                    # Skip if level is in cooldown (recent order placed here)
                    if time.time() - _used_levels.get(key, 0) < LEVEL_COOLDOWN_SECS:
                        continue

                    if not _is_triggered(current, level):
                        continue

                    print(
                        f"LevelMon: {ticker} ${current:,.2f} approaching "
                        f"{level['type']} {level['direction']} "
                        f"@ {_fmt(level['entry_price'])}"
                    )

                    trade_check = await executor.can_trade()
                    if not trade_check["allowed"]:
                        print(f"LevelMon: trade blocked — {trade_check['reason']}")
                        continue

                    await _place_limit_order(level, bot, chat_id)

            # ── Fill / expiry check ────────────────────────────────────────
            await _check_fills_and_expiry(bot, chat_id)

        except Exception as e:
            print(f"Level monitor error: {e}")
