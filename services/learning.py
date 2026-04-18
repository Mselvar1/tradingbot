"""
Self-learning engine.

Components
──────────
1. Deal→Signal map      — links Capital.com deal_ids to signal DB rows so we
                          can attribute closed positions back to their setups.

2. Closure recorder     — called by position_monitor when a position disappears;
                          saves an outcome row with entry/exit/PnL/session/RSI.

3. Pattern analyser     — runs after every 5 new outcomes; computes win rates
                          by session, confluence, RSI bucket, and trend direction;
                          saves a trade_insights row.

4. Dynamic thresholds   — reads the latest trade_insights row; returns a
                          per-session confidence threshold for each scanner.

5. Prompt injector      — formats the top-3/worst-3 setups as text that is
                          injected into every Claude analysis prompt.
"""

import json
import datetime

from services.memory import (
    save_outcome,
    save_trade_insight,
    get_latest_insight,
    get_outcomes_for_analysis,
    get_weekly_exits,
)

# ─── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_THRESHOLD_GOLD = 60
DEFAULT_THRESHOLD_BTC  = 55
_TRIGGER_ANALYSIS_EVERY = 5   # run pattern analysis after this many new outcomes

# ─── In-Memory State ────────────────────────────────────────────────────────────

# deal_id → {signal_id, ticker, rsi, trend_direction, confluences, session}
_deal_signal_map: dict[str, dict] = {}

# Count of new outcomes since last analysis run, per ticker
_new_outcomes_count: dict[str, int] = {}


# ─── Deal Registration ──────────────────────────────────────────────────────────

def register_trade_signal(deal_id: str, signal_id: int, ticker: str,
                           rsi: float = 0.0, trend_direction: str = "",
                           confluences: list = None, session: str = "") -> None:
    """
    Called immediately after a trade is placed.
    Stores enough metadata to reconstruct outcome context when the position
    later closes (even if the position_monitor restarts between open and close).
    """
    _deal_signal_map[deal_id] = {
        "signal_id":       signal_id,
        "ticker":          ticker,
        "rsi":             rsi,
        "trend_direction": trend_direction,
        "confluences":     confluences or [],
        "session":         session,
    }
    print(f"Learning: registered deal {deal_id} → signal {signal_id} ({ticker})")


# ─── Closed Position Recording ─────────────────────────────────────────────────

def _determine_result(exit_price: float, entry: float,
                       stop: float, tp1: float, direction: str) -> str:
    """Infer whether the trade hit TP, SL, or was manually closed."""
    if direction == "BUY":
        if tp1 > 0 and exit_price >= tp1 * 0.998:
            return "tp1"
        if stop > 0 and exit_price <= stop * 1.002:
            return "sl"
    else:  # SELL
        if tp1 > 0 and exit_price <= tp1 * 1.002:
            return "tp1"
        if stop > 0 and exit_price >= stop * 0.998:
            return "sl"
    return "manual_close"


async def record_closed_position(
    deal_id: str,
    entry: float,
    last_price: float,
    current_stop: float,
    tp1: float,
    direction: str,
    hold_secs: float,
    ticker: str,
    session: str,
) -> None:
    """
    Called by position_monitor when a deal disappears from GET /positions.
    Saves an outcomes row and triggers pattern analysis if threshold is met.
    """
    exit_price  = last_price
    result      = _determine_result(exit_price, entry, current_stop, tp1, direction)
    hold_mins   = max(1, int(hold_secs / 60))
    action      = "buy" if direction == "BUY" else "sell"

    if direction == "BUY":
        pnl_pct = round((exit_price - entry) / entry * 100, 3) if entry else 0.0
    else:
        pnl_pct = round((entry - exit_price) / entry * 100, 3) if entry else 0.0

    # Look up signal metadata if this deal was registered
    meta       = _deal_signal_map.get(deal_id, {})
    signal_id  = meta.get("signal_id", 0)
    rsi        = meta.get("rsi", 0.0)
    trend      = meta.get("trend_direction", "")
    confs      = meta.get("confluences", [])

    outcome = {
        "ticker":          ticker,
        "action":          action,
        "entry_price":     entry,
        "exit_price":      exit_price,
        "stop_loss":       current_stop,
        "take_profit":     tp1,
        "result":          result,
        "pnl_pct":         pnl_pct,
        "hold_minutes":    hold_mins,
        "session":         session,
        "notes":           f"auto-detected close | deal={deal_id}",
        "rsi_at_entry":    rsi,
        "trend_direction": trend,
        "confluences":     confs,
    }

    await save_outcome(signal_id, outcome)

    try:
        from services.signal_platform.circuit_breaker import on_trade_outcome

        await on_trade_outcome(result, ticker)
    except Exception as e:
        print(f"Circuit breaker hook error: {e}")

    # Clean up memory
    _deal_signal_map.pop(deal_id, None)

    print(
        f"Learning: {ticker} {action} closed — "
        f"{result}  PnL:{pnl_pct:+.2f}%  hold:{hold_mins}m  session:{session}"
    )

    # Increment counter and maybe run pattern analysis
    _new_outcomes_count[ticker] = _new_outcomes_count.get(ticker, 0) + 1
    if _new_outcomes_count[ticker] >= _TRIGGER_ANALYSIS_EVERY:
        _new_outcomes_count[ticker] = 0
        await run_pattern_analysis(ticker)


