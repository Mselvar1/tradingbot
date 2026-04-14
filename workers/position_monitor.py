"""
Position Monitor — runs every 2 minutes.

For every open Capital.com position:

  1. BREAKEVEN — when price reaches 50% of the distance from entry to TP1,
     move the stop loss to entry price. Locks in risk-free trade.

  2. TRAILING — once the stop is at or past breakeven, trail it at half
     the original stop distance behind the current price. Only ever moves
     in the profitable direction (never backwards).

Notifications are sent to Telegram on every stop move.
Every stop move is saved to the position_updates database table.

State is kept in memory (_states dict). After a bot restart positions
re-initialise from live Capital.com data on the first check.
"""

import asyncio
import datetime
import time
from services.data.capital import capital_client
from services.memory import save_position_update
from services.risk import risk
from services.learning import record_closed_position
from services.trade_store import trade_store

MONITOR_INTERVAL = 120    # seconds between checks

# Per-deal state: tracks breakeven status and original levels
# { deal_id: { breakeven_done, original_stop, original_entry, tp1,
#              breakeven_trigger, trail_distance,
#              opened_at, last_price, current_stop, ticker, session_at_open } }
_states: dict = {}

# Deal IDs seen in the PREVIOUS cycle — used to detect closures
_prev_open_ids: set = set()


def _current_session() -> str:
    """Simplified session name for closure recording."""
    now = datetime.datetime.utcnow()
    t   = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return "WEEKEND"
    if 8*60 <= t < 10*60:      return "LONDON OPEN"
    if 10*60 <= t < 13*60+30:  return "LONDON MID"
    if 13*60+30 <= t < 15*60+30: return "NY OPEN"
    if 15*60+30 <= t < 17*60:  return "LONDON/NY OVERLAP"
    if 17*60 <= t < 20*60:     return "NY SESSION"
    return "OFF HOURS"


# ─── State Management ─────────────────────────────────────────────────────────

def _init_state(deal_id: str, pos: dict) -> dict:
    """Initialise state for a position we haven't seen before."""
    entry = _f(pos.get("entry_price"))
    stop  = _f(pos.get("stop_loss"))
    tp1   = _f(pos.get("take_profit"))
    direction = (pos.get("direction") or "BUY").upper()

    orig_dist = abs(entry - stop) if entry and stop else 0

    # Breakeven triggers when price reaches 50% of the entry→TP1 distance
    if tp1 and entry:
        if direction == "BUY":
            be_trigger = entry + (tp1 - entry) * 0.5
        else:
            be_trigger = entry - (entry - tp1) * 0.5
    else:
        be_trigger = entry

    ticker = pos.get("name") or pos.get("epic") or "UNKNOWN"

    state = {
        "breakeven_done":    False,
        "original_stop":     stop,
        "original_entry":    entry,
        "tp1":               tp1,
        "direction":         direction,
        "breakeven_trigger": round(be_trigger, 2),
        "trail_distance":    round(orig_dist * 0.5, 2),
        # ── Self-learning fields ──────────────────────────────────────────
        "opened_at":         time.time(),
        "last_price":        _f(pos.get("current_price")),
        "current_stop":      stop,      # updated whenever stop moves
        "ticker":            ticker,
        "session_at_open":   _current_session(),
    }
    _states[deal_id] = state
    return state


def _f(v) -> float:
    """Safe float conversion."""
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ─── Stop Loss Logic ──────────────────────────────────────────────────────────

