from datetime import datetime

class SignalHistory:
    def __init__(self):
        self.signals = []

    def save(self, signal: dict):
        record = {
            "id": len(self.signals) + 1,
            "ticker": signal["ticker"],
            "action": signal["action"],
            "confidence": signal["confidence"],
            "price_at_signal": signal["price"],
            "entry": signal["entry"],
            "stop_loss": signal["stop_loss"],
            "tp1": signal["tp1"],
            "tp2": signal["tp2"],
            "rr": signal["rr"],
            "timeframe": signal["timeframe"],
            "summary": signal["summary"],
            "sent_at": datetime.utcnow().isoformat(),
            "outcome": "pending"
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