# ─── Pattern Analysis ───────────────────────────────────────────────────────────

def _rsi_bucket(rsi) -> str:
    if rsi is None or rsi == 0:
        return "unknown"
    r = float(rsi)
    if r < 30:  return "oversold (<30)"
    if r < 45:  return "low (30-45)"
    if r < 55:  return "neutral (45-55)"
    if r < 70:  return "high (55-70)"
    return "overbought (>70)"


def _is_win(result: str) -> bool:
    return result in ("tp1", "tp2", "tp3")


def _wr(wins: int, total: int) -> float:
    return round(wins / total, 3) if total > 0 else 0.0


async def run_pattern_analysis(ticker: str) -> dict:
    """
    Analyse the last 20 outcomes for `ticker`.
    Computes win rates by session, confluence, RSI bucket, trend direction.
    Saves a trade_insights row and returns the insights dict.
    """
    outcomes = await get_outcomes_for_analysis(ticker, limit=20)
    if len(outcomes) < 3:
        print(f"Learning: {ticker} — not enough data for analysis ({len(outcomes)} outcomes)")
        return {}

    total    = len(outcomes)
    wins     = sum(1 for o in outcomes if _is_win(o["result"]))
    overall  = _wr(wins, total)

    # ── Session win rates ────────────────────────────────────────────────
    sess: dict[str, dict] = {}
    for o in outcomes:
        s = (o.get("session") or "unknown").upper()
        sess.setdefault(s, {"w": 0, "t": 0, "pnl": 0.0})
        sess[s]["t"] += 1
        sess[s]["pnl"] += float(o.get("pnl_pct") or 0)
        if _is_win(o["result"]):
            sess[s]["w"] += 1
    session_wr = {s: _wr(d["w"], d["t"]) for s, d in sess.items()}

    # ── Confluence win rates (each confluence individually) ──────────────
    conf: dict[str, dict] = {}
    for o in outcomes:
        raw = o.get("confluences") or "[]"
        try:
            confs = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            confs = []
        for c in (confs or []):
            c = str(c).strip()
            if not c:
                continue
            conf.setdefault(c, {"w": 0, "t": 0, "pnl": 0.0})
            conf[c]["t"] += 1
            conf[c]["pnl"] += float(o.get("pnl_pct") or 0)
            if _is_win(o["result"]):
                conf[c]["w"] += 1
    confluence_wr = {
        c: _wr(d["w"], d["t"])
        for c, d in conf.items()
        if d["t"] >= 2    # minimum 2 samples
    }

    # ── RSI bucket win rates ─────────────────────────────────────────────
    rsi_b: dict[str, dict] = {}
    for o in outcomes:
        bucket = _rsi_bucket(o.get("rsi_at_entry"))
        rsi_b.setdefault(bucket, {"w": 0, "t": 0})
        rsi_b[bucket]["t"] += 1
        if _is_win(o["result"]):
            rsi_b[bucket]["w"] += 1
    rsi_bucket_wr = {b: _wr(d["w"], d["t"]) for b, d in rsi_b.items() if d["t"] >= 2}

    # ── Top setups (session × best confluence) ───────────────────────────
    # Build (session, action) → {w, t, pnl, confluences_seen}
    setup: dict[str, dict] = {}
    for o in outcomes:
        s     = (o.get("session") or "unknown").upper()
        act   = (o.get("action") or "buy").upper()
        raw   = o.get("confluences") or "[]"
        try:
            confs = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            confs = []
        key = f"{s} {act}"
        setup.setdefault(key, {"w": 0, "t": 0, "pnl": 0.0, "confs": {}})
        setup[key]["t"] += 1
        setup[key]["pnl"] += float(o.get("pnl_pct") or 0)
        if _is_win(o["result"]):
            setup[key]["w"] += 1
        for c in (confs or []):
            c = str(c).strip()
            if c:
                setup[key]["confs"][c] = setup[key]["confs"].get(c, 0) + 1

    def _setup_row(key: str, d: dict) -> dict:
        top_confs = sorted(d["confs"].items(), key=lambda x: x[1], reverse=True)[:3]
        conf_str  = " + ".join(c for c, _ in top_confs) or "no confluence data"
        wr        = _wr(d["w"], d["t"])
        avg_pnl   = round(d["pnl"] / d["t"], 2) if d["t"] > 0 else 0
        return {
            "setup":    f"{key} — {conf_str}",
            "win_rate": wr,
            "wins":     d["w"],
            "losses":   d["t"] - d["w"],
            "avg_pnl":  avg_pnl,
        }

    rows = [
        _setup_row(k, v) for k, v in setup.items()
        if v["t"] >= 2
    ]
    rows.sort(key=lambda x: x["win_rate"], reverse=True)
    top_setups      = rows[:3]
    losing_patterns = sorted(rows, key=lambda x: x["win_rate"])[:3]

    # ── Suggested thresholds ─────────────────────────────────────────────
    lon_wr = session_wr.get("LONDON OPEN", session_wr.get("LONDON_OPEN", None))
    ny_wr  = session_wr.get("NY OPEN",     session_wr.get("NY_OPEN",
             session_wr.get("NY SESSION",  session_wr.get("NY_SESSION", None))))

    thr_gold = DEFAULT_THRESHOLD_GOLD
    thr_btc  = DEFAULT_THRESHOLD_BTC

    if lon_wr is not None:
        if lon_wr > 0.65:
            thr_gold = 55
            thr_btc  = 53
        elif lon_wr < 0.40:
            thr_gold = 70
            thr_btc  = 68

    if ny_wr is not None:
        if ny_wr < 0.50:
            thr_gold = max(thr_gold, 75)
            thr_btc  = max(thr_btc,  73)
        elif ny_wr > 0.65:
            thr_gold = min(thr_gold, 55)
            thr_btc  = min(thr_btc,  53)

    insights = {
        "ticker":           ticker,
        "trades_analysed":  total,
        "overall_wr":       overall,
        "session_wr":       session_wr,
        "confluence_wr":    confluence_wr,
        "rsi_bucket_wr":    rsi_bucket_wr,
        "top_setups":       top_setups,
        "losing_patterns":  losing_patterns,
        "threshold_gold":   thr_gold,
        "threshold_btc":    thr_btc,
    }

    await save_trade_insight(insights)

    best_session = max(session_wr, key=session_wr.get) if session_wr else "n/a"
    print(
        f"Learning: {ticker} analysis done — "
        f"WR:{overall:.0%}  best_session:{best_session}  "
        f"thr_gold:{thr_gold}  thr_btc:{thr_btc}"
    )
    return insights