def _next_stop(pos: dict, state: dict) -> tuple[float | None, str | None]:
    """
    Determine whether the stop loss should be updated.
    Returns (new_stop, update_type) or (None, None) if no action needed.
    update_type: 'breakeven' | 'trailing'
    """
    direction   = state["direction"]
    entry       = state["original_entry"]
    be_trigger  = state["breakeven_trigger"]
    trail_dist  = state["trail_distance"]
    be_done     = state["breakeven_done"]

    current     = _f(pos.get("current_price"))
    current_stop = _f(pos.get("stop_loss"))

    if not all([entry, current, current_stop, trail_dist]):
        return None, None

    if direction == "BUY":
        # ── Step 1: breakeven ──────────────────────────────────────────────
        if not be_done and current >= be_trigger and current_stop < entry:
            return round(entry, 2), "breakeven"

        # ── Step 2: trailing ──────────────────────────────────────────────
        # Only trail once stop is at or above entry (after breakeven)
        if current_stop >= entry:
            candidate = round(current - trail_dist, 2)
            # Move stop up only — require at least a 0.03% improvement
            # to avoid tiny micro-adjustments on every tick
            min_step = max(current_stop * 0.0003, 0.01)
            if candidate > current_stop + min_step:
                return candidate, "trailing"

    else:  # SELL
        if not be_done and current <= be_trigger and current_stop > entry:
            return round(entry, 2), "breakeven"

        if current_stop <= entry:
            candidate = round(current + trail_dist, 2)
            min_step = max(current_stop * 0.0003, 0.01)
            if candidate < current_stop - min_step:
                return candidate, "trailing"

    return None, None


# ─── Telegram Message ─────────────────────────────────────────────────────────

def _build_message(pos: dict, state: dict, old_stop: float,
                   new_stop: float, update_type: str) -> str:
    direction = state["direction"]
    entry     = state["original_entry"]
    current   = _f(pos.get("current_price"))
    ticker    = pos.get("name") or pos.get("epic") or "?"
    pnl_pct   = 0.0
    if entry:
        pnl_pct = (current - entry) / entry * 100 if direction == "BUY" \
                  else (entry - current) / entry * 100

    if update_type == "breakeven":
        action_line = "STOP MOVED TO BREAKEVEN — trade now risk-free"
    else:
        action_line = "TRAILING STOP UPDATED"

    # Format prices: BTC needs commas, Gold needs 3dp
    def fmt(v):
        try:
            f = float(v)
            return f"${f:,.2f}"
        except Exception:
            return str(v)

    return (
        f"───────────────────\n"
        f"POSITION UPDATE\n"
        f"───────────────────\n"
        f"{ticker} {direction}\n"
        f"{action_line}\n\n"
        f"Entry:    {fmt(entry)}\n"
        f"Old stop: {fmt(old_stop)}\n"
        f"New stop: {fmt(new_stop)}\n"
        f"Current:  {fmt(current)}\n"
        f"P&L:      {pnl_pct:+.3f}%\n"
        f"───────────────────"
    )


# ─── Main Loop ────────────────────────────────────────────────────────────────

async def _handle_closed_positions(closed_ids: set, session: str, bot, chat_id: int) -> None:
    """Record outcomes for any deal_ids that disappeared this cycle."""
    for deal_id in closed_ids:
        # Skip deals intentionally closed by trade_manager — it records them separately
        if deal_id in trade_store.manager_closed:
            trade_store.manager_closed.discard(deal_id)
            _states.pop(deal_id, None)
            continue
        state = _states.get(deal_id)
        if not state:
            continue
        hold_secs  = time.time() - state.get("opened_at", time.time())
        entry      = state["original_entry"]
        last_price = state.get("last_price", entry)
        direction  = state["direction"]
        ticker     = state.get("ticker", "UNKNOWN")
        tp1        = state["tp1"]

        if direction == "BUY":
            pnl_pct = round((last_price - entry) / entry * 100, 3) if entry else 0.0
        else:
            pnl_pct = round((entry - last_price) / entry * 100, 3) if entry else 0.0

        hold_mins = max(1, int(hold_secs / 60))

        # Infer result label for the notification
        result_label = "WIN" if pnl_pct > 0 else "LOSS"

        def fmt(v):
            try:
                return f"${float(v):,.2f}"
            except Exception:
                return str(v)

        try:
            close_msg = (
                f"───────────────────\n"
                f"TRADE CLOSED\n"
                f"───────────────────\n"
                f"{ticker} {direction}\n"
                f"Result: {result_label}\n\n"
                f"Entry:    {fmt(entry)}\n"
                f"Exit:     {fmt(last_price)}\n"
                f"P&L:      {pnl_pct:+.3f}%\n"
                f"Held:     {hold_mins} min\n"
                f"Session:  {session}\n"
                f"───────────────────"
            )
            await bot.send_message(chat_id=chat_id, text=close_msg)
        except Exception as e:
            print(f"Monitor: failed to send close notification — {e}")

        await record_closed_position(
            deal_id      = deal_id,
            entry        = entry,
            last_price   = last_price,
            current_stop = state.get("current_stop", state["original_stop"]),
            tp1          = tp1,
            direction    = direction,
            hold_secs    = hold_secs,
            ticker       = ticker,
            session      = session,
        )
        del _states[deal_id]


