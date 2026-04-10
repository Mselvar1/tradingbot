import aiohttp
from config.settings import settings

BASE_URL_DEMO = "https://demo-api-capital.backend-capital.com/api/v1"
BASE_URL_LIVE = "https://api-capital.backend-capital.com/api/v1"

class CapitalClient:
    def __init__(self):
        self.api_key = settings.capital_api_key
        self.base_url = BASE_URL_DEMO if settings.capital_mode == "demo" else BASE_URL_LIVE
        self.session_token = None
        self.account_token = None

    async def create_session(self) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.post(
                    f"{self.base_url}/session",
                    headers={
                        "X-CAP-API-KEY": self.api_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "identifier": settings.capital_email,
                        "password": settings.capital_password,
                        "encryptedPassword": False
                    }
                )
                if r.status == 200:
                    self.session_token = r.headers.get("CST")
                    self.account_token = r.headers.get("X-SECURITY-TOKEN")
                    print(f"Capital.com session created ({settings.capital_mode} mode)")
                    return True
                else:
                    text = await r.text()
                    print(f"Capital.com session failed: {r.status} — {text[:100]}")
                    return False
        except Exception as e:
            print(f"Capital.com connection error: {e}")
            return False

    def get_headers(self) -> dict:
        return {
            "X-CAP-API-KEY": self.api_key,
            "CST": self.session_token or "",
            "X-SECURITY-TOKEN": self.account_token or "",
            "Content-Type": "application/json"
        }

    async def get_price(self, epic: str) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/markets/{epic}",
                    headers=self.get_headers()
                )
                d = await r.json()
                instrument = d.get("instrument", {})
                snapshot = d.get("snapshot", {})
                bid = snapshot.get("bid", 0)
                offer = snapshot.get("offer", 0)
                price = (bid + offer) / 2 if bid and offer else 0
                return {
                    "epic": epic,
                    "price": round(price, 5),
                    "bid": bid,
                    "offer": offer,
                    "high": snapshot.get("high", 0),
                    "low": snapshot.get("low", 0),
                    "change_pct": snapshot.get("percentageChange", 0),
                    "name": instrument.get("name", epic)
                }
        except Exception as e:
            return {"epic": epic, "price": 0, "error": str(e)}

    async def get_candles(self, epic: str, resolution: str = "MINUTE_5",
                          max_candles: int = 50) -> list:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/prices/{epic}",
                    headers=self.get_headers(),
                    params={"resolution": resolution, "max": max_candles}
                )
                d = await r.json()
                prices = d.get("prices", [])
                closes = []
                for p in prices:
                    close = p.get("closePrice", {})
                    mid = (close.get("bid", 0) + close.get("ask", 0)) / 2
                    closes.append(mid)
                return closes
        except Exception as e:
            print(f"Capital candles error: {e}")
            return []

    async def get_account_balance(self) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/accounts",
                    headers=self.get_headers()
                )
                d = await r.json()
                accounts = d.get("accounts", [])
                if accounts:
                    balance = accounts[0].get("balance", {})
                    return {
                        "balance": balance.get("balance", 0),
                        "available": balance.get("available", 0),
                        "profit_loss": balance.get("pnl", 0),
                        "currency": accounts[0].get("preferred", "EUR"),
                        "mode": settings.capital_mode
                    }
                return {"balance": 0, "available": 0}
        except Exception as e:
            return {"balance": 0, "error": str(e)}

    async def get_positions(self) -> list:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/positions",
                    headers=self.get_headers()
                )
                d = await r.json()
                positions = []
                for p in d.get("positions", []):
                    pos = p.get("position", {})
                    market = p.get("market", {})
                    positions.append({
                        "deal_id": pos.get("dealId"),
                        "epic": market.get("epic"),
                        "name": market.get("instrumentName"),
                        "direction": pos.get("direction"),
                        "size": pos.get("size"),
                        "entry_price": pos.get("openLevel"),
                        "current_price": market.get("bid"),
                        "pnl": pos.get("upl"),
                        "stop_loss": pos.get("stopLevel"),
                        "take_profit": pos.get("profitLevel")
                    })
                return positions
        except Exception as e:
            return []

    async def place_order(self, epic: str, direction: str,
                          size: float, stop_loss: float,
                          take_profit: float) -> dict:
        try:
            payload = {
                "epic": epic,
                "direction": direction.upper(),
                "size": size,
                "guaranteedStop": False,
                "stopLevel": stop_loss,
                "profitLevel": take_profit
            }
            async with aiohttp.ClientSession() as s:
                r = await s.post(
                    f"{self.base_url}/positions",
                    headers=self.get_headers(),
                    json=payload
                )
                d = await r.json()
                return {
                    "status": "success" if r.status == 200 else "error",
                    "deal_id": d.get("dealReference"),
                    "response": d
                }
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    async def close_position(self, deal_id: str) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.delete(
                    f"{self.base_url}/positions/{deal_id}",
                    headers=self.get_headers()
                )
                d = await r.json()
                return {
                    "status": "success" if r.status == 200 else "error",
                    "response": d
                }
        except Exception as e:
            return {"status": "error", "reason": str(e)}

capital_client = CapitalClient()