# ─── Dynamic Thresholds ────────────────────────────────────────────────────────

async def get_dynamic_threshold(ticker: str, session: str) -> int:
    """
    Returns the confidence threshold for the current ticker and session,
    adjusted by historical performance. Falls back to defaults if no data.
    """
    default = DEFAULT_THRESHOLD_GOLD if "BTC" not in ticker.upper() \
              else DEFAULT_THRESHOLD_BTC
    try:
        # Use ALL insights (covers both GOLD and BTC from same trade base)
        insight = await get_latest_insight(ticker)
        if not insight:
            return default

        session_wr = insight.get("session_wr", {})
        session_up = session.upper()

        # Per-session threshold rules
        for key, wr in session_wr.items():
            if "LONDON" in key and "OPEN" in key:
                if session_up in (key, key.replace("_", " ")):
                    if wr > 0.65:
                        return 55
                    elif wr < 0.40:
                        return 75
            if "NY" in key:
                if session_up in (key, key.replace("_", " ")):
                    if wr < 0.50:
                        return 75
                    elif wr > 0.65:
                        return 55

        # Fall back to stored global threshold
        key = "threshold_gold" if "BTC" not in ticker.upper() else "threshold_btc"
        stored = insight.get(key)
        return int(stored) if stored else default

    except Exception as e:
        print(f"Learning: get_dynamic_threshold error — {e}")
        return default


