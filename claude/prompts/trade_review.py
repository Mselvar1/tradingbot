TRADE_REVIEW_PROMPT = """
You are managing an active trade. Analyse the current state and return ONE management decision.
Return ONLY valid JSON — no text outside the JSON object.

ORIGINAL SIGNAL:
Ticker: {ticker}
Direction: {direction}
Entry price: {entry}
Stop Loss: {stop_loss}
TP1: {tp1}
TP2: {tp2}
Confluences at entry: {confluences}
Original thesis: {thesis}

CURRENT STATE:
Current price: {current_price}
Time in trade: {hold_minutes} min
Current P&L: {pnl_pct:+.3f}%
RSI: {rsi}
Momentum: {momentum}
RSI divergence: {rsi_divergence}
Consecutive candles against trade direction: {consec_against}
CHoCH signal: {choch_detected}
Live price narrative: {price_narrative}

DECISION RULES (apply in order of priority):
1. EXIT_NOW if: CHoCH detected, OR 3+ consecutive candles against direction, OR RSI diverging against with weak momentum
2. TAKE_PARTIAL_PROFIT if: price reached TP1 and not yet done
3. MOVE_STOP_TO_BREAKEVEN if: price is 50%+ of the way from entry to TP1, no reversal signals present
4. EXIT_NOW if: trade open > 2 hours with P&L between -0.1% and +0.1% (no progress)
5. EXIT_NOW if: momentum has fully reversed against trade direction
6. HOLD if: trade is progressing, momentum confirming, no reversal signals

{{
  "decision": "HOLD or TAKE_PARTIAL_PROFIT or MOVE_STOP_TO_BREAKEVEN or EXIT_NOW",
  "reason": "one short sentence explaining decision",
  "urgency": "immediate or normal",
  "new_stop": price_number_or_null
}}
"""
