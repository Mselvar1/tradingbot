from datetime import datetime

class SignalHistory:
    def __init__(self):
        self.signals = []

    def save(self, signal: dict):
        record = {
            "id":             len(self.signals) + 1,
            "ticker":         signal.get("ticker", ""),
            "action":         signal.get("action", ""),
            "confidence":     signal.get("confidence", 0),
            "price_at_signal": signal.get("price", 0),
            "entry":          signal.get("entry", []),
            "stop_loss":      signal.get("stop_loss", 0),
            "tp1":            signal.get("tp1", 0),
            "tp2":            signal.get("tp2", 0),
            "rr":             signal.get("rr", 0),
            "timeframe":      signal.get("timeframe", ""),
            # Gold uses "summary", BTC uses "analysis_summary"
            "summary":        signal.get("summary") or signal.get("analysis_summary", ""),
            "sent_at":        datetime.utcnow().isoformat(),
            "outcome":        "pending"
        }
        self.signals.append(record)
        return record["id"]

    def get_all(self) -> list:
        return self.signals

    def get_recent(self, n: int = 5) -> list:
        return self.signals[-n:]

    def mark_outcome(self, signal_id: int, outcome: str, pnl: float = 0):
        for s in self.signals:
            if s["id"] == signal_id:
                s["outcome"] = outcome
                s["pnl"] = pnl
                s["closed_at"] = datetime.utcnow().isoformat()
                return True
        return False

    def get_stats(self) -> dict:
        closed = [s for s in self.signals if s["outcome"] != "pending"]
        if not closed:
            return {
                "total_signals": len(self.signals),
                "closed": 0,
                "pending": len(self.signals),
                "win_rate": 0,
                "avg_pnl": 0
            }
        wins = [s for s in closed if s["outcome"] == "win"]
        total_pnl = sum(s.get("pnl", 0) for s in closed)
        return {
            "total_signals": len(self.signals),
            "closed": len(closed),
            "pending": len(self.signals) - len(closed),
            "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate": round(len(wins) / len(closed) * 100, 1),
            "avg_pnl": round(total_pnl / len(closed), 2),
            "total_pnl": round(total_pnl, 2)
        }

history = SignalHistory()