# ─── Prompt Injection ──────────────────────────────────────────────────────────

async def get_prompt_injection(ticker: str) -> str:
    """
    Returns a text block describing top-3 and worst-3 setups from the last
    20 trades. Empty string if no data yet.
    """
    try:
        insight = await get_latest_insight(ticker)
        if not insight:
            return ""

        total   = insight.get("trades_analysed", 0)
        overall = insight.get("overall_wr", 0)
        tops    = insight.get("top_setups", [])
        losers  = insight.get("losing_patterns", [])
        session_wr = insight.get("session_wr", {})

        if not tops and not losers:
            return ""

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━",
            f"SELF-LEARNED PATTERNS  (last {total} trades — this bot, this market)",
            "━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if tops:
            lines.append("TOP PERFORMING SETUPS — PRIORITISE THESE:")
            for i, s in enumerate(tops[:3], 1):
                wr_pct = int(s["win_rate"] * 100)
                lines.append(
                    f"  {i}. {s['setup']}: "
                    f"{s['wins']}W/{s['losses']}L ({wr_pct}% WR, avg {s['avg_pnl']:+.2f}%)"
                )

        if losers:
            lines.append("AVOID THESE PATTERNS:")
            for i, s in enumerate(losers[:3], 1):
                wr_pct = int(s["win_rate"] * 100)
                lines.append(
                    f"  {i}. {s['setup']}: "
                    f"{s['wins']}W/{s['losses']}L ({wr_pct}% WR, avg {s['avg_pnl']:+.2f}%)"
                )

        if session_wr:
            best  = max(session_wr, key=session_wr.get)
            worst = min(session_wr, key=session_wr.get)
            lines.append(
                f"Sessions: best={best} ({int(session_wr[best]*100)}% WR)  "
                f"worst={worst} ({int(session_wr[worst]*100)}% WR)"
            )

        lines.append(
            f"Overall: {int(overall*100)}% WR from last {total} trades"
        )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    except Exception as e:
        print(f"Learning: get_prompt_injection error — {e}")
        return ""


# ─── Weekly Report Generator ───────────────────────────────────────────────────

