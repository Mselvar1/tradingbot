"""
Intelligent Trade Manager.

Runs every 60 seconds for every open position registered in trade_store.

Decision hierarchy (applied in order):
  1. TP1 / TP2 / TP3 hit         → partial/full close + move SL to BE
  2. First 15 min early exit     → 0.15% adverse move with momentum → exit
  3. CHoCH detected              → exit immediately
  4. 3+ consecutive against      → exit 50% (full if partial fails)
  5. RSI divergence + weak mom   → exit
  6. 2-hour no-progress trap     → exit at market
  7. Claude review (interval from TRADE_REVIEW_INTERVAL_SECONDS) → HOLD / …

All exits are saved to trade_exits table and announced via Telegram.
"""

import asyncio
import datetime
import time

from services.data.capital import capital_client
from services.memory import save_trade_exit
from services.price_tracker import price_tracker
from services.trade_store import trade_store
from services.rate_limiter import claude_limiter
from services.risk import risk
from claude.client import review_trade
from claude.prompts.trade_review import TRADE_REVIEW_PROMPT
from config.settings import settings

MONITOR_INTERVAL = 60   # seconds between position checks


def _claude_review_interval_seconds(ticker: str) -> float:
    """BTC vs non-BTC review cadence (BTC-only aggressive settings stay on BTC)."""
    t = (ticker or "").upper()
    if "BTC" in t:
        return float(getattr(settings, "btc_trade_review_interval_seconds", 600))
    return float(getattr(settings, "gold_trade_review_interval_seconds", 300))


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _current_session() -> str:
    now = datetime.datetime.utcnow()
    t   = now.hour * 60 + now.minute
    if 7 * 60 <= t < 8 * 60 + 30:    return "LONDON OPEN"
    if 13 * 60 + 30 <= t < 15 * 60:  return "NY OPEN"
    if 8 * 60 + 30 <= t < 13 * 60 + 30: return "LONDON MID"
    return "OFF HOURS"


def _pnl_pct(current: float, entry: float, direction: str) -> float:
    if not entry:
        return 0.0
    if direction == "BUY":
        return round((current - entry) / entry * 100, 4)
    else:
        return round((entry - current) / entry * 100, 4)


def _pnl_euros(pnl_pct: float, entry: float, size: float) -> float:
    """Approximate euro P&L: pnl_pct% of (entry × size)."""
    return round(pnl_pct / 100 * entry * size, 2)


def _detect_choch(candles: list, direction: str) -> bool:
    """
    Simple CHoCH detection: check if second half of recent candles
    broke the structure established in the first half.
    """
    if len(candles) < 10:
        return False
    recent = candles[-10:]
    highs  = [c["high"] for c in recent]
    lows   = [c["low"]  for c in recent]
    mid    = len(recent) // 2

    if direction == "BUY":
        # Bearish CHoCH: second half makes a lower low than first half
        first_low  = min(lows[:mid])
        second_low = min(lows[mid:])
        return second_low < first_low * 0.9994
    else:
        # Bullish CHoCH: second half makes a higher high than first half
        first_high  = max(highs[:mid])
        second_high = max(highs[mid:])
        return second_high > first_high * 1.0006


def _consec_against(snap: dict, direction: str) -> int:
    """Return the count of consecutive candles running against the trade."""
    if not snap:
        return 0
    c = snap.get("consec_candles", 0)
    if direction == "BUY" and c < 0:
        return abs(c)
    if direction == "SELL" and c > 0:
        return abs(c)
    return 0


def _tp_hit(current: float, tp: float, direction: str) -> bool:
    if not tp:
        return False
    return current >= tp if direction == "BUY" else current <= tp


# ─── Telegram message builders ────────────────────────────────────────────────

