from datetime import datetime, date

class RiskEngine:
    def __init__(self):
        self.max_risk_per_trade = 0.02      # 2% of portfolio per trade
        self.max_daily_loss = 0.05          # 5% max daily loss
        self.max_position_size = 0.10       # 10% of portfolio per position
        self.max_open_positions = 5         # max 5 positions at once
        self.daily_loss = 0.0               # tracks today's loss
        self.daily_loss_date = date.today()
        self.kill_switch = False            # stops all trading when True
        self.portfolio_value = 10000.0      # starting portfolio value

    def reset_daily_if_needed(self):
        if date.today() != self.daily_loss_date:
            self.daily_loss = 0.0
            self.daily_loss_date = date.today()

    def check_trade(self, ticker: str, amount_usd: float,
                    open_positions: list) -> dict:
        self.reset_daily_if_needed()

        if self.kill_switch:
            return {
                "approved": False,
                "reason": "Kill switch is active. Trading halted."
            }

        if len(open_positions) >= self.max_open_positions:
            return {
                "approved": False,
                "reason": f"Max {self.max_open_positions} open positions reached."
            }

        max_amount = self.portfolio_value * self.max_position_size
        if amount_usd > max_amount:
            return {
                "approved": False,
                "reason": f"Position too large. Max allowed: ${max_amount:.2f}"
            }

        daily_loss_limit = self.portfolio_value * self.max_daily_loss
        if self.daily_loss >= daily_loss_limit:
            return {
                "approved": False,
                "reason": f"Daily loss limit hit (${self.daily_loss:.2f}). Trading paused."
            }

        return {"approved": True, "reason": "Risk check passed."}

    def record_loss(self, amount: float):
        self.reset_daily_if_needed()
        if amount < 0:
            self.daily_loss += abs(amount)

    def check_stop_loss(self, positions: list) -> list:
        alerts = []
        for p in positions:
            if p["entry_price"] == 0:
                continue
            pnl_pct = (p["current_price"] - p["entry_price"]) / p["entry_price"]
            if pnl_pct <= -self.max_risk_per_trade:
                alerts.append({
                    "ticker": p["ticker"],
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "pnl_usd": p["pnl"]
                })
        return alerts

    def activate_kill_switch(self):
        self.kill_switch = True

    def deactivate_kill_switch(self):
        self.kill_switch = False

    def get_status(self) -> dict:
        self.reset_daily_if_needed()
        return {
            "kill_switch": self.kill_switch,
            "daily_loss": round(self.daily_loss, 2),
            "daily_loss_limit": round(self.portfolio_value * self.max_daily_loss, 2),
            "max_position_size": round(self.portfolio_value * self.max_position_size, 2),
            "max_open_positions": self.max_open_positions
        }

risk = RiskEngine()