async def generate_weekly_report() -> str:
    """
    Monday weekly Telegram report covering GOLD + BTC-USD.
    Pulls from both outcomes table and trade_exits table.
    """
    now        = datetime.datetime.utcnow()
    week_label = (now - datetime.timedelta(days=7)).strftime("%b %d")
    week_end   = now.strftime("%b %d")

    # Aggregate all exits this week (managed exits from trade_manager)
    all_exits = await get_weekly_exits(days=7)
    exits_by_ticker: dict[str, list] = {}
    for ex in all_exits:
        t = ex.get("ticker", "UNKNOWN")
        exits_by_ticker.setdefault(t, []).append(ex)

    lines = [
        f"📈 DUTCHALPHA WEEKLY REPORT",
        f"Week: {week_label}–{week_end}",
        "",
    ]

    for ticker in ("GOLD", "BTC-USD"):
        outcomes = await get_outcomes_for_analysis(ticker, limit=200, days=7)
        exits    = exits_by_ticker.get(ticker, [])

        # Combine both sources for total trade count and P&L
        all_trades = outcomes  # outcomes covers everything (SL + managed exits via learning)
        if not all_trades and not exits:
            lines.append(f"{ticker}: no trades this week")
            continue

        total  = len(all_trades)
        wins   = sum(1 for o in all_trades if _is_win(o["result"]))
        losses = sum(1 for o in all_trades if o["result"] == "sl")
        wr     = _wr(wins, total)

        # P&L from exits (have euro amounts)
        net_euros  = round(sum(float(e.get("pnl_euros") or 0) for e in exits), 2)
        avg_winner = 0.0
        avg_loser  = 0.0
        win_exits  = [e for e in exits if _f(e.get("pnl_euros")) > 0]
        loss_exits = [e for e in exits if _f(e.get("pnl_euros")) <= 0]
        if win_exits:
            avg_winner = round(sum(_f(e["pnl_euros"]) for e in win_exits) / len(win_exits), 2)
        if loss_exits:
            avg_loser  = round(sum(_f(e["pnl_euros"]) for e in loss_exits) / len(loss_exits), 2)

        # Profit factor
        gross_win  = sum(_f(e["pnl_euros"]) for e in win_exits)
        gross_loss = abs(sum(_f(e["pnl_euros"]) for e in loss_exits)) or 0.01
        pf = round(gross_win / gross_loss, 2)

        # Early exits saved
        saved_total = round(sum(
            _f(e.get("saved_vs_sl_pct")) / 100 * _f(e.get("entry_price")) * _f(e.get("size"))
            for e in exits if _f(e.get("saved_vs_sl_pct")) > 0
        ), 2)

        # Session breakdown
        sess: dict[str, dict] = {}
        for o in all_trades:
            s = (o.get("session") or "unknown").upper()
            sess.setdefault(s, {"w": 0, "t": 0})
            sess[s]["t"] += 1
            if _is_win(o["result"]):
                sess[s]["w"] += 1
        sess_wr    = {s: _wr(d["w"], d["t"]) for s, d in sess.items() if d["t"] >= 1}
        best_sess  = max(sess_wr, key=sess_wr.get) if sess_wr else "n/a"
        worst_sess = min(sess_wr, key=sess_wr.get) if sess_wr else "n/a"

        # Top setup from insights
        insight = await get_latest_insight(ticker)
        top_setups      = insight.get("top_setups", [])      if insight else []
        losing_patterns = insight.get("losing_patterns", []) if insight else []

        # Next-week suggestion
        suggestions = []
        if sess_wr.get(worst_sess, 1) < 0.45:
            suggestions.append(f"{worst_sess} underperforming — reduce or skip next week")
        if sess_wr.get(best_sess, 0) > 0.65:
            suggestions.append(f"{best_sess} strong ({int(sess_wr[best_sess]*100)}% WR) — prioritise")
        if top_setups:
            suggestions.append(f"Best setup: {top_setups[0]['setup'][:50]} ({int(top_setups[0]['win_rate']*100)}% WR)")
        if not suggestions:
            suggestions.append("Continue current strategy — no major adjustments needed")

        pnl_sign = "+" if net_euros >= 0 else ""
        lines += [
            f"{'━'*19}",
            f"{ticker}",
            f"{'━'*19}",
            f"Trades: {total} | Wins: {wins} | Losses: {losses}",
            f"Win rate: {int(wr*100)}% | Profit factor: {pf}",
            (f"Net P&L: {pnl_sign}€{net_euros}" if exits else
             f"Net P&L: {wins}W/{losses}L (no euro data yet)"),
            "",
        ]
        if best_sess != "n/a":
            lines.append(f"Best session: {best_sess} ({int(sess_wr.get(best_sess,0)*100)}% WR)")
        if top_setups:
            best_setup = top_setups[0]
            lines.append(f"Best setup: {best_setup['setup'][:40]} ({int(best_setup['win_rate']*100)}% WR)")
        if win_exits or loss_exits:
            lines.append(f"Avg winner: €{avg_winner} | Avg loser: €{avg_loser}")
        if saved_total > 0:
            lines.append(f"Early exits saved: €{saved_total} this week")
        if losing_patterns:
            lines.append(f"Worst pattern: {losing_patterns[0]['setup'][:40]} ({int(losing_patterns[0]['win_rate']*100)}% WR — avoid)")
        lines.append("")
        lines.append("Next week adjustments:")
        for sug in suggestions[:2]:
            lines.append(f"  → {sug}")
        lines.append("")

    lines += [
        "───────────────────",
        "DutchAlpha — AI Trading Bot",
        "───────────────────",
    ]
    return "\n".join(lines)


def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