def _msg_management(ticker: str, direction: str, entry: float,
                    current: float, action: str, reason: str,
                    pnl_euros: float) -> str:
    sign = "+" if pnl_euros >= 0 else ""
    return (
        f"🔄 TRADE UPDATE — {ticker} {direction}\n"
        f"Entry: {entry:.2f} | Current: {current:.2f}\n"
        f"Action: {action}\n"
        f"Reason: {reason}\n"
        f"P&L: {sign}€{pnl_euros:.2f}"
    )


def _msg_closed(ticker: str, direction: str, entry: float, exit_p: float,
                pnl_pct: float, pnl_euros: float, hold_mins: int,
                exit_reason: str, saved_euros: float | None = None) -> str:
    sign   = "+" if pnl_euros >= 0 else ""
    result = "WIN" if pnl_euros >= 0 else "LOSS"
    saved  = f"\nSaved vs SL: +€{saved_euros:.2f}" if saved_euros and saved_euros > 0 else ""
    return (
        f"📊 TRADE CLOSED — {ticker} {direction}\n"
        f"Entry: {entry:.2f} | Exit: {exit_p:.2f}\n"
        f"Result: {sign}€{pnl_euros:.2f} ({pnl_pct:+.3f}%) — {result}\n"
        f"Hold time: {hold_mins} min | Exit: {exit_reason.replace('_', ' ').upper()}"
        f"{saved}"
    )


def _msg_early_exit(ticker: str, direction: str, entry: float, exit_p: float,
                    pnl_euros: float, saved_euros: float, reason: str) -> str:
    sign = "+" if pnl_euros >= 0 else ""
    return (
        f"⚠️ EARLY EXIT — {ticker} {direction}\n"
        f"Entry: {entry:.2f} | Exit: {exit_p:.2f}\n"
        f"Result: {sign}€{pnl_euros:.2f} (saved €{max(0, saved_euros):.2f} vs SL)\n"
        f"Reason: {reason}"
    )


# ─── Core exit executor ───────────────────────────────────────────────────────

async def _execute_exit(deal_id: str, pos: dict, entry_data: dict,
                         exit_reason: str, current: float,
                         bot, chat_id: int, is_early: bool = False) -> bool:
    """Close a position fully, save to DB, send Telegram, clean up trade_store."""
    signal    = entry_data["signal"]
    trade     = entry_data["trade"]
    direction = (signal.get("action") or "buy").upper()
    entry     = _f(trade.get("entry_price") or signal.get("price"))
    size      = _f(trade.get("size", 1))
    stop      = _f(signal.get("stop_loss"))
    confs     = signal.get("confluences", [])
    session   = _current_session()

    hold_mins = max(1, int((time.time() - entry_data["opened_at"]) / 60))
    pnl_pct   = _pnl_pct(current, entry, direction)
    euros     = _pnl_euros(pnl_pct, entry, size)

    # SL loss we avoided (for early exits)
    sl_loss_pct = abs((entry - stop) / entry * 100) if entry and stop else 0.0
    saved_pct   = sl_loss_pct - abs(pnl_pct) if pnl_pct < 0 else 0.0
    saved_euros = _pnl_euros(saved_pct, entry, size) if saved_pct > 0 else 0.0

    await capital_client.ensure_session()
    result = await capital_client.close_position(deal_id)
    if result.get("status") != "success":
        print(f"TradeManager: failed to close {deal_id} — {result.get('reason', result)}")
        return False

    # Mark closed before position_monitor runs
    trade_store.mark_closed(deal_id)

    # Save to DB
    await save_trade_exit({
        "deal_id":         deal_id,
        "ticker":          signal.get("ticker", "UNKNOWN"),
        "direction":       direction,
        "entry_price":     entry,
        "exit_price":      current,
        "size":            size,
        "pnl_pct":         pnl_pct,
        "pnl_euros":       euros,
        "exit_reason":     exit_reason,
        "hold_minutes":    hold_mins,
        "confluences":     confs,
        "session":         session,
        "entry_narrative": entry_data.get("entry_narrative", ""),
        "exit_narrative":  price_tracker.get_narrative(signal.get("ticker", "GOLD")),
        "sl_loss_pct":     sl_loss_pct,
        "saved_vs_sl_pct": saved_pct,
    })

    # Telegram
    if is_early:
        msg = _msg_early_exit(
            signal.get("ticker", "?"), direction, entry, current,
            euros, saved_euros,
            exit_reason.replace("_", " ")
        )
    else:
        msg = _msg_closed(
            signal.get("ticker", "?"), direction, entry, current,
            pnl_pct, euros, hold_mins, exit_reason
        )
    try:
        await bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        print(f"TradeManager: Telegram send error — {e}")

    print(
        f"TradeManager: {signal.get('ticker')} {direction} closed — "
        f"{exit_reason}  P&L:{pnl_pct:+.3f}%  €{euros:+.2f}"
    )
    return True