async def run_position_monitor(bot, chat_id: int):
    print("Position monitor started (2-minute interval)...")
    global _prev_open_ids

    while True:
        await asyncio.sleep(MONITOR_INTERVAL)
        try:
            if risk.kill_switch:
                continue

            await capital_client.ensure_session()
            positions = await capital_client.get_positions()
            session   = _current_session()

            if not positions:
                # All positions closed — record outcomes for everything we knew
                if _prev_open_ids:
                    closed = set(_prev_open_ids)
                    await _handle_closed_positions(closed, session, bot, chat_id)
                _states.clear()
                _prev_open_ids = set()
                continue

            current_ids = {p.get("deal_id") for p in positions if p.get("deal_id")}

            # ── Detect closed positions (were open last cycle, gone now) ──
            closed_ids = _prev_open_ids - current_ids
            if closed_ids:
                print(f"Monitor: {len(closed_ids)} position(s) closed — recording outcomes")
                await _handle_closed_positions(closed_ids, session, bot, chat_id)

            # ── Update last_price snapshot for all live positions ──────────
            for pos in positions:
                did = pos.get("deal_id")
                if did and did in _states:
                    _states[did]["last_price"] = _f(pos.get("current_price"))

            print(f"Monitor: checking {len(positions)} position(s)...")

            for pos in positions:
                deal_id = pos.get("deal_id")
                if not deal_id:
                    continue

                # Initialise state if first time seeing this deal
                state = _states.get(deal_id) or _init_state(deal_id, pos)

                new_stop, update_type = _next_stop(pos, state)
                if new_stop is None:
                    continue

                old_stop = _f(pos.get("stop_loss"))
                tp       = _f(pos.get("take_profit"))

                # Apply the update to Capital.com
                result = await capital_client.update_stop_loss(
                    deal_id=deal_id,
                    stop_loss=new_stop,
                    take_profit=tp,
                )

                if result.get("status") != "success":
                    print(f"Monitor: stop update failed for {deal_id}: {result.get('reason')}")
                    continue

                # Update in-memory state
                if update_type == "breakeven":
                    state["breakeven_done"] = True
                state["current_stop"] = new_stop   # track moving stop for outcome calc

                ticker    = pos.get("name") or pos.get("epic") or "?"
                direction = state["direction"]
                current   = _f(pos.get("current_price"))

                print(
                    f"Monitor: {ticker} {direction}  "
                    f"{update_type.upper()}  "
                    f"{old_stop} → {new_stop}  "
                    f"(price={current})"
                )

                # Save to database
                await save_position_update({
                    "deal_id":       deal_id,
                    "ticker":        ticker,
                    "direction":     direction,
                    "entry_price":   state["original_entry"],
                    "old_stop":      old_stop,
                    "new_stop":      new_stop,
                    "current_price": current,
                    "update_type":   update_type,
                })

                # Only notify Telegram on breakeven — trailing updates are silent
                if update_type == "breakeven":
                    msg = _build_message(pos, state, old_stop, new_stop, update_type)
                    await bot.send_message(chat_id=chat_id, text=msg)

            # ── Advance prev-ids for next cycle ───────────────────────────
            _prev_open_ids = current_ids

        except Exception as e:
            print(f"Position monitor error: {e}")
