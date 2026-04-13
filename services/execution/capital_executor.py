from services.data.capital import capital_client
from services.data.capital_epics import get_epic
from services.risk import risk
from config.settings import settings
from datetime import datetime

MAX_RISK_PER_TRADE_PCT = 0.02   # 2% of account balance risked per trade
MAX_OPEN_TRADES = 3
MIN_RR = 1.8


class CapitalExecutor:
    def __init__(self):
        self.open_trades = {}
        self.daily_pnl = 0.0
        self.daily_loss_limit_pct = 0.05
        self.trade_log = []

    async def get_account(self) -> dict:
        return await capital_client.get_account_balance()

    async def can_trade(self) -> dict:
        if risk.kill_switch:
            return {"allowed": False, "reason": "Kill switch is active. Trading halted."}
        if not settings.capital_api_key:
            return {"allowed": False, "reason": "No Capital.com API key"}
        account = await self.get_account()
        balance = account.get("balance", 0)
        if balance == 0:
            return {"allowed": False, "reason": "Zero balance"}
        if len(self.open_trades) >= MAX_OPEN_TRADES:
            return {"allowed": False,
                    "reason": f"Max {MAX_OPEN_TRADES} open trades reached"}
        daily_loss_limit = balance * self.daily_loss_limit_pct
        if self.daily_pnl <= -daily_loss_limit:
            return {"allowed": False,
                    "reason": f"Daily loss limit hit: {self.daily_pnl:.2f}"}
        return {"allowed": True, "balance": balance}

    def calculate_size(self, balance: float, entry_price: float,
                       stop_distance_pct: float) -> float:
        """
        Correct risk-based position sizing.

        size = risk_amount_usd / stop_loss_usd_per_unit

        Example — BTC at $84,000, $1,000 balance, 2% risk, 0.3% stop:
          risk_amount   = $1,000 × 0.02 = $20
          stop_per_unit = $84,000 × 0.003 = $252
          size          = $20 / $252 = 0.079 BTC  ✓

        Example — Gold at $4,700, $1,000 balance, 2% risk, 0.5% stop:
          risk_amount   = $20
          stop_per_unit = $4,700 × 0.005 = $23.50
          size          = $20 / $23.50 = 0.851 oz  ✓
        """
        if stop_distance_pct <= 0 or entry_price <= 0:
            return 0.01
        risk_amount   = balance * MAX_RISK_PER_TRADE_PCT
        stop_per_unit = entry_price * (stop_distance_pct / 100)
        size = risk_amount / stop_per_unit
        return round(max(0.01, size), 3)

    async def place_trade(self, signal: dict) -> dict:
        check = await self.can_trade()
        if not check["allowed"]:
            return {"status": "blocked", "reason": check["reason"]}

        balance = check["balance"]
        ticker = signal["ticker"]
        epic = get_epic(ticker)
        action = signal["action"]
        entry = signal.get("entry", [0, 0])
        stop_loss = signal.get("stop_loss", 0)
        tp1 = signal.get("tp1", 0)
        rr = signal.get("rr", 0)

        # rr can arrive as a string ("n/a") from Claude — handle safely
        try:
            rr_val = float(rr)
        except (TypeError, ValueError):
            rr_val = 0.0

        if rr_val < MIN_RR:
            return {"status": "blocked",
                    "reason": f"R:R {rr} below minimum {MIN_RR}"}

        entry_price = entry[0] if isinstance(entry, list) else entry
        try:
            entry_price = float(entry_price)
            stop_loss   = float(stop_loss)
            tp1         = float(tp1)
        except (TypeError, ValueError):
            return {"status": "blocked", "reason": "Invalid entry/stop/tp levels"}

        if entry_price == 0 or stop_loss == 0 or tp1 == 0:
            return {"status": "blocked", "reason": "Invalid entry/stop/tp levels"}

        stop_distance_pct = abs(entry_price - stop_loss) / entry_price * 100
        size = self.calculate_size(balance, entry_price, stop_distance_pct)

        await capital_client.ensure_session()

        direction = "BUY" if action == "buy" else "SELL"
        result = await capital_client.place_order(
            epic=epic,
            direction=direction,
            size=size,
            stop_loss=stop_loss,
            take_profit=tp1
        )

        if result["status"] == "success":
            trade = {
                "deal_id": result["deal_id"],
                "ticker": ticker,
                "epic": epic,
                "direction": direction,
                "size": size,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": tp1,
                "opened_at": datetime.utcnow().isoformat(),
                "balance_at_open": balance
            }
            self.open_trades[result["deal_id"]] = trade
            self.trade_log.append(trade)
            return {"status": "success", "trade": trade}
        else:
            return {"status": "error", "reason": result}

    def record_pnl(self, pnl: float):
        self.daily_pnl += pnl

    def get_stats(self) -> dict:
        return {
            "open_trades": len(self.open_trades),
            "daily_pnl": round(self.daily_pnl, 2),
            "total_trades": len(self.trade_log)
        }


executor = CapitalExecutor()