async def _execute_partial(deal_id: str, pos: dict, entry_data: dict,
                            reason: str, current: float,
                            bot, chat_id: int) -> bool:
    """Close 50% of position (or full if partial unsupported)."""
    signal    = entry_data["signal"]
    trade     = entry_data["trade"]
    direction = (signal.get("action") or "buy").upper()
    entry     = _f(trade.get("entry_price") or signal.get("price"))
    size      = _f(trade.get("size", 1))
    euros     = _pnl_euros(_pnl_pct(current, entry, direction), entry, size * 0.5)

    await capital_client.ensure_session()
    result = await capital_client.close_position_partial(deal_id, round(size * 0.5, 3))

    was_partial = result.get("partial", False) and result.get("status") == "success"
    full_closed = not was_partial and result.get("status") == "success"

    if result.get("status") != "success":
        print(f"TradeManager: partial close failed for {deal_id} — {result}")
        return False

    action_label = "TOOK 50% PROFIT" if was_partial else "CLOSED FULL (partial unsupported)"
    msg = _msg_management(
        signal.get("ticker", "?"), direction, entry, current,
        action_label, reason, euros
    )
    try:
        await bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        print(f"TradeManager: Telegram send error — {e}")

    if full_closed:
        # Full close happened — clean up completely
        pnl_pct = _pnl_pct(current, entry, direction)
        hold    = max(1, int((time.time() - entry_data["opened_at"]) / 60))
        trade_store.mark_closed(deal_id)
        await save_trade_exit({
            "deal_id":      deal_id,
            "ticker":       signal.get("ticker", "UNKNOWN"),
            "direction":    direction,
            "entry_price":  entry,
            "exit_price":   current,
            "size":         size,
            "pnl_pct":      pnl_pct,
            "pnl_euros":    _pnl_euros(pnl_pct, entry, size),
            "exit_reason":  reason,
            "hold_minutes": hold,
            "confluences":  signal.get("confluences", []),
            "session":      _current_session(),
        })

    return True


# ─── Claude trade review ──────────────────────────────────────────────────────

