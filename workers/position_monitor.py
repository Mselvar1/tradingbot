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
from services.data.capital import capital_client
from services.memory import save_position_update
from services.risk import risk

MONITOR_INTERVAL = 120    # seconds between checks

# Per-deal state: tracks breakeven status and original levels
# { deal_id: { breakeven_done, original_stop, original_entry, tp1,
#              breakeven_trigger, trail_distance } }
_states: dict = {}


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

    state = {
        "breakeven_done":   False,
        "original_stop":    stop,
        "original_entry":   entry,
        "tp1":              tp1,
        "direction":        direction,
        "breakeven_trigger": round(be_trigger, 2),
        # Trail at half the original stop distance — tight enough to protect
        # profits, wide enough not to get stopped out by normal noise
        "trail_distance":   round(orig_dist * 0.5, 2),
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

async def run_position_monitor(bot, chat_id: int):
    print("Position monitor started (2-minute interval)...")

    while True:
        await asyncio.sleep(MONITOR_INTERVAL)
        try:
            if risk.kill_switch:
                continue

            await capital_client.ensure_session()
            positions = await capital_client.get_positions()

            if not positions:
                # Clean up state for deals that are no longer open
                _states.clear()
                continue

            open_ids = {p.get("deal_id") for p in positions if p.get("deal_id")}

            # Remove state for closed positions
            for deal_id in list(_states.keys()):
                if deal_id not in open_ids:
                    del _states[deal_id]

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

                # Telegram notification
                msg = _build_message(pos, state, old_stop, new_stop, update_type)
                await bot.send_message(chat_id=chat_id, text=msg)

        except Exception as e:
            print(f"Position monitor error: {e}")
