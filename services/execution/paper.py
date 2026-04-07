from services.data.prices import get_price
from datetime import datetime

class PaperBroker:
    def __init__(self):
        self.positions = {}
        self.balance = 10000.0

    async def buy(self, ticker: str, amount_usd: float) -> dict:
        pd = await get_price(ticker)
        price = pd["price"]
        if price == 0:
            return {"status": "error", "reason": "price unavailable"}
        if amount_usd > self.balance:
            return {"status": "error", "reason": "insufficient balance"}
        qty = amount_usd / price
        self.positions[ticker] = {
            "ticker": ticker, "qty": qty,
            "entry_price": price,
            "opened_at": datetime.utcnow().isoformat()
        }
        self.balance -= amount_usd
        return {"status": "filled", "price": price, "qty": round(qty, 4)}

    async def sell(self, ticker: str) -> dict:
        if ticker not in self.positions:
            return {"status": "error", "reason": "no position"}
        pos = self.positions.pop(ticker)
        pd = await get_price(ticker)
        price = pd["price"]
        pnl = (price - pos["entry_price"]) * pos["qty"]
        self.balance += price * pos["qty"]
        return {"status": "filled", "price": price, "pnl": round(pnl, 2)}

    async def get_positions(self) -> list:
        result = []
        for t, p in self.positions.items():
            pd = await get_price(t)
            cur = pd["price"]
            pnl = (cur - p["entry_price"]) * p["qty"]
            result.append({**p, "current_price": cur, "pnl": round(pnl, 2)})
        return result

    def get_balance(self) -> float:
        return round(self.balance, 2)

broker = PaperBroker()