async def _claude_review(deal_id: str, pos: dict, entry_data: dict,
                          snap: dict | None, hold_mins: float,
                          pnl_pct: float, bot, chat_id: int) -> None:
    signal    = entry_data["signal"]
    trade     = entry_data["trade"]
    direction = (signal.get("action") or "buy").upper()
    entry     = _f(trade.get("entry_price") or signal.get("price"))
    current   = _f(pos.get("current_price"))
    size      = _f(trade.get("size", 1))

    momentum       = snap.get("momentum", "unknown")          if snap else "unknown"
    rsi_divergence = snap.get("rsi_divergence", "none")       if snap else "none"
    rsi            = snap.get("rsi", 50)                      if snap else 50
    candles        = price_tracker.get_candles(signal.get("ticker", "GOLD"))
    choch          = _detect_choch(candles, direction) if candles else False
    consec         = _consec_against(snap, direction)

    try:
        prompt = TRADE_REVIEW_PROMPT.format(
            ticker      = signal.get("ticker", "?"),
            direction   = direction,
            entry       = entry,
            stop_loss   = signal.get("stop_loss", "n/a"),
            tp1         = signal.get("tp1", "n/a"),
            tp2         = signal.get("tp2", "n/a"),
            confluences = ", ".join(signal.get("confluences", [])) or "none",
            thesis      = signal.get("summary") or signal.get("analysis_summary") or "n/a",
            current_price    = f"{current:.2f}",
            hold_minutes     = int(hold_mins),
            pnl_pct          = pnl_pct,
            rsi              = rsi,
            momentum         = momentum,
            rsi_divergence   = rsi_divergence,
            consec_against   = consec,
            choch_detected   = choch,
            price_narrative  = price_tracker.get_narrative(signal.get("ticker", "GOLD")),
        )
    except KeyError as e:
        print(f"TradeManager: Claude review prompt error — {e}")
        return

    result = await review_trade(prompt)
    if "error" in result:
        print(f"TradeManager: Claude review parse error — {result}")
        return

    decision = result.get("decision", "HOLD")
    reason   = result.get("reason", "n/a")
    print(f"TradeManager: Claude says {decision} for {deal_id} — {reason}")

    if decision == "EXIT_NOW":
        await _execute_exit(deal_id, pos, entry_data, "claude_exit",
                            current, bot, chat_id, is_early=(pnl_pct < 0))

    elif decision == "TAKE_PARTIAL_PROFIT":
        await _execute_partial(deal_id, pos, entry_data, reason, current, bot, chat_id)

    elif decision == "MOVE_STOP_TO_BREAKEVEN":
        if not entry_data.get("be_moved"):
            tp2  = _f(signal.get("tp2") or signal.get("tp1"))
            res  = await capital_client.update_stop_loss(deal_id, entry, tp2)
            if res.get("status") == "success":
                trade_store.update(deal_id, be_moved=True)
                euros = _pnl_euros(pnl_pct, entry, size)
                msg   = _msg_management(
                    signal.get("ticker", "?"), direction, entry, current,
                    "MOVED SL TO BREAKEVEN", reason, euros
                )
                try:
                    await bot.send_message(chat_id=chat_id, text=msg)
                except Exception:
                    pass


# ─── Main Loop ────────────────────────────────────────────────────────────────

