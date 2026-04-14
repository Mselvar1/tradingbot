"""
In-memory registry of open trades.

Scanners register here immediately after a trade is placed.
TradeManager reads here to access original signal data, TP levels, and
partial-close tracking.  PositionMonitor checks manager_closed to avoid
double-recording outcomes for intentional exits.
"""

import time


class TradeStore:

    def __init__(self):
        self._trades: dict[str, dict] = {}
        # Deals intentionally closed by trade_manager — position_monitor skips these
        self.manager_closed: set[str] = set()

    def register(self, deal_id: str, signal: dict, trade: dict,
                 entry_narrative: str = "") -> None:
        self._trades[deal_id] = {
            "signal":             signal,         # full Claude signal dict
            "trade":              trade,          # Capital.com trade details
            "partial_closed_pct": 0,              # 0 / 50 / 80 — virtual tracking
            "last_claude_review": 0.0,            # unix timestamp of last Claude review
            "entry_narrative":    entry_narrative,
            "be_moved":           False,          # breakeven set by trade_manager
            "tp1_hit":            False,
            "tp2_hit":            False,
            "opened_at":          time.time(),
        }

    def update(self, deal_id: str, **kwargs) -> None:
        if deal_id in self._trades:
            self._trades[deal_id].update(kwargs)

    def get(self, deal_id: str) -> dict | None:
        return self._trades.get(deal_id)

    def remove(self, deal_id: str) -> None:
        self._trades.pop(deal_id, None)

    def mark_closed(self, deal_id: str) -> None:
        """Tell position_monitor this deal was intentionally closed — skip outcome recording."""
        self.manager_closed.add(deal_id)
        self.remove(deal_id)

    def is_tracked(self, deal_id: str) -> bool:
        return deal_id in self._trades

    def get_all(self) -> dict:
        return dict(self._trades)


trade_store = TradeStore()