async def run_trade_manager(bot, chat_id: int):
    await asyncio.sleep(15)   # let scanners start first
    print("Trade manager started (60-second interval)...")

    while True:
        await asyncio.sleep(MONITOR_INTERVAL)
        try:
            if risk.kill_switch:
                continue

            await capital_client.ensure_session()
            positions = await capital_client.get_positions()
            if not positions:
                continue

            now = time.time()
            pos_map = {p.get("deal_id"): p for p in positions if p.get("deal_id")}

            # Detect tracked deals that disappeared (SL hit externally)
            tracked = trade_store.get_all()
            for deal_id, entry_data in list(tracked.items()):
                if deal_id not in pos_map:
                    # SL or external close — position_monitor will handle outcome recording
                    print(f"TradeManager: {deal_id} disappeared (SL or external close)")
                    trade_store.remove(deal_id)

            # Manage open positions
            for deal_id, pos in pos_map.items():
                entry_data = trade_store.get(deal_id)
                if not entry_data:
                    continue

                signal    = entry_data["signal"]
                trade_rec = entry_data["trade"]
                direction = (signal.get("action") or "buy").upper()
                entry     = _f(trade_rec.get("entry_price") or signal.get("price"))
                size      = _f(trade_rec.get("size", 1))
                current   = _f(pos.get("current_price"))
                tp1       = _f(signal.get("tp1"))
                tp2       = _f(signal.get("tp2"))
                tp3       = _f(signal.get("tp3"))
                stop      = _f(signal.get("stop_loss"))
                ticker    = signal.get("ticker", "GOLD")

                hold_secs = now - entry_data["opened_at"]
                hold_mins = hold_secs / 60
                pnl_pct   = _pnl_pct(current, entry, direction)

                snap    = price_tracker.get_latest(ticker)
                candles = price_tracker.get_candles(ticker)

                # ── 1. TP3 hit → full close ───────────────────────────────
                if tp3 and entry_data.get("tp2_hit") and _tp_hit(current, tp3, direction):
                    await _execute_exit(deal_id, pos, entry_data, "TP3",
                                        current, bot, chat_id)
                    continue

                # ── 2. TP2 hit → close 30% more, trail to TP1 ────────────
                if tp2 and entry_data.get("tp1_hit") and not entry_data.get("tp2_hit"):
                    if _tp_hit(current, tp2, direction):
                        res = await _execute_partial(deal_id, pos, entry_data,
                                                     "TP2 hit", current, bot, chat_id)
                        if res:
                            trade_store.update(deal_id, tp2_hit=True)
                            # Trail stop to TP1 level
                            if tp1:
                                await capital_client.update_stop_loss(deal_id, tp1, tp2 or tp3 or tp1)
                        continue

                # ── 3. TP1 hit → close 50%, move SL to BE ────────────────
                if tp1 and not entry_data.get("tp1_hit"):
                    if _tp_hit(current, tp1, direction):
                        res = await _execute_partial(deal_id, pos, entry_data,
                                                     "TP1 hit", current, bot, chat_id)
                        if res:
                            trade_store.update(deal_id, tp1_hit=True, be_moved=True)
                            # Move SL to breakeven
                            await capital_client.update_stop_loss(deal_id, entry, tp2 or tp1)
                        continue

                # ── 4. First 15 min: early exit on adverse momentum ───────
                if hold_mins < 15:
                    adverse = (
                        (direction == "BUY"  and current < entry * 0.9985) or
                        (direction == "SELL" and current > entry * 1.0015)
                    )
                    mom = (snap or {}).get("momentum", "")
                    if adverse and ("bearish" in mom if direction == "BUY" else "bullish" in mom):
                        await _execute_exit(deal_id, pos, entry_data,
                                            "early_exit_momentum", current,
                                            bot, chat_id, is_early=True)
                        continue

                # ── 5. CHoCH detected → exit immediately ──────────────────
                if candles and _detect_choch(candles, direction):
                    await _execute_exit(deal_id, pos, entry_data, "choch",
                                        current, bot, chat_id, is_early=(pnl_pct < 0))
                    continue

                # ── 6. 3+ consecutive candles against + losing ────────────
                consec = _consec_against(snap, direction)
                if consec >= 3 and pnl_pct < 0:
                    await _execute_partial(deal_id, pos, entry_data,
                                           "3 consecutive candles against direction",
                                           current, bot, chat_id)
                    continue

                # ── 7. RSI divergence + weak momentum ─────────────────────
                if snap:
                    div = snap.get("rsi_divergence", "none")
                    mom = snap.get("momentum", "")
                    div_against = (
                        (direction == "BUY"  and div == "bearish_divergence") or
                        (direction == "SELL" and div == "bullish_divergence")
                    )
                    if div_against and "weak" in mom and pnl_pct < 0:
                        await _execute_exit(deal_id, pos, entry_data,
                                            "rsi_divergence", current,
                                            bot, chat_id, is_early=True)
                        continue

                # ── 8. 2-hour no progress → time exit ─────────────────────
                if hold_mins > 120 and abs(pnl_pct) < 0.10:
                    await _execute_exit(deal_id, pos, entry_data, "time_exit",
                                        current, bot, chat_id)
                    continue

                # ── 9. Claude review (interval from settings; default 10 min) ─
                last_review = entry_data.get("last_claude_review", 0.0)
                if now - last_review >= _claude_review_interval_seconds(ticker):
                    trade_store.update(deal_id, last_claude_review=now)
                    if await claude_limiter.acquire("TRADE_REVIEW"):
                        await _claude_review(deal_id, pos, entry_data,
                                             snap, hold_mins, pnl_pct,
                                             bot, chat_id)

        except Exception as e:
            print(f"Trade manager error: {e}")
